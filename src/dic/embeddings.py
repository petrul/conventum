"""Resumable Ollama embeddings and exact paired cosine distances on local disk.

SQLite stores normalized float32 vectors. We compare known word/etymon pairs,
so an approximate nearest-neighbour index would add error without saving work.
The static page receives scores, never the vector collection or an Ollama URL.
"""
from array import array
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re
import sqlite3
import sys
import time
import unicodedata
import urllib.error
import urllib.request
from urllib.parse import quote

from .database import pages
from .dumps import sha1
from .parser import (ETY_MARKER, LANGUAGE_HEADING, ancestor_term, is_form_definition,
                     normalize, plain)

DEFAULT_ENDPOINT = 'http://zmeu.local:11434'
DEFAULT_MODEL = 'bge-m3:567m-fp16'
RECIPE = 'bare-words-and-first-attested-definition-v1'
SCHEMA = '''
CREATE TABLE IF NOT EXISTS metadata(key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS vectors (
 model_digest TEXT NOT NULL, text_hash TEXT NOT NULL, input_text TEXT NOT NULL,
 dimensions INTEGER NOT NULL, vector BLOB NOT NULL, created_at TEXT NOT NULL,
 PRIMARY KEY(model_digest,text_hash)
);
CREATE TABLE IF NOT EXISTS lexicon (
 language TEXT NOT NULL, term TEXT NOT NULL, sense INTEGER NOT NULL,
 definitions TEXT NOT NULL, source_url TEXT NOT NULL, revision TEXT,
 PRIMARY KEY(language,term,sense)
);
CREATE TABLE IF NOT EXISTS runs (
 id TEXT PRIMARY KEY, dataset_sha TEXT NOT NULL, model TEXT NOT NULL,
 model_digest TEXT NOT NULL, recipe TEXT NOT NULL, endpoint TEXT NOT NULL,
 status TEXT NOT NULL, started_at TEXT NOT NULL, completed_at TEXT, summary TEXT
);
CREATE TABLE IF NOT EXISTS pair_scores (
 run_id TEXT NOT NULL REFERENCES runs(id), pair_key TEXT NOT NULL,
 word TEXT NOT NULL, sense INTEGER NOT NULL, language TEXT NOT NULL,
 ancestor TEXT NOT NULL, origin_group TEXT NOT NULL, is_direct INTEGER NOT NULL,
 word_distance REAL NOT NULL, definition_distance REAL,
 word_text_hash TEXT NOT NULL, ancestor_text_hash TEXT NOT NULL,
 word_definition_hash TEXT, ancestor_definition_hash TEXT,
 word_definition TEXT, ancestor_definition TEXT, definition_source TEXT,
 PRIMARY KEY(run_id,pair_key)
);
CREATE INDEX IF NOT EXISTS pairs_distance ON pair_scores(run_id,word_distance);
'''
LANGUAGES = {'ron':'ro','lat':'la','fra':'fr','eng':'en','deu':'de','ger':'de','ita':'it',
             'tur':'tr','hun':'hu','ell':'el','rus':'ru','bul':'bg','spa':'es','por':'pt',
             'srp':'sr','hrv':'hr','ukr':'uk','pol':'pl','ces':'cs','slk':'sk','slv':'sl',
             'sqi':'sq','ara':'ar','fas':'fa','nld':'nl','chu':'cu',
             'Romanian':'ro','Latin':'la','French':'fr','English':'en','German':'de',
             'Italian':'it','Turkish':'tr','Hungarian':'hu','Greek':'el','Ancient Greek':'grc',
             'Russian':'ru','Bulgarian':'bg','Spanish':'es','Portuguese':'pt',
             'Romanian language':'ro'}


def now():
    return datetime.now(timezone.utc).isoformat()


