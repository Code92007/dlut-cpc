import tempfile
import unittest
from pathlib import Path
from database import Database


class TrainingPortalTests(unittest.TestCase):
    def test_hidden_and_edited_seed_links_survive_restart_without_duplicates(self):
        with tempfile.TemporaryDirectory() as temporary:
            db = Database(Path(temporary) / 'site.sqlite3')
            seed = {'meta': {}, 'honors': [], 'training': [{'title': 'Old standings'}], 'trainingLinks': [
                {'seedId': 'video-v1', 'title': 'Lectures', 'url': 'https://space.bilibili.com/396380763', 'kind': 'video'}]}
            db.initialize(seed)
            link = db.list_training_links()[0]
            db.save_training_link({**link, 'title': 'Updated lectures', 'published': False})
            db.initialize(seed)
            self.assertEqual(db.list_training_links(), [])
            self.assertEqual(len(db.list_training_links(include_drafts=True)), 1)
            self.assertEqual(db.list_training_links(include_drafts=True)[0]['title'], 'Updated lectures')
            payload = db.payload(seed)
            self.assertEqual(payload['trainingLinks'], [])
            self.assertEqual(payload['training'], [])

    def test_invalid_urls_and_ids_do_not_modify_training_entries(self):
        with tempfile.TemporaryDirectory() as temporary:
            db = Database(Path(temporary) / 'site.sqlite3')
            db.initialize()
            item = {'title': 'OJ', 'kind': 'oj', 'url': 'https://hydro.ac/d/ssdut/'}
            for change in ({'url': 'javascript:alert(1)'}, {'url': 'https://'}, {'kind': 'unknown'},
                           {'published': 'yes'}, {'sortOrder': True}, {'id': 999}, {'id': True}, {'title': ''}):
                with self.assertRaises(ValueError):
                    db.save_training_link({**item, **change})
            self.assertEqual(db.list_training_links(include_drafts=True), [])
            db.save_training_link({**item, 'sortOrder': 10})
            db.save_training_link({**item, 'title': 'First', 'sortOrder': 1})
            self.assertEqual([entry['title'] for entry in db.list_training_links()], ['First', 'OJ'])
