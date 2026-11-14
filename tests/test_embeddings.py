"""Regression tests for extraction, vector-cache integrity, and score provenance."""
import json
import math
import sqlite3

import pytest

from dic.embeddings import (compute, connect, cosine_distance, definitions, ensure_vectors,
                               inline_gloss, load_scores, lookup_term, normalized_blob, unpack)
from dic.site import build
from test_pipeline import make_dump, ro
from dic.database import import_dump
from dic.parser import extract_origins


class FakeEmbedder:
    digest = 'model-version-1'

    def __init__(self, fail_after=None):
        self.calls = []
        self.fail_after = fail_after

    def embed(self, texts):
        if self.fail_after is not None and len(self.calls) >= self.fail_after:
            raise RuntimeError('simulated network failure')
        self.calls.append(list(texts))
        return [[1.0, float(len(text) % 7 + 1), 2.0] for text in texts]


def test_cosine_distance_is_normalized_and_uses_float32():
    first = unpack(normalized_blob([3, 4, 0]))
    assert len(normalized_blob([3, 4, 0])) == 12
    assert cosine_distance(first, first) == pytest.approx(0, abs=1e-6)
    assert cosine_distance(unpack(normalized_blob([1, 0])), unpack(normalized_blob([0, 9]))) == 1
    assert cosine_distance(unpack(normalized_blob([1, 0])), unpack(normalized_blob([-5, 0]))) == 2
    for vector in ([], [0, 0], [math.nan, 1], [math.inf, 1]):
        with pytest.raises(ValueError):
            normalized_blob(vector)


def test_interrupted_embeddings_resume_without_repeating_committed_batches(tmp_path):
    db = connect(tmp_path / 'vectors.sqlite')
    failing = FakeEmbedder(fail_after=1)
    with pytest.raises(RuntimeError, match='network'):
        ensure_vectors(db, failing, ['a', 'b', 'c'], batch_size=2)
    assert db.execute('SELECT count(*) FROM vectors').fetchone()[0] == 2
    resumed = FakeEmbedder()
    ensure_vectors(db, resumed, ['a', 'b', 'c', 'a'], batch_size=2)
    assert resumed.calls == [['c']]
    # Changing the model digest must not reuse embeddings from a different model.
    changed = FakeEmbedder()
    changed.digest = 'model-version-2'
    ensure_vectors(db, changed, ['a'], batch_size=2)
    assert changed.calls == [['a']]
    db.close()


def test_invalid_dimensions_and_nonfinite_batches_are_not_cached(tmp_path):
    db = connect(tmp_path / 'vectors.sqlite')
    ensure_vectors(db, FakeEmbedder(), ['a'])
    class WrongDimensions(FakeEmbedder):
        def embed(self, texts):
            return [[1, 0] for _ in texts]
    with pytest.raises(ValueError, match='dimensions'):
        ensure_vectors(db, WrongDimensions(), ['b'])
    assert db.execute('SELECT count(*) FROM vectors').fetchone()[0] == 1
    class NonFinite(FakeEmbedder):
        def embed(self, texts):
            return [[1, math.nan, 0] for _ in texts]
    with pytest.raises(ValueError, match='non-finite'):
        ensure_vectors(db, NonFinite(), ['b'])
    assert db.execute('SELECT count(*) FROM vectors').fetchone()[0] == 1
    db.close()


def test_motor_comparison_does_not_hide_parallel_german_donor():
    origins = extract_origins("Din franceză ''[[moteur]]'' < latină ''[[motor]]'', germană ''[[Motor]]''.")
    assert {(o.language, o.ancestor, o.direct) for o in origins} == {
        ('fr', 'moteur', True), ('la', 'motor', False), ('de', 'Motor', True)}


def test_definitions_exclude_examples_inflections_and_ancestors():
    source = "{{-etimologie-}}\nDin latină ''[[conventum]]''.\n# {{plural of|ro|cuvânt}}\n# [[unitate]] a [[vocabular]]ului\n#: An example\n#* Another example\n{{-etimologie-}}\n# second homograph"
    assert definitions(source) == {1: ['unitate a vocabularului'], 2: ['second homograph']}
    assert lookup_term('mŏnŭmentum', 'la') == 'monumentum'
    assert lookup_term('casă', 'ro') == 'casă'
    raw = "Din latină ''[[other]]'', ''[[conventum]]'' („[[înțelegere]]”)."
    assert inline_gloss(raw, 'conventum', 'la') == 'înțelegere'
    assert inline_gloss(raw, 'other', 'la') is None


