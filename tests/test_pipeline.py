import bz2
import hashlib
import json
import sqlite3
from pathlib import Path
from unittest.mock import patch
from xml.sax.saxutils import escape

import pytest

from dic.database import import_dump
from dic.dumps import download
from dic.parser import extract_origins, parse_entry
from dic.site import PROJECT_NAME, build


def ro(body):
    return '=={{limba|ron}}==\n' + body


def test_template_inheritance_scoped_to_romanian_etymology():
    entry = parse_entry('casă', '==English==\n===Etymology===\n{{inh|en|la|wrong}}\n==Romanian==\n===Etymology 1===\n{{inh|ro|la|casa}}\n====Noun====\n# house\n===Related terms===\n{{inh|ro|la|also-wrong}}\n==Italian==\n===Etymology===\n{{inh|it|la|wrong}}')
    assert [(o.language, o.ancestor, o.relation) for _, o in entry.origins] == [('la', 'casa', 'inherited')]


def test_romanian_template_is_not_automatically_inherited():
    entry = parse_entry('casă', ro('{{-etimologie-}}\n{{etimologie|lat|casa}}\n{{-substantiv-|ron}}\n# house'))
    assert entry.origins[0][1].relation == 'etymology-template'
    assert entry.origins[0][1].language == 'la'


def test_parallel_donors_and_remote_ancestor():
    parallel = extract_origins('From {{bor|ro|fr|acteur}} and {{bor|ro|de|Akteur}}.')
    assert all(o.direct for o in parallel)
    chain = extract_origins('From {{bor|ro|fr|acteur}}, from {{der|ro|la|actor}}.')
    assert [o.direct for o in chain] == [True, False]


def test_prose_separates_immediate_donor_and_comparisons():
    entry = parse_entry('actor', ro("{{-etimologie-}}\nDin franceză ''[[acteur]]'' < latină ''[[actor]]''. Înrudit cu italiană ''[[attore]]''.\n{{-substantiv-|ron}}\n# actor"))
    assert [(o.language, o.ancestor, o.relation) for _, o in entry.origins if o.direct] == [('fr', 'acteur', 'prose')]
    assert [(o.language, o.ancestor) for _, o in entry.origins if not o.direct] == [('la', 'actor')]
    assert not extract_origins("Probabil din latină ''[[casa]]''.")
    assert not extract_origins("Din latină probabil ''[[casa]]''.")


def test_multiple_homograph_etymologies_and_inflection_table():
    entry = parse_entry('casă', ro("{{-etimologie-}}\nDin latină ''[[casa]]''.\n{{-substantiv-|ron}}\n{{substantiv-ron|nom-sg=casă|nom-pl=case|dat-sg=casei}}\n# house\n{{-etimologie-}}\nDin italiană ''[[cassa]]'', germană ''[[Kasse]]''.\n{{-substantiv-|ron}}\n# cash desk"))
    assert len(entry.etymologies) == 2
    assert {(sense, o.language, o.ancestor) for sense, o in entry.origins} == {(1, 'la', 'casa'), (2, 'it', 'cassa'), (2, 'de', 'Kasse')}
    assert entry.forms == {('case', 'nom-pl'), ('casei', 'dat-sg')}


def test_explicit_form_pages_and_homographs():
    form = parse_entry('case', ro("{{-substantiv-}}\n#''forma de plural pentru'' [[casă]]."))
    assert form.form_of == {'casă'}
    assert not form.lemma
    form = parse_entry('case', '==Romanian==\n===Noun===\n# {{plural of|ro|casă}}')
    assert form.form_of == {'casă'} and not form.lemma
    homograph = parse_entry('case', ro("#''forma de plural pentru'' [[casă]].\n# independent meaning"))
    assert homograph.lemma
    assert parse_entry('casa', '==Italian==\n# house') is None


