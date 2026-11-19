"""Private competition albums, using OJ Wall login and live CPC claim checks."""
from __future__ import annotations

import base64
import datetime as dt
import hashlib
import hmac
import io
import json
import os
import re
import secrets
import sqlite3
import time
import uuid
import warnings
from http.cookies import SimpleCookie
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlsplit

from cpc_common import fetch
from cpc_integration import Integration
from photo_lfs import GitHubLFS, StorageUnavailable


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def cookie_value(handler, name):
    jar = SimpleCookie()
    try:
        jar.load(handler.headers.get('Cookie', ''))
    except Exception:
        return ''
    return jar[name].value if name in jar else ''


class Photos:
    def __init__(self, database):
        self.database = database
        self.integration = Integration(database)
        self.root = Path(os.environ.get('COMPETITION_PHOTOS_PATH', database.path.parent / 'competition_photos')).resolve()
        web = (Path(__file__).parent / 'web').resolve()
        if self.root == web or web in self.root.parents:
            raise ValueError('照片目录必须位于静态网站目录之外')
        with self.db():
            pass

    def db(self):
        db = self.database.connect()
        db.executescript('''
          create table if not exists photo_albums(
            id text primary key, title text not null, date text not null,
            description text not null, published integer not null, created integer not null);
          create table if not exists competition_photos(
            id text primary key, album text not null, caption text not null, created integer not null);
          create index if not exists photo_album_index on competition_photos(album,created);
          create table if not exists photo_blobs(
            photo text not null, variant text not null, repository text not null,
            oid text not null, size integer not null, git_commit text not null,
            primary key(photo,variant));
          create table if not exists photo_sessions(
            hash text primary key, claim text not null, expires integer not null, csrf text not null);
          create table if not exists photo_login_states(
            hash text primary key, verifier text not null, expires integer not null);
        ''')
        return db

    def remote_store(self):
        backend = os.environ.get('PHOTO_STORAGE', 'local')
        if backend == 'local':
            return None
        if backend != 'github-lfs':
            raise StorageUnavailable('照片存储配置无效')
        return GitHubLFS(self.database.path.parent)

    def identity(self, handler):
        token = cookie_value(handler, 'dlut_photos')
        if not token:
            return None
        with self.db() as db:
            # Join the live approval every time, including image GET and HEAD.
            row = db.execute("""select s.csrf,c.account_name,c.person from photo_sessions s
                join cpc_claims c on c.id=s.claim where s.hash=? and s.expires>?
                and c.status='approved' and c.client=?""",
                (digest(token), int(time.time()), os.environ.get('OJ_WALL_AUTHORITY_ID', ''))).fetchone()
            if row:
                member = self.integration.canonical_person(db, row['person'])
                if not db.execute("select 1 from cpc_ids i join members m on cast(m.id as text)=i.local_id where i.kind='person' and i.uid=?", (member,)).fetchone():
                    return None
        return dict(row) if row else None

    def albums(self, admin=False):
        with self.db() as db:
            rows = db.execute('''select a.*,count(p.id) as count,
                (select id from competition_photos where album=a.id order by created,id limit 1) as cover
                from photo_albums a left join competition_photos p on p.album=a.id
                where (? or a.published=1) group by a.id order by a.date desc,a.created desc''', (admin,))
            return [dict(r) for r in rows]

    def album(self, album, page=1, admin=False):
        with self.db() as db:
            row = db.execute('select * from photo_albums where id=? and (? or published=1)', (album, admin)).fetchone()
            if not row:
                return None
            count = db.execute('select count(*) from competition_photos where album=?', (album,)).fetchone()[0]
            pages = max(1, (count + 59) // 60)
            page = min(max(1, page), pages)
            items = [dict(r) for r in db.execute('select id,caption from competition_photos where album=? order by created,id limit 60 offset ?', (album, (page - 1) * 60))]
            return {'album': dict(row), 'items': items, 'count': count, 'page': page, 'pages': pages}

    def save_album(self, body):
        title = body.get('title', '')
        description = body.get('description', '')
        date = body.get('date', '')
        if not isinstance(title, str) or not 1 <= len(title.strip()) <= 200 or not isinstance(description, str) or len(description) > 2000:
            raise ValueError('请填写相册名称（最多 200 字）和简介（最多 2000 字）')
        dt.date.fromisoformat(date)
        if type(body.get('published', False)) is not bool:
            raise ValueError('相册状态无效')
        album = body.get('id') or str(uuid.uuid4())
        uuid.UUID(album)
        with self.db() as db:
            if body.get('id') and not db.execute('select 1 from photo_albums where id=?', (album,)).fetchone():
                raise ValueError('相册不存在')
            db.execute('''insert into photo_albums values (?,?,?,?,?,?) on conflict(id) do update set
                title=excluded.title,date=excluded.date,description=excluded.description,published=excluded.published''',
                (album, title.strip(), date, description.strip(), body.get('published', False), int(time.time())))
        return {'ok': True, 'id': album}

    def upload(self, album, caption, raw):
        from PIL import Image, ImageOps, UnidentifiedImageError
        if not isinstance(caption, str) or len(caption) > 300 or not 0 < len(raw) <= 12 * 1024 * 1024:
            raise ValueError('每张照片不超过 12 MB，说明不超过 300 字')
        with self.db() as db:
            if not db.execute('select 1 from photo_albums where id=?', (album,)).fetchone():
                raise ValueError('相册不存在')
        # Decode only raster formats, discard source metadata and filenames.
        try:
            with warnings.catch_warnings():
                warnings.simplefilter('error', Image.DecompressionBombWarning)
                with Image.open(io.BytesIO(raw), formats=('JPEG', 'PNG', 'WEBP')) as source:
                    if source.width * source.height > 40_000_000:
                        raise ValueError('照片像素过大，请缩小到 4000 万像素以内')
                    source.load()
                    image = ImageOps.exif_transpose(source).convert('RGB')
                    image.info.clear()
        except (UnidentifiedImageError, OSError, Image.DecompressionBombError, Image.DecompressionBombWarning):
            raise ValueError('请上传有效的 JPEG、PNG 或 WebP 照片') from None
        image.thumbnail((6000, 6000))
        photo = str(uuid.uuid4())
        store = self.remote_store()
        if store:
            full_data, thumb_data = io.BytesIO(), io.BytesIO()
            image.save(full_data, 'JPEG', quality=92)
            image.thumbnail((720, 720))
            image.save(thumb_data, 'JPEG', quality=85)
            records = store.put(photo, full_data.getvalue(), thumb_data.getvalue())
            with self.db() as db:
                db.execute('insert into competition_photos values (?,?,?,?)', (photo, album, caption.strip(), time.time_ns()))
                for variant, record in records.items():
                    db.execute('insert into photo_blobs values (?,?,?,?,?,?)',
                               (photo, variant, store.repository, record['oid'], record['size'], record['commit']))
            return {'ok': True, 'id': photo}
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        full, thumb = self.file(photo), self.file(photo, True)
        try:
            image.save(full, 'JPEG', quality=92)
            image.thumbnail((720, 720))
            image.save(thumb, 'JPEG', quality=85)
            with self.db() as db:
                db.execute('insert into competition_photos values (?,?,?,?)', (photo, album, caption.strip(), time.time_ns()))
        except Exception:
            full.unlink(missing_ok=True)
            thumb.unlink(missing_ok=True)
            raise
        return {'ok': True, 'id': photo}

    def file(self, photo, thumb=False):
        uuid.UUID(photo)
        return self.root / (photo + ('.thumb.jpg' if thumb else '.jpg'))


def same_origin(handler):
    return handler.headers.get('Origin') == os.environ.get('PHOTO_PUBLIC_URL', '').rstrip('/')


def handle(handler, database, post=False):
    parsed = urlsplit(handler.path)
    path = parsed.path
    if not path.startswith(('/api/photos/', '/api/admin/photos/')):
        return False
    # Strip codes from the application's access log.
    handler.requestline = f'{handler.command} {path} {handler.request_version}'
    def respond(body, status=200, headers=None):
        handler._send_json(body, status, headers={'Cache-Control': 'private, no-store', 'Vary': 'Cookie', 'Referrer-Policy': 'no-referrer', **(headers or {})})
    try:
        service = Photos(database)
        query = parse_qs(parsed.query)
        admin = handler.server.admin_auth.session(handler.headers.get('Cookie', ''))
        identity = service.identity(handler)
        base = os.environ.get('PHOTO_PUBLIC_URL', '').rstrip('/')
        wall = os.environ.get('OJ_WALL_URL', '').rstrip('/')
        configured = bool(base.startswith('https://') and wall.startswith('https://') and os.environ.get('OJ_WALL_AUTHORITY_ID') and os.environ.get('CPC_SYNC_TOKEN'))
        if not post and path == '/api/photos/session':
            respond({'authenticated': bool(identity), 'admin': bool(admin), 'configured': configured,
                     'account': identity['account_name'] if identity else None, 'csrf': identity['csrf'] if identity else None,
                     'wallUrl': wall if configured else None})
            return True
        if not post and path == '/api/photos/login':
            if not configured:
                respond({'error': '照片登录暂未配置'}, 503)
                return True
            state, verifier = secrets.token_urlsafe(32), secrets.token_urlsafe(48)
            proof = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip('=')
            with service.db() as db:
                db.execute('delete from photo_login_states where expires<=?', (int(time.time()),))
                db.execute('delete from photo_sessions where expires<=?', (int(time.time()),))
                db.execute('insert into photo_login_states values (?,?,?)', (digest(state), verifier, int(time.time()) + 600))
            handler.send_response(302)
            handler.send_header('Location', wall + '/api/cpc/photos/authorize?' + urlencode({'state': state, 'code_challenge': proof}))
            handler.send_header('Set-Cookie', f'dlut_photo_state={state}; Path=/api/photos/; HttpOnly; Secure; SameSite=Lax; Max-Age=600')
            handler.send_header('Cache-Control', 'no-store')
            handler.send_header('Referrer-Policy', 'no-referrer')
            handler.send_header('Content-Length', '0')
            handler.end_headers()
            return True
        if not post and path == '/api/photos/callback':
            state, code = query.get('state', [''])[0], query.get('code', [''])[0]
            cookie = cookie_value(handler, 'dlut_photo_state')
            if not configured or not state or not cookie or not hmac.compare_digest(state, cookie):
                handler._send_redirect('/photos?auth=expired')
                return True
            with service.db() as db:
                db.execute('begin immediate')
                row = db.execute('select verifier from photo_login_states where hash=? and expires>?', (digest(state), int(time.time()))).fetchone()
                db.execute('delete from photo_login_states where hash=?', (digest(state),))
            if not row:
                handler._send_redirect('/photos?auth=expired')
                return True
            try:
                result = fetch(wall, '/api/integration/v1/photos/exchange', os.environ['CPC_SYNC_TOKEN'], {'code': code, 'verifier': row['verifier']})
                if result.get('authority_id') != os.environ['OJ_WALL_AUTHORITY_ID']:
                    raise ValueError('身份来源不匹配')
                uuid.UUID(result['subject'])
            except (OSError, ValueError, KeyError, TypeError):
                handler._send_redirect('/photos?auth=expired')
                return True
            with service.db() as db:
                claim = db.execute("select id from cpc_claims where client=? and subject=? and status='approved'", (result['authority_id'], result['subject'])).fetchone()
                if not claim:
                    handler._send_redirect('/photos?auth=unverified')
                    return True
                token = secrets.token_urlsafe(32)
                db.execute('delete from photo_sessions where hash=?', (digest(cookie_value(handler, 'dlut_photos')),))
                db.execute('insert into photo_sessions values (?,?,?,?)', (digest(token), claim['id'], int(time.time()) + 8 * 3600, secrets.token_urlsafe(32)))
            handler.send_response(302)
            handler.send_header('Location', '/photos')
            handler.send_header('Set-Cookie', f'dlut_photos={token}; Path=/api/photos/; HttpOnly; Secure; SameSite=Lax; Max-Age=28800')
            handler.send_header('Set-Cookie', 'dlut_photo_state=; Path=/api/photos/; HttpOnly; Secure; SameSite=Lax; Max-Age=0')
            handler.send_header('Cache-Control', 'no-store')
            handler.send_header('Referrer-Policy', 'no-referrer')
            handler.send_header('Content-Length', '0')
            handler.end_headers()
            return True
        if path.startswith('/api/admin/photos/'):
            if not admin:
                respond({'error': '请先以管理员身份登录'}, 401)
                return True
            if post and (not same_origin(handler) or not hmac.compare_digest(handler.headers.get('X-CSRF-Token', '').encode(), admin['csrf'].encode())):
                respond({'error': '登录验证已失效，请刷新页面'}, 403)
                return True
            if not post and path == '/api/admin/photos/albums':
                album = query.get('album', [''])[0]
                result = service.album(album, int(query.get('page', ['1'])[0]), True) if album else {'albums': service.albums(True)}
                if result:
                    result['storage'] = os.environ.get('PHOTO_STORAGE', 'local')
                respond(result or {'error': '相册不存在'}, 200 if result else 404)
            elif post and path == '/api/admin/photos/album':
                respond(service.save_album(handler._read_json()))
            elif post and path == '/api/admin/photos/upload':
                # Read only after authentication, CSRF and Origin checks.
                if handler.headers.get_content_type() != 'image/jpeg' and handler.headers.get_content_type() not in {'image/png', 'image/webp'}:
                    raise ValueError('请上传 JPEG、PNG 或 WebP 照片')
                length = int(handler.headers.get('Content-Length', '0'))
                if not 0 < length <= 12 * 1024 * 1024:
                    respond({'error': '每张照片不超过 12 MB'}, 413)
                    handler.close_connection = True
                    return True
                caption = query.get('caption', [''])[0]
                respond(service.upload(query.get('album', [''])[0], caption, handler.rfile.read(length)))
            elif post and path == '/api/admin/photos/remove':
                photo = handler._read_json().get('id', '')
                uuid.UUID(photo)
                with service.db() as db:
                    remote = db.execute('select 1 from photo_blobs where photo=?', (photo,)).fetchone()
                if remote:
                    store = service.remote_store()
                    if not store:
                        raise StorageUnavailable('私有照片存储尚未配置')
                    store.remove(photo)
                with service.db() as db:
                    db.execute('delete from competition_photos where id=?', (photo,))
                    db.execute('delete from photo_blobs where photo=?', (photo,))
                service.file(photo).unlink(missing_ok=True)
                service.file(photo, True).unlink(missing_ok=True)
                respond({'ok': True})
            else:
                respond({'error': 'not found'}, 404)
            return True
        if not identity and not admin:
            respond({'error': '请使用已通过成员认证的 OJ Wall 账号登录'}, 401)
            return True
        if post:
            if path != '/api/photos/logout' or not same_origin(handler) or not identity or not hmac.compare_digest(handler.headers.get('X-CSRF-Token', '').encode(), identity['csrf'].encode()):
                respond({'error': '请求无效'}, 403)
                return True
            with service.db() as db:
                db.execute('delete from photo_sessions where hash=?', (digest(cookie_value(handler, 'dlut_photos')),))
            respond({'ok': True}, headers={'Set-Cookie': 'dlut_photos=; Path=/api/photos/; HttpOnly; Secure; SameSite=Lax; Max-Age=0'})
        elif path == '/api/photos/albums':
            respond({'albums': service.albums()})
        elif path == '/api/photos/album':
            result = service.album(query.get('id', [''])[0], int(query.get('page', ['1'])[0]))
            respond(result or {'error': '相册不存在'}, 200 if result else 404)
        elif re.fullmatch(r'/api/photos/image/[0-9a-f-]{36}', path):
            photo = path.rsplit('/', 1)[1]
            with service.db() as db:
                row = db.execute('select p.id from competition_photos p join photo_albums a on a.id=p.album where p.id=? and (? or a.published=1)', (photo, bool(admin))).fetchone()
                blob = db.execute('select * from photo_blobs where photo=? and variant=?',
                                  (photo, 'thumb' if query.get('thumb', [''])[0] == '1' else 'full')).fetchone()
            if not row:
                respond({'error': '照片不存在'}, 404)
                return True
            data = None
            file = None
            if blob:
                store = service.remote_store()
                if not store or blob['repository'] != store.repository:
                    raise StorageUnavailable('私有照片存储尚未配置')
                if getattr(handler, '_head_only', False):
                    store.ensure_private()
                    length = blob['size']
                else:
                    data = store.get(dict(blob))
                    length = len(data)
            else:
                if service.remote_store():
                    raise StorageUnavailable('照片尚未迁移至私有存储')
                file = service.file(photo, query.get('thumb', [''])[0] == '1')
                if not file.is_file():
                    respond({'error': '照片不存在'}, 404)
                    return True
                length = file.stat().st_size
            handler.send_response(200)
            for name, value in [('Content-Type', 'image/jpeg'), ('Content-Length', str(length)),
                                ('Cache-Control', 'private, no-store'), ('Vary', 'Cookie'), ('X-Content-Type-Options', 'nosniff'),
                                ('Cross-Origin-Resource-Policy', 'same-origin'), ('Referrer-Policy', 'no-referrer')]:
                handler.send_header(name, value)
            handler.end_headers()
            if not getattr(handler, '_head_only', False):
                if data is not None:
                    handler.wfile.write(data)
                else:
                    with file.open('rb') as source:
                        while chunk := source.read(128 * 1024):
                            handler.wfile.write(chunk)
        else:
            respond({'error': 'not found'}, 404)
    except (ValueError, TypeError, KeyError, OverflowError):
        respond({'error': '请求无效，请检查相册、日期或照片格式'}, 400)
    except StorageUnavailable as exc:
        respond({'error': str(exc)}, 503)
    except (OSError, sqlite3.Error):
        respond({'error': '照片服务暂不可用，请稍后重试'}, 503)
    return True
