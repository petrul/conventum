"""Persist compact, publishable model scores beside the dictionary tables."""
import json
import re
from pathlib import Path
import sqlite3
import unicodedata

from .embeddings import load_scores, pair_key
from .parser import plain

WEB_SCHEMA = '''
CREATE TABLE IF NOT EXISTS origin_scores (
 origin_id INTEGER PRIMARY KEY REFERENCES origins(id) ON DELETE CASCADE,
 word_distance REAL NOT NULL, definition_distance REAL,
 word_definition TEXT, ancestor_definition TEXT, definition_source TEXT
);
CREATE TABLE IF NOT EXISTS etymology_display (
 etymology_id INTEGER PRIMARY KEY REFERENCES etymologies(id) ON DELETE CASCADE,
 text TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS search_aliases (
 search_key TEXT NOT NULL, entry_id INTEGER NOT NULL REFERENCES entries(id), kind TEXT NOT NULL,
 PRIMARY KEY(search_key,entry_id)
) WITHOUT ROWID;
'''


def search_keys(text):
    text = unicodedata.normalize('NFC',text).lower()
    variant = text.translate(str.maketrans('âî','îâ'))
    return sorted({''.join(c for c in unicodedata.normalize('NFD',value) if not unicodedata.combining(c)) for value in (text,variant)})


def prepare(database: Path, vectors: Path | None = None):
    with sqlite3.connect(database) as db:
        db.execute('PRAGMA foreign_keys=ON')
        db.executescript(WEB_SCHEMA)
        for identifier, raw in db.execute('''SELECT t.id,t.raw FROM etymologies t
                LEFT JOIN etymology_display d ON d.etymology_id=t.id
                WHERE d.etymology_id IS NULL OR instr(d.text,'__NOEDITSECTION__')>0'''):
            text = re.sub(r'__[A-Z_]+__','',plain(raw)).strip()
            db.execute('INSERT OR REPLACE INTO etymology_display VALUES(?,?)', (identifier, text or raw))
        metadata = {key:json.loads(value) for key,value in db.execute('SELECT key,value FROM metadata')}
        if metadata.get('search_schema') != 1:
            db.execute('DELETE FROM search_aliases')
            for identifier,word in db.execute('SELECT id,word FROM entries WHERE is_lemma=1'):
                db.executemany('INSERT OR IGNORE INTO search_aliases VALUES(?,?,?)', ((key,identifier,'word') for key in search_keys(word)))
            for identifier,form in db.execute('SELECT f.lemma_id,f.form FROM forms f JOIN entries e ON e.id=f.lemma_id WHERE e.is_lemma=1'):
                db.executemany('INSERT OR IGNORE INTO search_aliases VALUES(?,?,?)', ((key,identifier,'form') for key in search_keys(form)))
            for identifier,ancestor in db.execute('SELECT t.entry_id,o.ancestor FROM origins o JOIN etymologies t ON t.id=o.etymology_id JOIN entries e ON e.id=t.entry_id WHERE e.is_lemma=1'):
                db.executemany('INSERT OR IGNORE INTO search_aliases VALUES(?,?,?)', ((key,identifier,'ancestor') for key in search_keys(ancestor)))
            metadata['search_schema'] = 1
            db.execute('INSERT OR REPLACE INTO metadata VALUES(?,?)',('search_schema','1'))
        info, scores = load_scores(vectors, metadata['sha1'])
        if info and metadata.get('embeddings', {}).get('run_id') != info['run_id']:
            db.execute('DELETE FROM origin_scores')
            for identifier, word, sense, code, ancestor, direct in db.execute('''
                SELECT o.id,e.word,t.sense,o.language,o.ancestor,o.is_direct FROM origins o
                JOIN etymologies t ON t.id=o.etymology_id JOIN entries e ON e.id=t.entry_id WHERE e.is_lemma=1'''):
                score = scores.get(pair_key(word,sense,code,ancestor,direct))
                if score:
                    db.execute('INSERT INTO origin_scores VALUES(?,?,?,?,?,?)', (
                        identifier,score['word_distance'],score['definition_distance'],score['word_definition'],
                        score['ancestor_definition'],score['definition_source']))
            metadata['embeddings'] = {key:value for key,value in info.items() if key not in {'endpoint','store'}}
            db.execute('INSERT OR REPLACE INTO metadata VALUES(?,?)', ('embeddings',json.dumps(metadata['embeddings'])))
        if metadata.get('web_schema') != 1:
            db.execute('INSERT OR REPLACE INTO metadata VALUES(?,?)', ('web_schema',json.dumps(1)))
    return metadata