def test_embedding_pipeline_missing_definitions_stale_scores_and_static_export(tmp_path):
    dump = make_dump(tmp_path / 'dump.xml.bz2', [
        ('casă', ro("{{-etimologie-}}\nDin latină ''[[casa]]'' („[[locuință]]”).\n# [[clădire]] pentru locuit"), 0),
        ('actor', ro("{{-etimologie-}}\nDin franceză ''[[acteur]]''.\n# persoană care joacă teatru"), 0),
        ('casa', '=={{limba|ita}}==\n# casa in Italian must not supply a Latin gloss', 0),
        ('missing', ro('# no origin recorded'), 0),
    ])
    database = tmp_path / 'dictionary.sqlite'
    import_dump(dump, database)
    store = tmp_path / 'vectors.sqlite'
    client = FakeEmbedder()
    result = compute(database, store, dump, client=client, batch_size=2)
    assert result['dictionary_words'] == 3
    assert result['pairs'] == 2
    assert result['definition_pairs'] == 1
    # Every word is embedded, even words with no etymon.
    assert 'missing' in {text for batch in client.calls for text in batch}
    with sqlite3.connect(store) as db:
        assert db.execute("SELECT definition_distance FROM pair_scores WHERE word='actor'").fetchone()[0] is None
        assert db.execute("SELECT ancestor_definition FROM pair_scores WHERE word='casă'").fetchone()[0] == 'locuință'
    no_network = FakeEmbedder(fail_after=0)
    compute(database, store, dump, client=no_network)
    assert no_network.calls == []
    _, scores = load_scores(store, result['dataset_sha'])
    assert len(scores) == 2
    assert load_scores(store, 'another-dictionary-snapshot') == (None, {})
    html = build(database, tmp_path / 'site', store).read_text()
    data = json.loads(html.split('<script id="dic-data" type="application/json">')[1].split('</script>')[0])
    with sqlite3.connect(tmp_path / 'site' / 'dic.sqlite') as published:
        score = published.execute('SELECT word_distance,definition_distance FROM origin_scores WHERE definition_distance IS NOT NULL').fetchone()
        assert score is not None
        assert score[0] is not None and score[1] is not None
    # No browser request to the private Ollama service or vectors is required.
    assert 'endpoint' not in data['metadata']['embeddings']


def test_browser_ranking_regressions():
    import shutil
    import subprocess
    from pathlib import Path
    node = shutil.which('node')
    if not node:
        pytest.skip('Node is optional; install it to test the browser ranking rules')
    subprocess.run([node, str(Path(__file__).with_name('ranking.test.mjs'))], check=True, capture_output=True, text=True)


def test_mentioning_forma_inside_a_definition_does_not_make_it_an_inflection():
    assert definitions("# [[totalitate]]a oamenilor care locuiesc împreună ([[forma|formând]] o familie).") == {
        0: ['totalitatea oamenilor care locuiesc împreună (formând o familie).']}


def test_a_real_definition_starting_with_forma_is_not_an_inflection():
    assert definitions('# formă de relief înaltă') == {0: ['formă de relief înaltă']}


def test_definition_scores_keep_homograph_senses_and_source_languages_separate(tmp_path):
    dump = make_dump(tmp_path / 'homographs.xml.bz2', [
        ('casă', ro("{{-etimologie-}}\nDin latină ''[[casa]]''.\n# clădire pentru locuit\n{{-etimologie-}}\nDin italiană ''[[casa]]''.\n# casierie"), 0),
        ('casa', '=={{limba|ita}}==\n# italian dictionary meaning', 0),
    ])
    database, store = tmp_path / 'dictionary.sqlite', tmp_path / 'vectors.sqlite'
    import_dump(dump, database)
    compute(database, store, dump, client=FakeEmbedder())
    with sqlite3.connect(store) as db:
        rows = db.execute('SELECT sense,word_definition,ancestor_definition,definition_distance FROM pair_scores ORDER BY sense').fetchall()
    assert rows[0] == (1, 'clădire pentru locuit', None, None)
    assert rows[1][:3] == (2, 'casierie', 'italian dictionary meaning')
    assert rows[1][3] is not None


def test_recorded_bge_m3_distances_identify_large_changes_in_requested_pairs():
    from pathlib import Path
    reference = json.loads(Path(__file__).with_name('fixtures').joinpath('bge_m3_reference.json').read_text())
    assert reference['representation'] == 'bare-word'
    vectors = reference['vectors']
    unchanged = cosine_distance(vectors['casă'],vectors['casa'])
    word_change = cosine_distance(vectors['cuvânt'],vectors['conventum'])
    elder_change = cosine_distance(vectors['bătrân'],vectors['veteranus'])
    assert word_change > 0.45 and word_change > unchanged + 0.25
    assert elder_change > 0.35 and elder_change > unchanged + 0.20
    # The limitation is explicit: language metadata does not alter identical
    # bare-string inputs. Attested definitions are the separate semantic metric.
    assert cosine_distance(vectors['casa'],vectors['casa']) == pytest.approx(0,abs=1e-6)
