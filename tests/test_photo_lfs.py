import hashlib
import json
import os
import subprocess
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import MagicMock, patch

from photo_lfs import GitHubLFS, StorageUnavailable, _private_until


class LFSTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.root = Path(self.temp.name)
        self.env = patch.dict(os.environ, {'PHOTO_LFS_REPOSITORY': 'fixture/private-photos'})
        self.env.start(); self.store = GitHubLFS(self.root)
        self.store.key.write_text('fixture key'); self.store.known_hosts.write_text('fixture host')
        _private_until.clear()

    def tearDown(self):
        self.env.stop(); self.temp.cleanup(); _private_until.clear()

    def test_public_repo_and_untrusted_transfer_hosts_are_rejected(self):
        opener = MagicMock()
        with patch('photo_lfs.build_opener', return_value=opener):
            with self.assertRaises(StorageUnavailable): self.store.ensure_private()
        for url in ['http://github.com/data', 'https://github.com.evil.example/data', 'https://127.0.0.1/data', 'https://user@github.com/data']:
            with self.subTest(url=url), self.assertRaises(StorageUnavailable): self.store._http(url)

    def test_download_checks_length_hash_and_keeps_remote_urls_out_of_result(self):
        raw = b'fixture photo data'
        record = {'oid': hashlib.sha256(raw).hexdigest(), 'size': len(raw)}
        batch = [{'actions': {'download': {'href': 'https://objects.githubusercontent.com/private?token=secret', 'header': {'Authorization': 'scoped'}}}}]
        with patch.object(self.store, 'batch', return_value=batch), patch.object(self.store, '_http', return_value=raw):
            self.assertEqual(self.store.get(record), raw)
        with patch.object(self.store, 'batch', return_value=batch), patch.object(self.store, '_http', return_value=b'tampered'):
            with self.assertRaises(StorageUnavailable): self.store.get(record)

    def test_upload_uses_batch_actions_and_commits_only_pointers(self):
        full, thumb = b'full image bytes', b'thumbnail bytes'
        def batch(operation, records):
            self.assertEqual(operation, 'upload')
            return [{**r, 'actions': {'upload': {'href': 'https://github-cloud.s3.amazonaws.com/upload', 'header': {}},
                                    'verify': {'href': 'https://lfs.github.com/verify', 'header': {}}}} for r in records]
        with patch.object(self.store, 'batch', side_effect=batch), patch.object(self.store, '_http') as http, patch.object(self.store, 'commit_pointers', return_value='commit') as commit:
            records = self.store.put(str(uuid.uuid4()), full, thumb)
        self.assertEqual(http.call_count, 4)
        pointers = commit.call_args.args[0]
        self.assertTrue(all(b'version https://git-lfs.github.com/spec/v1' in data for name, data in pointers.items() if name.endswith('.jpg')))
        self.assertFalse(any(full in data or thumb in data for data in pointers.values()))
        self.assertEqual(records['full']['oid'], hashlib.sha256(full).hexdigest())

    def test_real_git_cache_contains_only_small_pointers_and_supports_removal(self):
        remote = self.root / 'upstream.git'
        subprocess.run(['git', 'init', '--bare', '--initial-branch=main', str(remote)], check=True, capture_output=True)
        process_env = {**os.environ, 'GIT_AUTHOR_NAME': 'Fixture', 'GIT_AUTHOR_EMAIL': 'fixture@example.invalid',
                       'GIT_COMMITTER_NAME': 'Fixture', 'GIT_COMMITTER_EMAIL': 'fixture@example.invalid'}
        def upstream(*args, data=None):
            return subprocess.check_output(['git', '--git-dir=' + str(remote), *args], input=data, env=process_env).decode().strip()
        tree = upstream('mktree', data=b''); initial = upstream('commit-tree', tree, data=b'Initialize\n')
        upstream('update-ref', 'refs/heads/main', initial)
        real_git = self.store._git
        def local_git(*args, **kwargs):
            return real_git(*(str(remote) if a == 'git@github.com:fixture/private-photos.git' else a for a in args), **kwargs)
        photo = str(uuid.uuid4()); path = 'photos/' + photo + '.jpg'
        pointer = self.store.pointer({'oid': 'f' * 64, 'size': 7000000})
        with patch.object(self.store, '_git', side_effect=local_git):
            commit = self.store.commit_pointers({path: pointer}, 'Add fixture pointer\n')
            self.assertEqual(upstream('show', commit + ':' + path), pointer.decode().strip())
            self.assertFalse((self.store.git_dir / 'lfs').exists())
            self.assertLess(sum(p.stat().st_size for p in self.store.git_dir.rglob('*') if p.is_file()), 100_000)
            removed = self.store.commit_pointers({path: None}, 'Remove fixture pointer\n')
            self.assertEqual(upstream('ls-tree', '-r', '--name-only', removed), '')


if __name__ == '__main__': unittest.main()
