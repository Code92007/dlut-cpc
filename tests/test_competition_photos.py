import io
import json
import os
import secrets
import tempfile
import threading
import time
import unittest
import uuid
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
from unittest.mock import MagicMock

from PIL import Image
import app
from competition_photos import Photos, digest
from photo_lfs import StorageUnavailable
from database import Database
from test_database import seed_data


class PhotoAccessTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / 'db.sqlite'
        self.env = patch.dict(os.environ, {'PHOTO_PUBLIC_URL': 'https://cpc.example', 'OJ_WALL_URL': 'https://wall.example',
                                          'OJ_WALL_AUTHORITY_ID': str(uuid.uuid4()), 'CPC_SYNC_TOKEN': 'private-service-secret',
                                          'COMPETITION_PHOTOS_PATH': str(Path(self.temp.name) / 'photos')})
        self.env.start()
        self.db_patch = patch.object(app, 'DATABASE_PATH', self.path)
        self.db_patch.start()
        Database(self.path).initialize(seed_data())
        self.service = Photos(Database(self.path))
        self.subject = str(uuid.uuid4())
        claim = {'id': str(uuid.uuid4()), 'client': os.environ['OJ_WALL_AUTHORITY_ID'], 'subject': self.subject,
                 'person': self.service.integration.roster()['members'][0]['id'], 'account_name': 'member', 'note': '核验说明'}
        self.service.integration.submit(claim)
        self.service.integration.review(claim['id'], 'approved', 'admin')
        self.claim = claim['id']
        self.token = secrets.token_urlsafe(32)
        with self.service.db() as db:
            db.execute('insert into photo_sessions values (?,?,?,?)', (digest(self.token), self.claim, int(time.time()) + 600, 'photo-csrf'))
        self.album = self.service.save_album({'title': '测试比赛', 'date': '2026-11-19', 'published': True})['id']
        source = Image.new('RGB', (50, 40), '#336699')
        raw = io.BytesIO()
        exif = Image.Exif(); exif[270] = 'private source metadata'
        source.save(raw, 'JPEG', exif=exif)
        self.raw = raw.getvalue()
        self.photo = self.service.upload(self.album, '赛场照片', self.raw)['id']
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), app.SiteHandler)
        self.server.admin_auth = type('Auth', (), {'session': lambda _, cookie: {'username': 'admin', 'csrf': 'admin-csrf'} if 'dlut_admin=admin' in cookie else None})()
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join()
        self.db_patch.stop(); self.env.stop(); self.temp.cleanup()

    def request(self, path, method='GET', cookie='', body=None, headers=None):
        conn = HTTPConnection('127.0.0.1', self.server.server_port)
        conn.request(method, path, body=body, headers={'Cookie': cookie, **(headers or {})})
        response = conn.getresponse()
        result = response.status, dict(response.getheaders()), response.read()
        conn.close()
        return result

    @property
    def cookie(self):
        return 'dlut_photos=' + self.token

    def test_lists_thumbnails_full_images_and_head_require_authentication(self):
        for path in ['/api/photos/albums', '/api/photos/album?id=' + self.album,
                     '/api/photos/image/' + self.photo, '/api/photos/image/' + self.photo + '?thumb=1']:
            for method in ['GET', 'HEAD']:
                status, headers, body = self.request(path, method)
                self.assertEqual(status, 401)
                self.assertIn('no-store', headers['Cache-Control'])
                if method == 'HEAD': self.assertEqual(body, b'')
        self.assertEqual(self.request('/competition_photos/' + self.photo + '.jpg')[0], 404)

    def test_approved_member_reads_image_and_revocation_takes_effect_immediately(self):
        status, headers, body = self.request('/api/photos/image/' + self.photo, cookie=self.cookie)
        self.assertEqual(status, 200)
        self.assertEqual(headers['Content-Type'], 'image/jpeg')
        self.assertEqual(headers['Cache-Control'], 'private, no-store')
        self.assertEqual(headers['Cross-Origin-Resource-Policy'], 'same-origin')
        self.assertFalse(Image.open(io.BytesIO(body)).getexif())
        self.service.integration.review(self.claim, 'revoked', 'admin')
        self.assertEqual(self.request('/api/photos/image/' + self.photo, cookie=self.cookie)[0], 401)

    def test_hidden_album_protects_direct_image_and_admin_can_preview(self):
        self.service.save_album({'id': self.album, 'title': '隐藏比赛', 'date': '2026-11-19', 'published': False})
        self.assertEqual(json.loads(self.request('/api/photos/albums', cookie=self.cookie)[2])['albums'], [])
        self.assertEqual(self.request('/api/photos/album?id=' + self.album, cookie=self.cookie)[0], 404)
        self.assertEqual(self.request('/api/photos/image/' + self.photo, cookie=self.cookie)[0], 404)
        self.assertEqual(self.request('/api/photos/image/' + self.photo, cookie='dlut_admin=admin')[0], 200)

    def test_upload_is_admin_only_and_requires_origin_and_csrf(self):
        path = '/api/admin/photos/upload?album=' + self.album
        headers = {'Content-Type': 'image/jpeg', 'Origin': 'https://cpc.example', 'X-CSRF-Token': 'admin-csrf'}
        self.assertEqual(self.request(path, 'POST', self.cookie, self.raw, headers)[0], 401)
        self.assertEqual(self.request(path, 'POST', 'dlut_admin=admin', self.raw, {**headers, 'Origin': 'https://evil.example'})[0], 403)
        self.assertEqual(self.request(path, 'POST', 'dlut_admin=admin', self.raw, {**headers, 'X-CSRF-Token': 'wrong'})[0], 403)
        self.assertEqual(self.request(path, 'POST', 'dlut_admin=admin', b'<svg/>', headers)[0], 400)
        status, _, body = self.request(path, 'POST', 'dlut_admin=admin', self.raw, headers)
        self.assertEqual(status, 200)
        photo = json.loads(body)['id']
        self.assertTrue(self.service.file(photo, True).is_file())
        status, _, _ = self.request('/api/admin/photos/remove', 'POST', 'dlut_admin=admin', json.dumps({'id': photo}), {**headers, 'Content-Type': 'application/json'})
        self.assertEqual(status, 200)
        self.assertFalse(self.service.file(photo).exists())
        self.assertEqual(self.request('/api/photos/image/' + photo, cookie=self.cookie)[0], 404)

    def test_callback_checks_state_authority_approval_and_cannot_replay(self):
        status, headers, _ = self.request('/api/photos/login')
        self.assertEqual(status, 302)
        self.assertIn('code_challenge=', headers['Location'])
        state = headers['Set-Cookie'].split(';')[0].split('=')[1]
        path = '/api/photos/callback?state=' + state + '&code=single-use-code'
        with patch('competition_photos.fetch', return_value={'authority_id': os.environ['OJ_WALL_AUTHORITY_ID'], 'subject': self.subject}) as exchange:
            self.assertIn('expired', self.request(path)[1]['Location'])
            exchange.assert_not_called()
            status, headers, _ = self.request(path, cookie='dlut_photo_state=' + state)
            self.assertEqual((status, headers['Location']), (302, '/photos'))
            self.assertIn('HttpOnly', headers['Set-Cookie'])
            self.assertIn('expired', self.request(path, cookie='dlut_photo_state=' + state)[1]['Location'])
            self.assertEqual(exchange.call_count, 1)
        self.service.integration.review(self.claim, 'revoked', 'admin')
        _, headers, _ = self.request('/api/photos/login')
        state = headers['Set-Cookie'].split(';')[0].split('=')[1]
        with patch('competition_photos.fetch', return_value={'authority_id': os.environ['OJ_WALL_AUTHORITY_ID'], 'subject': self.subject}):
            _, headers, _ = self.request('/api/photos/callback?state=' + state + '&code=code', cookie='dlut_photo_state=' + state)
            self.assertEqual(headers['Location'], '/photos?auth=unverified')
            self.assertNotIn('Set-Cookie', headers)

    def test_expiry_logout_and_untrusted_authority_fail_closed(self):
        with self.service.db() as db:
            db.execute('update photo_sessions set expires=0')
        self.assertEqual(self.request('/api/photos/albums', cookie=self.cookie)[0], 401)
        _, headers, _ = self.request('/api/photos/login')
        state = headers['Set-Cookie'].split(';')[0].split('=')[1]
        with patch('competition_photos.fetch', return_value={'authority_id': str(uuid.uuid4()), 'subject': self.subject}):
            self.assertEqual(self.request('/api/photos/callback?state=' + state + '&code=x', cookie='dlut_photo_state=' + state)[1]['Location'], '/photos?auth=expired')

    def test_remote_upload_and_read_leave_no_local_image_and_auth_precedes_storage(self):
        import hashlib
        store = MagicMock(repository='fixture/private-photos')
        blobs = {}
        def upload(photo, full, thumb):
            result = {}
            for variant, data in [('full', full), ('thumb', thumb)]:
                oid = hashlib.sha256(data).hexdigest(); blobs[oid] = data
                result[variant] = {'oid': oid, 'size': len(data), 'commit': 'fixture-commit'}
            return result
        store.put.side_effect = upload
        store.get.side_effect = lambda row: blobs[row['oid']]
        before = sorted(self.service.root.iterdir())
        with patch.dict(os.environ, {'PHOTO_STORAGE': 'github-lfs'}), patch('competition_photos.GitHubLFS', return_value=store):
            photo = self.service.upload(self.album, 'remote only', self.raw)['id']
            self.assertEqual(sorted(self.service.root.iterdir()), before)
            self.assertFalse(self.service.file(photo).exists())
            self.assertEqual(self.request('/api/photos/image/' + photo)[0], 401)
            store.get.assert_not_called()
            status, headers, data = self.request('/api/photos/image/' + photo, cookie=self.cookie)
            self.assertEqual(status, 200)
            self.assertNotIn('Location', headers)
            self.assertEqual(headers['Cache-Control'], 'private, no-store')
            self.assertTrue(data.startswith(b'\xff\xd8'))
            self.assertEqual(self.request('/api/photos/image/' + photo + '?thumb=1', cookie=self.cookie)[0], 200)
            store.get.side_effect = StorageUnavailable('private remote unavailable')
            self.assertEqual(self.request('/api/photos/image/' + photo, cookie=self.cookie)[0], 503)
            store.put.side_effect = StorageUnavailable('private remote unavailable')
            with self.assertRaises(StorageUnavailable): self.service.upload(self.album, '', self.raw)
            self.assertEqual(sorted(self.service.root.iterdir()), before)
            self.service.integration.review(self.claim, 'revoked', 'admin')
            store.get.reset_mock()
            self.assertEqual(self.request('/api/photos/image/' + photo, cookie=self.cookie)[0], 401)
            store.get.assert_not_called()


if __name__ == '__main__':
    unittest.main()
