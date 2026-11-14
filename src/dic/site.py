"""Build a static site which queries the published SQLite database in-browser."""
import json
import os
from pathlib import Path
import shutil
import sqlite3

from .webdata import prepare

PROJECT_NAME = 'conventum'


def build(database: Path, output: Path, vectors: Path | None = None):
    if not database.is_file():
        raise FileNotFoundError(f'{database}: run rake populate first')
    metadata = prepare(database, vectors)
    resources = Path(__file__).with_name('web')
    template = resources.joinpath('index.html').read_text()
    template = template.replace('__PROJECT_NAME_TITLE__', PROJECT_NAME.capitalize())
    template = template.replace('__PROJECT_NAME_UPPER__', PROJECT_NAME.upper())
    template = template.replace('__PROJECT_NAME__', PROJECT_NAME)
    template = template.replace('__DIC_RANKING__', resources.joinpath('ranking.mjs').read_text().replace('export ', ''))
    template = template.replace('__DIC_SQLITE__', resources.joinpath('sqlite.mjs').read_text().replace('export ', ''))
    template = template.replace('__DIC_APP__', resources.joinpath('explorer.js').read_text())
    # Only small build metadata lives in HTML. The actual words, etymologies,
    # autocomplete results and scores are read from dic.sqlite at runtime.
    payload = json.dumps({'metadata':metadata,'database':'dic.sqlite'}, ensure_ascii=False, separators=(',',':'))
    payload = payload.replace('<','\\u003c').replace('>','\\u003e').replace('&','\\u0026')
    output.mkdir(parents=True, exist_ok=True)
    target = output / 'index.html'
    temporary = target.with_suffix('.html.building')
    temporary.write_text(template.replace('__DIC_DATA__',payload),encoding='utf-8')
    os.replace(temporary,target)
    database_copy = output / 'dic.sqlite.building'
    with sqlite3.connect(f'{database.resolve().as_uri()}?mode=ro',uri=True) as source, sqlite3.connect(database_copy) as destination:
        source.backup(destination)
    os.replace(database_copy,output / 'dic.sqlite')
    vendor = resources / 'vendor'
    for asset in ['sql-wasm.js','sql-wasm.wasm','LICENSE.sql.js']:
        if not (vendor / asset).is_file():
            raise FileNotFoundError(f'Missing {asset}: run rake setup to install pinned web assets')
        (output / 'vendor').mkdir(exist_ok=True)
        shutil.copyfile(vendor / asset,output / 'vendor' / asset)
    print(f'Generated {target}; browser reads {output / "dic.sqlite"} ({database.stat().st_size:,} bytes)')
    return target
