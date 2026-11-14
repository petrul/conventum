"""Streaming XML ingestion and normalized, provenance-preserving SQLite storage."""
import bz2
import json
import os
from pathlib import Path
import sqlite3
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from urllib.parse import quote

from .dumps import sha1
from .parser import group, parse_entry

SCHEMA = """
PRAGMA foreign_keys=ON;
CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE entries (
 id INTEGER PRIMARY KEY, word TEXT NOT NULL UNIQUE, is_lemma INTEGER NOT NULL,
 source_url TEXT NOT NULL, revision_id TEXT, revision_timestamp TEXT
);
CREATE TABLE etymologies (
 id INTEGER PRIMARY KEY, entry_id INTEGER NOT NULL REFERENCES entries(id),
 sense INTEGER NOT NULL, raw TEXT NOT NULL, UNIQUE(entry_id,sense)
);
CREATE TABLE origins (
 id INTEGER PRIMARY KEY, etymology_id INTEGER NOT NULL REFERENCES etymologies(id),
 language TEXT NOT NULL, ancestor TEXT NOT NULL, origin_group TEXT NOT NULL,
 relation TEXT NOT NULL, evidence TEXT NOT NULL, is_direct INTEGER NOT NULL,
 UNIQUE(etymology_id,language,ancestor,relation,is_direct)
);
CREATE TABLE forms (
 lemma_id INTEGER NOT NULL REFERENCES entries(id), form TEXT NOT NULL,
 kind TEXT NOT NULL, source_word TEXT NOT NULL,
 PRIMARY KEY(lemma_id,form,kind,source_word)
);
CREATE TABLE form_links (form_id INTEGER NOT NULL REFERENCES entries(id), lemma TEXT NOT NULL,
 PRIMARY KEY(form_id,lemma));
CREATE INDEX origins_group ON origins(origin_group);
CREATE INDEX forms_word ON forms(form);
CREATE VIEW latin_inheritance AS
 SELECT DISTINCT e.word AS Romanian_word, o.ancestor AS Latin_ancestor,
 e.source_url, e.revision_id, o.evidence
 FROM entries e JOIN etymologies t ON t.entry_id=e.id
 JOIN origins o ON o.etymology_id=t.id
 WHERE e.is_lemma=1 AND o.language='la' AND o.relation='inherited' AND o.is_direct=1;
CREATE VIEW latin_candidates AS
 SELECT DISTINCT e.word AS Romanian_word, o.ancestor AS Latin_ancestor,
 o.relation, e.source_url, o.evidence
 FROM entries e JOIN etymologies t ON t.entry_id=e.id
 JOIN origins o ON o.etymology_id=t.id
 WHERE e.is_lemma=1 AND o.language='la' AND o.is_direct=1;
"""


def pages(path):
    opener = bz2.open if path.suffix == '.bz2' else open
    with opener(path, 'rb') as stream:
        context = ET.iterparse(stream, events=('start', 'end'))
        _, root = next(context)
        for event, element in context:
            if event != 'end' or element.tag.rsplit('}', 1)[-1] != 'page':
                continue
            # Match any MediaWiki export namespace version.
            ns = element.tag.removesuffix('page')
            if element.findtext(ns + 'ns') == '0':
                revision = element.find(ns + 'revision')
                if revision is not None:
                    yield (element.findtext(ns + 'title') or '',
                           revision.findtext(ns + 'text') or '',
                           revision.findtext(ns + 'id'), revision.findtext(ns + 'timestamp'))
            root.clear()