def make_dump(path, items):
    xml = '<mediawiki xmlns="http://www.mediawiki.org/xml/export-0.11/">'
    for title, text, ns in items:
        xml += f'<page><title>{escape(title)}</title><ns>{ns}</ns><revision><id>123</id><timestamp>2026-11-01T00:00:00Z</timestamp><text>{escape(text)}</text></revision></page>'
    path.write_bytes(bz2.compress((xml + '</mediawiki>').encode()))
    return path


def test_full_import_export_grouping_and_provenance(tmp_path):
    dump = make_dump(tmp_path / 'test.xml.bz2', [
        ('casă', '==Romanian==\n===Etymology===\n{{inh|ro|la|casa}}\n===Noun===\n# house', 0),
        ('case', '==Romanian==\n===Noun===\n# {{plural of|ro|casă}}', 0),
        ('casele', '==Romanian==\n===Noun===\n# {{inflection of|ro|case||def}}', 0),
        ('unknown', ro('{{-etimologie-}}\nOrigine necunoscută.\n# word'), 0),
        ('hostile', ro('{{-etimologie-}}\n</script><script>alert(1)</script>\n# word'), 0),
        ('Template:ignore', ro('# word'), 10),
        ('other', '==English==\n# word', 0),
    ])
    database = tmp_path / 'words.sqlite'
    meta = import_dump(dump, database, 'en')
    assert meta['entries'] == 5 and meta['lemmas'] == 3
    assert meta['grouped_forms'] == 2
    with sqlite3.connect(database) as db:
        assert db.execute('SELECT Romanian_word, Latin_ancestor FROM latin_inheritance').fetchall() == [('casă', 'casa')]
        assert db.execute('SELECT form FROM forms ORDER BY form').fetchall() == [('case',), ('casele',)]
        assert db.execute('SELECT raw FROM etymologies WHERE raw LIKE ?', ('%Origine%',)).fetchone()
        assert db.execute('PRAGMA foreign_key_check').fetchall() == []
    output = build(database, tmp_path / 'site')
    html = output.read_text()
    assert f'<title>{PROJECT_NAME.capitalize()} · An atlas of Romanian words</title>' in html
    assert f'<div class="brand">{PROJECT_NAME}<span>.</span></div>' in html
    assert f'<span>{PROJECT_NAME.upper()} · Romanian etymology explorer</span>' in html
    payload = html.split('<script id="dic-data" type="application/json">')[1].split('</script>')[0]
    data = json.loads(payload)
    assert data['database'] == 'dic.sqlite'
    assert '<script>alert(1)' not in payload
    with sqlite3.connect(tmp_path / 'site' / 'dic.sqlite') as published:
        assert published.execute('SELECT count(*) FROM entries WHERE is_lemma=1').fetchone()[0] == 3
        assert published.execute('SELECT form FROM forms ORDER BY form').fetchall() == [('case',), ('casele',)]
        assert published.execute("SELECT raw FROM etymologies WHERE raw LIKE '%alert(1)%'").fetchone()
    assert (tmp_path / 'site' / 'vendor' / 'sql-wasm.wasm').is_file()
    assert '__DIC_DATA__' not in html
    # Invalid replacements must preserve the existing usable database.
    dump.write_bytes(b'not bzip2')
    with pytest.raises(OSError):
        import_dump(dump, database)
    with sqlite3.connect(database) as db:
        assert db.execute('SELECT count(*) FROM entries').fetchone()[0] == 5


def test_downloader_checks_cache_and_refuses_corrupt_replacement(tmp_path):
    content = b'valid dump'
    digest = hashlib.sha1(content).hexdigest()
    name = 'rowiktionary-20261101-pages-articles.xml.bz2'
    calls = []
    def fetch(url, destination):
        calls.append(url)
        destination.write_bytes(f'{digest}  {name}\n'.encode() if 'sha1sums' in url else content)
    with patch('dic.dumps.fetch', fetch):
        target = download(tmp_path)
        download(tmp_path)
    assert target.read_bytes() == content
    assert sum(url.endswith('.bz2') for url in calls) == 1
    assert json.loads(target.with_suffix('.bz2.json').read_text())['sha1'] == digest
    assert '/20261101/' in calls[1]
    def corrupt(url, destination):
        destination.write_bytes(f'{digest}  {name}\n'.encode() if 'sha1sums' in url else b'corrupt')
    with patch('dic.dumps.fetch', corrupt), pytest.raises(ValueError, match='checksum mismatch'):
        download(tmp_path, force=True)
    assert target.read_bytes() == content


