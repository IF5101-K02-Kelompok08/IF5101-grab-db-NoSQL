"""Builds the design documents and Mango indexes from the files in this folder.

Layout, per logical database (orders, fleet, customers, geo):

    <db>/validate_doc_update.js      -> _design/validation (installed before loading)
    <db>/<ddoc>/<view>.map.js        -> _design/<ddoc>, views.<view>.map
    <db>/<ddoc>/<view>.reduce.js     -> views.<view>.reduce (a built-in such as _sum)
    <db>/mango_indexes.json          -> POST /{db}/_index

The functions are kept as plain .js files so they can be read, linted and tested on their
own. Leading comment lines are stripped, because CouchDB expects the source of a function
expression.
"""

from __future__ import annotations

import json
from pathlib import Path

DESIGN_DIR = Path(__file__).resolve().parent
VALIDATION_DDOC = '_design/validation'


def function_source(path: Path) -> str:
    lines = path.read_text(encoding='utf-8').splitlines()
    while lines and (not lines[0].strip() or lines[0].lstrip().startswith('//')):
        lines.pop(0)
    return '\n'.join(lines).strip() + '\n'


def validation_ddoc(db: str) -> dict | None:
    path = DESIGN_DIR / db / 'validate_doc_update.js'
    if not path.is_file():
        return None
    return {'_id': VALIDATION_DDOC, 'language': 'javascript',
            'validate_doc_update': function_source(path)}


def view_ddocs(db: str) -> list[dict]:
    ddocs = []
    for folder in sorted(p for p in (DESIGN_DIR / db).iterdir() if p.is_dir()):
        views = {}
        for map_file in sorted(folder.glob('*.map.js')):
            name = map_file.name[:-len('.map.js')]
            view = {'map': function_source(map_file)}
            reduce_file = folder / f'{name}.reduce.js'
            if reduce_file.is_file():
                # Built-in reducers must match exactly, e.g. "_sum" without a newline.
                view['reduce'] = function_source(reduce_file).strip()
            views[name] = view
        if views:
            ddocs.append({'_id': f'_design/{folder.name}', 'language': 'javascript',
                          'views': views})
    return ddocs


def mango_indexes(db: str) -> list[dict]:
    path = DESIGN_DIR / db / 'mango_indexes.json'
    if not path.is_file():
        return []
    indexes = json.loads(path.read_text(encoding='utf-8'))
    return [{key: value for key, value in index.items() if key != 'comment'} for index in indexes]