def import_dump(dump: Path, database: Path, edition='ro'):
    if edition not in {'ro', 'en'}:
        raise ValueError('Edition must be ro or en')
    database.parent.mkdir(parents=True, exist_ok=True)
    temporary = database.with_suffix(database.suffix + '.building')
    temporary.unlink(missing_ok=True)
    db = sqlite3.connect(temporary)
    try:
        db.executescript(SCHEMA)
        manifest_path = dump.with_suffix(dump.suffix + '.json')
        manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {}
        if manifest.get('edition', edition) != edition:
            raise ValueError('Selected edition does not match the download manifest')
        checksum = sha1(dump)
        if manifest.get('sha1', checksum) != checksum:
            raise ValueError('Dump differs from its verified download manifest')
        timestamp = (datetime.fromtimestamp(int(os.environ['SOURCE_DATE_EPOCH']), timezone.utc)
                     if 'SOURCE_DATE_EPOCH' in os.environ else datetime.now(timezone.utc))
        meta = {'edition': edition, 'dump_file': dump.name, 'sha1': checksum,
                'imported_at': timestamp.isoformat(),
                'source': f'https://{edition}.wiktionary.org',
                'license': 'Wiktionary: CC BY-SA 4.0; see linked page histories for attribution',
                'download': manifest}
        count = 0
        for title, text, revision_id, timestamp in pages(dump):
            entry = parse_entry(title, text)
            if entry is None:
                continue
            url = f'https://{edition}.wiktionary.org/wiki/{quote(title, safe="")}'
            cursor = db.execute('INSERT INTO entries(word,is_lemma,source_url,revision_id,revision_timestamp) VALUES(?,?,?,?,?)',
                                (entry.word, entry.lemma, url, revision_id, timestamp))
            entry_id = cursor.lastrowid
            etymology_ids = {}
            for sense, raw in enumerate(entry.etymologies, 1):
                etymology_ids[sense] = db.execute('INSERT INTO etymologies(entry_id,sense,raw) VALUES(?,?,?)',
                                                 (entry_id, sense, raw)).lastrowid
            for sense, origin in entry.origins:
                db.execute('INSERT OR IGNORE INTO origins(etymology_id,language,ancestor,origin_group,relation,evidence,is_direct) VALUES(?,?,?,?,?,?,?)',
                           (etymology_ids[sense], origin.language, origin.ancestor, group(origin.language), origin.relation, origin.evidence, origin.direct))
            db.executemany('INSERT OR IGNORE INTO forms VALUES(?,?,?,?)',
                           ((entry_id, form, kind, entry.word) for form, kind in sorted(entry.forms)))
            db.executemany('INSERT OR IGNORE INTO form_links VALUES(?,?)',
                           ((entry_id, lemma) for lemma in sorted(entry.form_of)))
            count += 1
            if count % 10000 == 0:
                print(f'Parsed {count:,} Romanian entries', flush=True)
        # Resolve explicit form pages, including chains; never guess by suffix.
        # Cycles and missing targets stay inspectable in form_links.
        entries = {word: (identifier, lemma) for identifier, word, lemma in db.execute('SELECT id,word,is_lemma FROM entries')}
        links = {}
        for form_id, lemma in db.execute('SELECT form_id,lemma FROM form_links'):
            links.setdefault(form_id, set()).add(lemma)
        def targets(identifier, seen):
            if identifier in seen:
                return set()
            result = set()
            for word in sorted(links.get(identifier, ())):
                if word in entries:
                    target_id, is_lemma = entries[word]
                    if is_lemma:
                        result.add(target_id)
                    else:
                        result.update(targets(target_id, seen | {identifier}))
            return result
        for word, (identifier, _) in entries.items():
            for target in sorted(targets(identifier, set())):
                if target != identifier:
                    db.execute('INSERT OR IGNORE INTO forms VALUES(?,?,?,?)', (target, word, 'form-page', word))
        for table in ('entries', 'etymologies', 'origins', 'forms', 'form_links'):
            meta[table] = db.execute(f'SELECT count(*) FROM {table}').fetchone()[0]
        meta['lemmas'] = db.execute('SELECT count(*) FROM entries WHERE is_lemma=1').fetchone()[0]
        meta['grouped_forms'] = db.execute('SELECT count(*) FROM (SELECT DISTINCT lemma_id,form FROM forms)').fetchone()[0]
        meta['strict_latin_pairs'] = db.execute('SELECT count(*) FROM latin_inheritance').fetchone()[0]
        meta['unresolved_form_links'] = db.execute('SELECT count(*) FROM form_links f LEFT JOIN entries e ON e.word=f.lemma WHERE e.id IS NULL').fetchone()[0]
        db.executemany('INSERT INTO metadata VALUES(?,?)', ((key, json.dumps(value, ensure_ascii=False)) for key, value in meta.items()))
        db.commit()
        if db.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
            raise ValueError('SQLite integrity check failed')
    except BaseException:
        db.close()
        temporary.unlink(missing_ok=True)
        raise
    db.close()
    os.replace(temporary, database)
    print(json.dumps(meta, ensure_ascii=False, indent=2))
    return meta