def test_mormant_does_not_treat_comparisons_as_latin_ancestors():
    entry = parse_entry('mormânt', ro("{{-etimologie-}}\nDin latină ''[[monumentum|mŏnŭmentum]]'' sau ''[[monimentum|mŏnĭmentum]]''; ''[[r]]'', prin disimilare, confer ''[[morimentu]]''.\n# grave"))
    assert {o.ancestor for _, o in entry.origins} == {'monumentum', 'monimentum'}


def test_pamant_keeps_immediate_and_deeper_latin_ancestors_separate():
    entry = parse_entry('pământ', ro("{{-etimologie-}}\nProvine din latină ''[[paumentum]]'' < ''[[pavimentum]]''.\n# earth"))
    assert [(o.ancestor, o.direct) for _, o in entry.origins] == [('paumentum', True), ('pavimentum', False)]


def test_alternative_and_archaic_forms_are_grouped():
    alternative = parse_entry('april', ro("#''formă alternativă pentru'' '''[[aprilie]]'''."))
    assert alternative.form_of == {'aprilie'}
    assert not alternative.lemma
    archaic = parse_entry('cane', ro("#''formă arhaică pentru'' '''[[câine]]'''."))
    assert not archaic.lemma


LATIN_REGRESSIONS = json.loads(Path(__file__).with_name('fixtures').joinpath('latin_regressions.json').read_text())


@pytest.mark.parametrize('case', LATIN_REGRESSIONS, ids=[case['queries'][0] for case in LATIN_REGRESSIONS])
def test_requested_words_have_their_recorded_latin_ancestors(case):
    entry = parse_entry(case['word'], ro('{{-etimologie-}}\n' + case['etymology'] + '\n{{-substantiv-|ron}}\n# dictionary meaning'))
    assert entry is not None and entry.lemma
    assert {(origin.language, origin.ancestor, origin.direct) for _, origin in entry.origins} == {
        ('la', origin['ancestor'], origin['direct']) for origin in case['origins']}
    # Older spellings must not change what the same source etymology asserts.
    for query in case['queries']:
        if query == 'conventum':
            continue  # A Latin ancestor, not a Romanian headword.
        variant = parse_entry(query, ro('{{-etimologie-}}\n' + case['etymology'] + '\n{{-substantiv-|ron}}\n# dictionary meaning'))
        assert variant.origins == entry.origins


def test_rost_keeps_its_latin_and_german_etymologies_separate():
    entry = parse_entry('rost',ro("{{-etimologie-}}\nDin latină ''[[rostrum]]''.\n{{-substantiv-|ron}}\n# meaning one\n{{-etimologie-}}\nDin germană ''[[Rost]]''.\n{{-substantiv-|ron}}\n# meaning two"))
    assert {(sense,o.language,o.ancestor) for sense,o in entry.origins} == {(1,'la','rostrum'),(2,'de','Rost')}


def test_import_is_byte_reproducible_with_a_fixed_source_epoch(tmp_path,monkeypatch):
    monkeypatch.setenv('SOURCE_DATE_EPOCH','1793491200')
    dump = make_dump(tmp_path/'snapshot.xml.bz2',[('casă',ro("{{-etimologie-}}\nDin latină ''[[casa]]''.\n# house"),0)])
    first,second = tmp_path/'first.sqlite',tmp_path/'second.sqlite'
    import_dump(dump,first)
    import_dump(dump,second)
    assert first.read_bytes() == second.read_bytes()


