"""Small, vendored wire helpers; no dependency on another running project."""
from __future__ import annotations
import hashlib
import json
import sqlite3
import uuid
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import Request, build_opener, HTTPRedirectHandler


def connect(path):
    db = sqlite3.connect(path, timeout=30)
    db.row_factory = sqlite3.Row
    db.execute('pragma busy_timeout=30000')
    db.executescript('''
      create table if not exists cpc_ids(kind text, local_id text, uid text unique,
        primary key(kind,local_id));
    ''')
    return db


def uid(db, kind, local_id):
    db.execute('insert or ignore into cpc_ids values (?,?,?)',
               (kind, str(local_id), str(uuid.uuid4())))
    return db.execute('select uid from cpc_ids where kind=? and local_id=?',
                      (kind, str(local_id))).fetchone()[0]


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def fetch(base, path, token, payload=None):
    parsed = urlsplit(base)
    if parsed.scheme not in {'http', 'https'} or not parsed.hostname or parsed.username or parsed.query or parsed.fragment:
        raise ValueError('请配置有效的服务地址')
    headers = {'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json'}
    body = None if payload is None else json.dumps(payload).encode()
    request = Request(base.rstrip('/') + path, data=body, headers=headers)
    with build_opener(NoRedirect()).open(request, timeout=15) as response:
        raw = response.read(10_000_001)
    if len(raw) > 10_000_000:
        raise ValueError('联动响应过大')
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError('联动响应格式错误')
    return value


def validate_snapshot(value, authority):
    if (value.get('schema_version') != 1 or value.get('snapshot_complete') is not True
            or not authority or value.get('authority_id') != authority):
        raise ValueError('联动信源身份或快照不匹配')
    return value


def catalog(path):
    value = json.loads(Path(path).read_text())
    aliases = {}
    ids = set()
    for contest in value['contests']:
        for problem in contest['problems']:
            if problem['id'] in ids:
                raise ValueError('目录题目重复')
            ids.add(problem['id'])
            for alias in problem.get('aliases', []):
                if alias in aliases and aliases[alias] != problem['id']:
                    raise ValueError('目录映射重复')
                aliases[alias] = problem['id']
    return value, aliases