def text_hash(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def pair_key(word, sense, code, ancestor, direct):
    return text_hash(json.dumps([word, sense, code, ancestor, bool(direct)], ensure_ascii=False))


def lookup_term(term, code):
    term = normalize(term).strip().removeprefix('*')
    # Latin breves/macrons are pronunciation marks, not separate dictionary keys.
    # Keep Romanian accents and all other scripts intact.
    if code == 'la':
        term = unicodedata.normalize('NFC', ''.join(c for c in unicodedata.normalize('NFD', term)
                                                  if c not in '\u0304\u0306'))
    return term


def connect(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path)
    db.execute('PRAGMA foreign_keys=ON')
    db.execute('PRAGMA journal_mode=WAL')
    db.executescript(SCHEMA)
    return db


def request_json(endpoint, path, payload=None, timeout=180, retries=3):
    data = None if payload is None else json.dumps(payload).encode()
    request = urllib.request.Request(endpoint.rstrip('/') + path, data=data,
                                     headers={'Content-Type':'application/json','User-Agent':'dic/0.1'})
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return json.load(response)
        except urllib.error.HTTPError as error:
            detail = error.read(1024).decode(errors='replace')
            if error.code < 500 or attempt == retries - 1:
                raise RuntimeError(f'Ollama HTTP {error.code}: {detail}') from error
        except (urllib.error.URLError, TimeoutError, ConnectionError) as error:
            if attempt == retries - 1:
                raise RuntimeError(f'Cannot reach Ollama at {endpoint}: {error}') from error
        time.sleep(min(2 ** attempt, 8))


class Ollama:
    def __init__(self, endpoint=DEFAULT_ENDPOINT, model=DEFAULT_MODEL):
        self.endpoint, self.model = endpoint, model
        tags = request_json(endpoint, '/api/tags')['models']
        match = next((m for m in tags if m['name'] == model or m['name'] == model + ':latest'), None)
        if not match:
            raise ValueError(f'{model} is not installed at {endpoint}; choose a model from /api/tags')
        self.digest = match['digest']
        self.dimensions = None

    def embed(self, texts):
        result = request_json(self.endpoint, '/api/embed', {
            'model': self.model, 'input': texts, 'truncate': False, 'keep_alive': '30m'})
        vectors = result.get('embeddings', [])
        if len(vectors) != len(texts):
            raise ValueError('Ollama returned the wrong number of embeddings')
        return vectors


def normalized_blob(values):
    if not values or not all(math.isfinite(v) for v in values):
        raise ValueError('Embedding is empty or contains non-finite values')
    norm = math.sqrt(sum(v * v for v in values))
    if norm <= 0:
        raise ValueError('Embedding has zero norm')
    result = array('f', (v / norm for v in values))
    if sys.byteorder != 'little':
        result.byteswap()
    return result.tobytes()


def unpack(blob):
    values = array('f')
    values.frombytes(blob)
    if sys.byteorder != 'little':
        values.byteswap()
    return values


def cosine_distance(first, second):
    if len(first) != len(second) or not first:
        raise ValueError('Embedding dimensions differ or are empty')
    return min(2.0, max(0.0, 1.0 - sum(a * b for a, b in zip(first, second))))


def language_sections(text):
    headings = list(LANGUAGE_HEADING.finditer(text))
    for index, heading in enumerate(headings):
        label = heading[1].strip()
        template = re.fullmatch(r'\{\{\s*limba\s*\|\s*([^|}]+)\s*\}\}', label, re.I)
        code = template[1].strip() if template else label
        code = LANGUAGES.get(code, code if len(code) in {2, 3} and code.islower() else None)
        if code:
            yield code, text[heading.end():headings[index + 1].start() if index + 1 < len(headings) else len(text)]


def definitions(body):
    """First dictionary definitions per etymology; excludes examples and form pages."""
    result = {}
    markers = list(ETY_MARKER.finditer(body))
    for match in re.finditer(r'^#+(?![:*#])\s*(.+)$', body, re.M):
        raw = match[1]
        if is_form_definition(raw):
            continue
        text = re.sub(r'\s+', ' ', plain(raw)).strip()
        if not text or not re.search(r'[^\W\d_]', text):
            continue
        sense = sum(marker.start() < match.start() for marker in markers)
        result.setdefault(sense, [])
        if text not in result[sense] and len(result[sense]) < 8:
            result[sense].append(text[:2000])
    return result


def enrich_lexicon(db, dump, wanted, edition):
    checksum = sha1(dump)
    marker = text_hash(json.dumps([checksum, sorted(wanted), 'lexicon-v3'], ensure_ascii=False))
    previous = db.execute("SELECT value FROM metadata WHERE key='lexicon_fingerprint'").fetchone()
    if previous and json.loads(previous[0]) == marker:
        return
    # Keep the old cache if parsing fails. Lexicon writes and its fingerprint
    # are committed as one transaction, independently of vector checkpoints.
    with db:
        db.execute('DELETE FROM lexicon')
        count = 0
        reported = 0
        for word, text, revision, _ in pages(dump):
            for code, body in language_sections(normalize(text)):
                term = lookup_term(word, code)
                if (code, term) not in wanted:
                    continue
                for sense, glosses in definitions(body).items():
                    db.execute('INSERT OR REPLACE INTO lexicon VALUES(?,?,?,?,?,?)', (
                        code, term, sense, json.dumps(glosses, ensure_ascii=False),
                        f'https://{edition}.wiktionary.org/w/index.php?oldid={revision}', revision))
                    count += 1
            if count - reported >= 20000:
                print(f'Collected {count:,} definition groups', flush=True)
                reported = count
        db.execute('INSERT OR REPLACE INTO metadata VALUES(?,?)', ('lexicon_fingerprint', json.dumps(marker)))
    print(f'Collected {count:,} attested definition groups', flush=True)


def inline_gloss(raw, ancestor, code):
    for match in re.finditer(r"''([^'\n]+)''\s*\([„\"“]([^”\"“]+)[”\"“]\)", raw):
        if lookup_term(ancestor_term(match[1]), code) == lookup_term(ancestor, code):
            return plain(match[2])
    return None


def get_definitions(db):
    return {(code, term, sense):(json.loads(glosses), url) for code, term, sense, glosses, url in
            db.execute('SELECT language,term,sense,definitions,source_url FROM lexicon')}


def definition_for(lexicon, code, term, sense=None):
    term = lookup_term(term, code)
    if sense is not None and (code, term, sense) in lexicon:
        return lexicon[code, term, sense]
    # Do not borrow a different Romanian homograph's meaning.
    if sense is not None:
        return lexicon.get((code, term, 0))
    candidates = [(key[2], value) for key, value in lexicon.items() if key[:2] == (code, term)]
    return min(candidates, default=(0, None), key=lambda item:item[0])[1]


def ensure_vectors(db, client, texts, batch_size=64):
    texts = sorted(set(texts))
    cached = {row[0] for row in db.execute('SELECT text_hash FROM vectors WHERE model_digest=?', (client.digest,))}
    pending = [(text_hash(text), text) for text in texts if text_hash(text) not in cached]
    dimensions = {row[0] for row in db.execute('SELECT DISTINCT dimensions FROM vectors WHERE model_digest=?', (client.digest,))}
    if len(dimensions) > 1:
        raise ValueError('Cache contains inconsistent vector dimensions')
    expected = next(iter(dimensions), None)
    print(f'{len(texts):,} unique inputs; {len(pending):,} to embed ({len(texts)-len(pending):,} cached)', flush=True)
    started = time.monotonic()
    for offset in range(0, len(pending), batch_size):
        batch = pending[offset:offset + batch_size]
        vectors = client.embed([text for _, text in batch])
        if len(vectors) != len(batch):
            raise ValueError('Wrong embedding batch length')
        records = []
        for (key, text), values in zip(batch, vectors):
            expected = expected or len(values)
            if len(values) != expected:
                raise ValueError('Ollama embedding dimensions changed; refusing mixed vectors')
            records.append((client.digest, key, text, expected, normalized_blob(values), now()))
        with db:
            db.executemany('INSERT INTO vectors VALUES(?,?,?,?,?,?)', records)
        done = min(offset + len(batch), len(pending))
        if offset == 0 or done == len(pending) or done % (batch_size * 20) == 0:
            elapsed = time.monotonic() - started
            print(f'Embedded {done:,}/{len(pending):,} · {done/max(elapsed, .001):.1f} inputs/s', flush=True)
    return expected


def compute(database: Path, store: Path, dump: Path | None = None,
            endpoint=DEFAULT_ENDPOINT, model=DEFAULT_MODEL, batch_size=64, client=None):
    if batch_size < 1:
        raise ValueError('Batch size must be positive')
    if not database.is_file():
        raise FileNotFoundError(f'{database}: populate the dictionary first')
    with sqlite3.connect(f'{database.resolve().as_uri()}?mode=ro', uri=True) as source:
        metadata = {key: json.loads(value) for key, value in source.execute('SELECT key,value FROM metadata')}
        words = [row[0] for row in source.execute('SELECT word FROM entries')]
        pairs = list(source.execute('''SELECT e.word,t.sense,o.language,o.ancestor,o.origin_group,o.is_direct,t.raw
            FROM entries e JOIN etymologies t ON t.entry_id=e.id JOIN origins o ON o.etymology_id=t.id
            WHERE e.is_lemma=1 GROUP BY e.word,t.sense,o.language,o.ancestor,o.is_direct
            ORDER BY e.word,t.sense,o.language,o.ancestor,o.is_direct'''))
    client = client or Ollama(endpoint, model)
    run_id = text_hash(json.dumps([metadata['sha1'], client.digest, RECIPE, pairs], ensure_ascii=False))
    db = connect(store)
    try:
        db.execute('INSERT OR REPLACE INTO runs VALUES(?,?,?,?,?,?,?,?,?,?)', (
            run_id, metadata['sha1'], model, client.digest, RECIPE, endpoint, 'running', now(), None, None))
        db.commit()
        if dump:
            if sha1(dump) != metadata['sha1']:
                raise ValueError('Definition dump checksum differs from the imported dictionary')
            wanted = {('ro', word) for word in words}
            wanted.update((code, lookup_term(ancestor, code)) for _, _, code, ancestor, *_ in pairs)
            enrich_lexicon(db, dump, wanted, metadata['edition'])
        lexicon = get_definitions(db)
        # Index alternate source senses once rather than scanning the whole lexicon per pair.
        by_term = {}
        for (code, term, sense), value in sorted(lexicon.items(), key=lambda item:item[0][2], reverse=True):
            by_term[code, term] = value
        prepared = []
        texts = set(words)
        for word, sense, code, ancestor, origin_group, direct, raw in pairs:
            texts.add(ancestor)
            target = definition_for(lexicon, 'ro', word, sense)
            origin = by_term.get((code, lookup_term(ancestor, code)))
            gloss = inline_gloss(raw, ancestor, code)
            if gloss:
                origin = ([gloss], f"{metadata['source']}/wiki/{quote(word, safe='')}")
            # Definitions only, with no headword or language scaffold. This
            # deliberately excludes orthographic identity from the meaning score.
            word_definition = target[0][0] if target else None
            ancestor_definition = origin[0][0] if origin else None
            if word_definition and ancestor_definition:
                texts.update((word_definition, ancestor_definition))
            prepared.append((word, sense, code, ancestor, origin_group, direct,
                             word_definition, ancestor_definition, origin[1] if origin else None))
        dimensions = ensure_vectors(db, client, texts, batch_size)
        # Keep only the keys required by this dataset/model in memory. Vectors
        # stay as compact float32 arrays, not Python float objects.
        wanted_hashes = {text_hash(text) for text in texts}
        vectors = {key:unpack(blob) for key, blob in db.execute('SELECT text_hash,vector FROM vectors WHERE model_digest=?',
                                                              (client.digest,)) if key in wanted_hashes}
        count_definitions = 0
        with db:
            db.execute('DELETE FROM pair_scores WHERE run_id=?', (run_id,))
            for word, sense, code, ancestor, origin_group, direct, wd, ad, definition_source in prepared:
                wh, ah = text_hash(word), text_hash(ancestor)
                distance = cosine_distance(vectors[wh], vectors[ah])
                meaning_distance = cosine_distance(vectors[text_hash(wd)], vectors[text_hash(ad)]) if wd and ad else None
                count_definitions += meaning_distance is not None
                db.execute('INSERT INTO pair_scores VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)', (
                    run_id, pair_key(word, sense, code, ancestor, direct), word, sense, code, ancestor, origin_group,
                    direct, distance, meaning_distance, wh, ah, text_hash(wd) if wd and ad else None,
                    text_hash(ad) if wd and ad else None, wd, ad, definition_source))
            summary = {'run_id':run_id,'dataset_sha':metadata['sha1'],'model':model,'model_digest':client.digest,
                       'recipe':RECIPE,'dimensions':dimensions,'endpoint':endpoint,'completed_at':now(),
                       'dictionary_words':len(words),'unique_inputs':len(texts),'pairs':len(pairs),
                       'definition_pairs':count_definitions,'store':str(store)}
            db.execute("UPDATE runs SET status='complete',completed_at=?,summary=? WHERE id=?", (now(), json.dumps(summary), run_id))
            db.execute('INSERT OR REPLACE INTO metadata VALUES(?,?)', ('active_run', json.dumps(run_id)))
        print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
        return summary
    except BaseException:
        db.rollback()
        db.execute("UPDATE runs SET status='interrupted' WHERE id=?", (run_id,))
        db.commit()
        raise
    finally:
        db.close()


def load_scores(store, dataset_sha):
    if not store or not store.is_file():
        return None, {}
    with sqlite3.connect(f'{store.resolve().as_uri()}?mode=ro', uri=True) as db:
        active = db.execute("SELECT value FROM metadata WHERE key='active_run'").fetchone()
        if not active:
            return None, {}
        run_id = json.loads(active[0])
        row = db.execute("SELECT summary FROM runs WHERE id=? AND dataset_sha=? AND status='complete'", (run_id, dataset_sha)).fetchone()
        if not row:
            print('Vector scores are absent or stale for this dictionary; run rake embed', flush=True)
            return None, {}
        summary = json.loads(row[0])
        scores = {}
        for key, distance, meaning, wd, ad, source in db.execute('''SELECT pair_key,word_distance,definition_distance,
                word_definition,ancestor_definition,definition_source FROM pair_scores WHERE run_id=?''', (run_id,)):
            scores[key] = {'word_distance':round(distance, 6), 'definition_distance':round(meaning, 6) if meaning is not None else None,
                           'word_definition':wd,'ancestor_definition':ad,'definition_source':source}
        return summary, scores