def test_pinned_download_reuses_exact_snapshot_and_preserves_cache_on_bad_checksum(tmp_path):
    from dic.dumps import download_pinned
    content=b'pinned source'
    name='rowiktionary-20261101-pages-articles.xml.bz2'
    source=tmp_path/'source.json'
    manifest={'edition':'ro','filename':name,'sha1':hashlib.sha1(content).hexdigest(),'url':'https://dumps.wikimedia.org/rowiktionary/20261101/'+name}
    source.write_text(json.dumps(manifest))
    calls=[]
    def fetch(url,destination):
        calls.append(url);destination.write_bytes(content)
    with patch('dic.dumps.fetch',fetch):
        target=download_pinned(source,tmp_path/'dumps')
        download_pinned(source,tmp_path/'dumps')
    assert calls == [manifest['url']]
    assert 'latest' not in calls[0]
    before=target.with_suffix('.bz2.json').read_bytes()
    with patch('dic.dumps.fetch',fetch):
        download_pinned(source,tmp_path/'dumps')
    assert target.with_suffix('.bz2.json').read_bytes() == before
    manifest['sha1']=hashlib.sha1(b'different expected snapshot').hexdigest()
    source.write_text(json.dumps(manifest))
    with patch('dic.dumps.fetch',fetch),pytest.raises(ValueError,match='checksum mismatch'):
        download_pinned(source,tmp_path/'dumps')
    assert target.read_bytes()==content


def test_build_keeps_a_prepared_versioned_database_unchanged(tmp_path):
    dump=make_dump(tmp_path/'snapshot.xml.bz2',[('casă',ro("{{-etimologie-}}\nDin latină ''[[casa]]''.\n# house"),0)])
    database=tmp_path/'dictionary.sqlite'
    import_dump(dump,database)
    build(database,tmp_path/'site')
    first=database.read_bytes()
    build(database,tmp_path/'site')
    assert database.read_bytes()==first


def test_rake_population_has_real_file_dependencies():
    import subprocess
    graph=subprocess.run(['rake','-P'],cwd=Path(__file__).resolve().parents[1],capture_output=True,text=True,check=True).stdout
    assert 'rake data/dic.sqlite\n    data/dumps/rowiktionary-20261101-pages-articles.xml.bz2\n    data/source.json' in graph
    assert 'rake populate\n    download\n    data/dic.sqlite' in graph
    assert 'rake test\n    browser' in graph


def test_pamant_visible_etymology_keeps_ancestry_but_hides_wiki_control_markup(tmp_path):
    dump=make_dump(tmp_path/'snapshot.xml.bz2',[('pământ',ro("{{-etimologie-}}\nProvine din latină ''[[paumentum]]'' < ''[[pavimentum]]''.\n__NOEDITSECTION__\n{{-substantiv-|ron}}\n# earth"),0)])
    database=tmp_path/'dictionary.sqlite'
    import_dump(dump,database)
    build(database,tmp_path/'site')
    with sqlite3.connect(tmp_path/'site'/'dic.sqlite') as db:
        text=db.execute('SELECT text FROM etymology_display').fetchone()[0]
    assert 'paumentum' in text and 'pavimentum' in text
    assert '__NOEDITSECTION__' not in text


def test_import_is_reproducible_across_python_hash_seeds(tmp_path):
    import os
    import subprocess
    import sys
    dump=make_dump(tmp_path/'snapshot.xml.bz2',[
        ('casă',ro("{{-etimologie-}}\nDin latină ''[[casa]]''.\n{{substantiv-ron|nom-pl=case|art-sg=casa|art-pl=casele|dat-sg=casei|dat-pl=caselor|voc-sg=caso}}\n# house"),0),
        ('case','==Romanian==\n# {{plural of|ro|casă}}',0),
    ])
    paths=[]
    for seed in ['1','777']:
        database=tmp_path/f'{seed}.sqlite'
        env={**os.environ,'PYTHONHASHSEED':seed,'SOURCE_DATE_EPOCH':'1793491200'}
        subprocess.run([sys.executable,'-m','dic.cli','import',str(dump),'--database',str(database)],env=env,check=True,capture_output=True,text=True)
        paths.append(database)
    assert paths[0].read_bytes()==paths[1].read_bytes()
