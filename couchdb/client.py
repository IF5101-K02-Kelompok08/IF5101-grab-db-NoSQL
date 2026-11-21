"""A small CouchDB HTTP client built on requests.

Only the parts of the CouchDB API that this project uses are wrapped. Every method maps to
one HTTP request, so the benchmark measures CouchDB round trips and not client-side magic.
"""

from __future__ import annotations

import json
import re
from typing import Any, Callable, Iterable, Iterator
from urllib.parse import quote

import requests

from couchdb.settings import Settings

# View query parameters whose values are JSON, as the CouchDB API requires.
JSON_PARAMS = {'key', 'keys', 'startkey', 'endkey', 'start_key', 'end_key'}
ROWS_START = re.compile(r'"rows"\s*:\s*\[')


class CouchError(Exception):
    """A CouchDB request that returned an error status."""

    def __init__(self, status: int, error: str, reason: str, method: str, path: str) -> None:
        super().__init__(f'{method} {path} -> {status} {error}: {reason}')
        self.status = status
        self.error = error
        self.reason = reason


def doc_path(db: str, doc_id: str) -> str:
    if doc_id.startswith('_design/'):
        return f'/{db}/_design/{quote(doc_id[len("_design/"):], safe="")}'
    return f'/{db}/{quote(doc_id, safe="")}'


def encode_params(params: dict[str, Any] | None) -> dict[str, str] | None:
    if not params:
        return None
    encoded = {}
    for name, value in params.items():
        if value is None:
            continue
        if name in JSON_PARAMS:
            encoded[name] = json.dumps(value, separators=(',', ':'))
        elif isinstance(value, bool):
            encoded[name] = 'true' if value else 'false'
        else:
            encoded[name] = str(value)
    return encoded


