import tempfile
import unittest
import uuid
from pathlib import Path
from cpc_integration import Integration
from database import Database
from test_database import seed_data


class CpcTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.database=Database(Path(self.temp.name)/'db.sqlite')
        self.database.initialize(seed_data())
        self.service=Integration(self.database)

    def tearDown(self):
        self.temp.cleanup()

    def request(self, subject=None):
        return {'id':str(uuid.uuid4()),'client':str(uuid.uuid4()),
                'subject':subject or str(uuid.uuid4()),'person':self.service.roster()['members'][0]['id'],
                'account_name':'fixture','note':'人工核验'}

    def test_uuid_is_stable_and_members_are_per_contest(self):
        before=self.service.roster()
        after=Integration(self.database).roster()
        self.assertEqual(before,after)
        self.assertEqual(len(before['participations'][0]['members']),3)

    def test_idempotency_rejection_and_conflicting_person_approval(self):
        first=self.request();second=self.request()
        self.service.submit(first);self.service.submit(first);self.service.submit(second)
        self.service.review(first['id'],'approved','admin')
        with self.assertRaises(ValueError):self.service.review(second['id'],'approved','admin')
        self.service.review(first['id'],'revoked','admin')
        self.service.review(second['id'],'approved','admin')
        rows=self.service.claims(second['client'])['claims']
        self.assertEqual(rows[0]['status'],'approved')
        self.assertNotIn('note',rows[0])

    def test_missing_or_unknown_person_cannot_be_claimed(self):
        body=self.request();body['person']=str(uuid.uuid4())
        with self.assertRaises(ValueError):self.service.submit(body)
