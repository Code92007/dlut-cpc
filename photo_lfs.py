"""Private GitHub LFS objects; only Git pointer metadata is retained on disk."""
from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import subprocess
import tempfile
import threading
import time
import uuid
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


class StorageUnavailable(OSError):
    pass


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


_lock = threading.RLock()
_private_until = {}
_auth_cache = {}
MAX_IMAGE_BYTES = 24 * 1024 * 1024


class GitHubLFS:
    def __init__(self, runtime):
        self.repository = os.environ.get('PHOTO_LFS_REPOSITORY', '')
        if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', self.repository):
            raise StorageUnavailable('私有照片仓库尚未配置')
        self.key = Path(os.environ.get('PHOTO_LFS_KEY_FILE', runtime / 'photo_lfs_key'))
        self.known_hosts = Path(os.environ.get('PHOTO_LFS_KNOWN_HOSTS', runtime / 'photo_lfs_known_hosts'))
        self.git_dir = runtime / 'photo_lfs_metadata.git'
        self.runtime = runtime
        self.ssh = ['ssh', '-F', '/dev/null', '-i', str(self.key), '-o', 'IdentitiesOnly=yes',
                    '-o', 'StrictHostKeyChecking=yes', '-o', 'UserKnownHostsFile=' + str(self.known_hosts),
                    '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=10']

    def _credentials(self):
        if not self.key.is_file() or not self.known_hosts.is_file():
            raise StorageUnavailable('私有照片存储凭据尚未配置')

    def _http(self, url, method='GET', body=None, headers=None, limit=MAX_IMAGE_BYTES):
        parsed = urlsplit(url)
        hostname = parsed.hostname or ''
        trusted = any(hostname == domain or hostname.endswith('.' + domain)
                      for domain in ('github.com', 'githubusercontent.com', 'amazonaws.com'))
        if parsed.scheme != 'https' or not trusted or parsed.username or parsed.password or parsed.port not in (None, 443):
            raise StorageUnavailable('照片存储返回了无效地址')
        try:
            with build_opener(NoRedirect()).open(Request(url, data=body, method=method, headers=headers or {}), timeout=30) as response:
                data = response.read(limit + 1)
            if len(data) > limit:
                raise StorageUnavailable('照片存储响应过大')
            return data
        except (OSError, ValueError):
            # Do not propagate signed URLs, remote headers or credential details.
            raise StorageUnavailable('私有照片存储暂不可用，请稍后重试') from None

    def ensure_private(self):
        with _lock:
            if _private_until.get(self.repository, 0) > time.time():
                return
            # A private repo is invisible to anonymous REST requests. Never send
            # a GitHub account token to this endpoint or to the application.
            url = 'https://api.github.com/repos/' + self.repository
            try:
                with build_opener(NoRedirect()).open(Request(url, headers={'User-Agent': 'DLUT-CPC-Private-Photos'}), timeout=15) as response:
                    response.read(1)
            except HTTPError as exc:
                if exc.code == 404:
                    _private_until[self.repository] = time.time() + 300
                    return
                raise StorageUnavailable('无法确认照片仓库的私有状态') from None
            except OSError:
                raise StorageUnavailable('无法确认照片仓库的私有状态') from None
            raise StorageUnavailable('照片仓库必须保持私有，已停止访问')

    def _auth(self, operation):
        self._credentials()
        key = (self.repository, str(self.key), operation)
        with _lock:
            cached = _auth_cache.get(key)
            if cached and cached[0] > time.time():
                return cached[1]
            try:
                result = subprocess.run([*self.ssh, 'git@github.com', 'git-lfs-authenticate', self.repository + '.git', operation],
                                        capture_output=True, check=True, timeout=20)
                auth = json.loads(result.stdout)
                if not isinstance(auth.get('header'), dict) or urlsplit(auth['href']).hostname not in {'github.com', 'lfs.github.com'}:
                    raise ValueError()
                seconds = min(600, max(0, int(auth.get('expires_in', 0)) - 30))
                _auth_cache[key] = (time.time() + seconds, auth)
                return auth
            except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError):
                raise StorageUnavailable('私有照片存储认证失败') from None

    def batch(self, operation, records):
        self.ensure_private()
        auth = self._auth(operation)
        raw = self._http(auth['href'].rstrip('/') + '/objects/batch', 'POST',
                         json.dumps({'operation': operation, 'transfers': ['basic'], 'objects': records,
                                     'ref': {'name': 'refs/heads/main'}}).encode(),
                         {'Accept': 'application/vnd.git-lfs+json', 'Content-Type': 'application/vnd.git-lfs+json', **auth['header']}, 1024 * 1024)
        try:
            result = json.loads(raw)
            objects = result['objects']
            expected = {(r['oid'], r['size']) for r in records}
            if len(objects) != len(records) or {(r['oid'], r['size']) for r in objects} != expected or any(r.get('error') for r in objects):
                raise ValueError()
            return objects
        except (ValueError, KeyError, TypeError):
            raise StorageUnavailable('照片存储未接受文件，请检查 Git LFS 额度或稍后重试') from None

    def _git(self, *args, data=None, env=None):
        self._credentials()
        process_env = {**os.environ, 'GIT_SSH_COMMAND': shlex.join(self.ssh),
                       'GIT_AUTHOR_NAME': 'DLUT CPC Photos', 'GIT_AUTHOR_EMAIL': 'photos@dlut-cpc.invalid',
                       'GIT_COMMITTER_NAME': 'DLUT CPC Photos', 'GIT_COMMITTER_EMAIL': 'photos@dlut-cpc.invalid',
                       'GIT_TERMINAL_PROMPT': '0', **(env or {})}
        try:
            result = subprocess.run(['git', '-c', 'core.hooksPath=/dev/null', '--git-dir=' + str(self.git_dir), *args],
                                    input=data, capture_output=True, check=True, timeout=45, env=process_env)
            return result.stdout.decode().strip()
        except (OSError, subprocess.SubprocessError):
            raise StorageUnavailable('照片索引同步失败，请稍后重试') from None

    def commit_pointers(self, pointers, message):
        # The bare Git cache contains only ~130-byte pointers, never images or
        # an LFS object cache. Serialize index operations across HTTP threads.
        with _lock:
            self.runtime.mkdir(parents=True, exist_ok=True)
            if not self.git_dir.exists():
                self._git('init', '--bare', '--initial-branch=main')
            remote = 'git@github.com:' + self.repository + '.git'
            self._git('fetch', '--no-tags', remote, 'refs/heads/main')
            parent = self._git('rev-parse', 'FETCH_HEAD')
            with tempfile.TemporaryDirectory(prefix='photo-index-', dir=self.runtime) as temporary:
                env = {'GIT_INDEX_FILE': str(Path(temporary) / 'index')}
                self._git('read-tree', parent, env=env)
                for path, pointer in pointers.items():
                    if pointer is None:
                        self._git('update-index', '--index-info', data=('0 ' + '0' * 40 + '\t' + path + '\n').encode(), env=env)
                    else:
                        blob = self._git('hash-object', '-w', '--stdin', data=pointer)
                        self._git('update-index', '--add', '--cacheinfo', '100644,' + blob + ',' + path, env=env)
                tree = self._git('write-tree', env=env)
                commit = self._git('commit-tree', tree, '-p', parent, data=message.encode())
                # Non-fast-forward pushes fail closed; never overwrite a writer.
                self._git('push', remote, commit + ':refs/heads/main')
                self._git('update-ref', 'refs/heads/main', commit)
                return commit

    @staticmethod
    def pointer(record):
        return f"version https://git-lfs.github.com/spec/v1\noid sha256:{record['oid']}\nsize {record['size']}\n".encode()

    def put(self, photo, full, thumb):
        uuid.UUID(photo)
        data = {'full': full, 'thumb': thumb}
        records = {variant: {'oid': hashlib.sha256(raw).hexdigest(), 'size': len(raw)} for variant, raw in data.items()}
        unique = {r['oid']: r for r in records.values()}
        by_oid = {records[variant]['oid']: raw for variant, raw in data.items()}
        for item in self.batch('upload', list(unique.values())):
            actions = item.get('actions', {})
            if 'upload' in actions:
                action = actions['upload']
                self._http(action['href'], 'PUT', by_oid[item['oid']],
                           {'Content-Type': 'application/octet-stream', **action.get('header', {})}, 1024 * 1024)
            if 'verify' in actions:
                action = actions['verify']
                self._http(action['href'], 'POST', json.dumps({'oid': item['oid'], 'size': item['size']}).encode(),
                           {'Content-Type': 'application/vnd.git-lfs+json', 'Accept': 'application/vnd.git-lfs+json', **action.get('header', {})}, 1024 * 1024)
        pointers = {f'photos/{photo}{".thumb" if variant == "thumb" else ""}.jpg': self.pointer(record) for variant, record in records.items()}
        pointers['.gitattributes'] = b'*.jpg filter=lfs diff=lfs merge=lfs -text\n'
        commit = self.commit_pointers(pointers, 'Add private competition photo ' + photo + '\n')
        return {variant: {**record, 'commit': commit} for variant, record in records.items()}

    def get(self, record):
        oid, size = record['oid'], record['size']
        if not re.fullmatch('[0-9a-f]{64}', oid) or not 0 < size <= MAX_IMAGE_BYTES:
            raise StorageUnavailable('照片索引无效')
        item = self.batch('download', [{'oid': oid, 'size': size}])[0]
        try:
            action = item['actions']['download']
            data = self._http(action['href'], headers=action.get('header', {}), limit=size)
        except (KeyError, TypeError):
            raise StorageUnavailable('照片暂不可用') from None
        if len(data) != size or hashlib.sha256(data).hexdigest() != oid:
            raise StorageUnavailable('照片完整性校验失败')
        return data

    def remove(self, photo):
        uuid.UUID(photo)
        self.ensure_private()
        self.commit_pointers({f'photos/{photo}.jpg': None, f'photos/{photo}.thumb.jpg': None},
                             'Remove competition photo from current gallery ' + photo + '\n')