class CouchClient:
    def __init__(self, url: str, user: str, password: str, timeout: float | None = 120) -> None:
        self.url = url.rstrip('/')
        self.user = user
        self.timeout = timeout
        self.session = requests.Session()
        self.session.auth = (user, password)
        self.session.headers.update({'Accept': 'application/json'})

    @classmethod
    def admin(cls, settings: Settings, timeout: float | None = 120) -> 'CouchClient':
        return cls(settings.url, settings.admin_user, settings.admin_password, timeout)

    @classmethod
    def app(cls, settings: Settings, timeout: float | None = 120) -> 'CouchClient':
        """Client for the least-privileged application user, or the admin if none is set."""
        if settings.has_app_user:
            return cls(settings.url, settings.app_user, settings.app_password, timeout)
        return cls.admin(settings, timeout)

    def close(self) -> None:
        self.session.close()

    # -- raw requests -------------------------------------------------------------------

    def request(self, method: str, path: str, *, params: dict | None = None, body: Any = None,
                ok: Iterable[int] = (200, 201, 202), stream: bool = False,
                timeout: float | None | str = 'default') -> requests.Response:
        data = None
        headers = {}
        if body is not None:
            data = json.dumps(body, separators=(',', ':'), ensure_ascii=False).encode('utf-8')
            headers['Content-Type'] = 'application/json'
        response = self.session.request(
            method, self.url + path, params=encode_params(params), data=data, headers=headers,
            stream=stream, timeout=self.timeout if timeout == 'default' else timeout)
        if response.status_code not in ok:
            try:
                info = response.json()
            except ValueError:
                info = {'error': 'http_error', 'reason': response.text[:300]}
            raise CouchError(response.status_code, str(info.get('error')), str(info.get('reason')),
                             method, path)
        return response

    def json(self, method: str, path: str, **kwargs) -> Any:
        return self.request(method, path, **kwargs).json()

    # -- server and databases -----------------------------------------------------------

    def server_info(self) -> dict:
        return self.json('GET', '/')

    def db_exists(self, db: str) -> bool:
        return self.request('HEAD', f'/{db}', ok=(200, 404)).status_code == 200

    def create_db(self, db: str) -> None:
        self.request('PUT', f'/{db}', ok=(201, 202, 412))

    def delete_db(self, db: str) -> None:
        self.request('DELETE', f'/{db}', ok=(200, 202, 404))

    def db_info(self, db: str) -> dict:
        return self.json('GET', f'/{db}')

    # -- documents ----------------------------------------------------------------------

    def get_doc(self, db: str, doc_id: str, *, missing_ok: bool = False, **params) -> dict | None:
        response = self.request('GET', doc_path(db, doc_id), params=params,
                                ok=(200, 404) if missing_ok else (200,))
        return None if response.status_code == 404 else response.json()

    def put_doc(self, db: str, doc: dict) -> str:
        """Create or update a document; on success doc['_rev'] holds the new revision."""
        result = self.json('PUT', doc_path(db, doc['_id']), body=doc)
        doc['_rev'] = result['rev']
        return result['rev']

    def delete_doc(self, db: str, doc_id: str, rev: str) -> str:
        return self.json('DELETE', doc_path(db, doc_id), params={'rev': rev})['rev']

    def update_doc(self, db: str, doc_id: str, change: Callable[[dict], Any],
                   retries: int = 5) -> dict:
        """Optimistic read-modify-write: re-read and re-apply the change on a 409 conflict.

        `change` edits the document in place. It may raise to abort the update, or return
        False when there is nothing to write.
        """
        for attempt in range(retries + 1):
            doc = self.get_doc(db, doc_id)
            if change(doc) is False:
                return doc
            try:
                self.put_doc(db, doc)
                return doc
            except CouchError as error:
                if error.status != 409 or attempt == retries:
                    raise
        raise AssertionError('unreachable')

    def bulk_docs(self, db: str, docs: list[dict]) -> list[dict]:
        """Write many documents in one request. Each result has 'rev' or 'error'."""
        return self.json('POST', f'/{db}/_bulk_docs', body={'docs': docs})

    def all_docs(self, db: str, keys: list[str] | None = None, **params) -> dict:
        if keys is not None:
            return self.json('POST', f'/{db}/_all_docs', params=params, body={'keys': keys})
        return self.json('GET', f'/{db}/_all_docs', params=params)

    def purge(self, db: str, revs_by_id: dict[str, list[str]]) -> dict:
        """Remove documents and their history completely (used to restore test state)."""
        return self.json('POST', f'/{db}/_purge', body=revs_by_id)

    # -- views and Mango ----------------------------------------------------------------

    def view(self, db: str, ddoc: str, name: str, keys: list | None = None, **params) -> dict:
        path = f'/{db}/_design/{ddoc}/_view/{name}'
        if keys is not None:
            return self.json('POST', path, params=params, body={'keys': keys})
        return self.json('GET', path, params=params)

    def iter_view(self, db: str, ddoc: str, name: str, **params) -> Iterator[dict]:
        """Stream the rows of a view result instead of holding the whole body in memory."""
        path = f'/{db}/_design/{ddoc}/_view/{name}'
        response = self.request('GET', path, params=params, stream=True)
        try:
            yield from iter_rows(response.iter_content(chunk_size=1 << 16, decode_unicode=False))
        finally:
            response.close()

    def find(self, db: str, query: dict) -> dict:
        return self.json('POST', f'/{db}/_find', body=query)

    def create_index(self, db: str, index: dict) -> dict:
        return self.json('POST', f'/{db}/_index', body=index)


def iter_rows(chunks: Iterable[bytes]) -> Iterator[dict]:
    """Yield the objects of the "rows" array of a CouchDB JSON response, incrementally."""
    decoder = json.JSONDecoder()
    buffer = ''
    pending = b''
    position = -1
    for chunk in chunks:
        pending += chunk
        try:
            text = pending.decode('utf-8')
            pending = b''
        except UnicodeDecodeError as error:
            # A multi-byte character was split between chunks; keep the tail for later.
            text = pending[:error.start].decode('utf-8')
            pending = pending[error.start:]
        buffer += text
        if position < 0:
            start = ROWS_START.search(buffer)
            if start is None:
                continue
            position = start.end()
        while True:
            while position < len(buffer) and buffer[position] in ' \t\r\n,':
                position += 1
            if position >= len(buffer):
                break
            if buffer[position] == ']':
                return
            try:
                row, end = decoder.raw_decode(buffer, position)
            except json.JSONDecodeError:
                break  # incomplete row, wait for more data
            yield row
            position = end
        buffer = buffer[position:]
        position = 0
    if position < 0:
        raise ValueError('Response has no rows array')
    raise ValueError('Response ended before its rows array was closed')
