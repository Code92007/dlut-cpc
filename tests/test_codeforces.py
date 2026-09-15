import tempfile
import unittest
from pathlib import Path

from database import Database
from tools.sync_codeforces import sync_ratings, validate_response


class CodeforcesTests(unittest.TestCase):
    def test_response_maps_by_handle_not_array_order(self):
        response = {"status": "OK", "result": [{"handle": "B", "rating": 1500, "maxRating": 2100}, {"handle": "A", "rating": 1800, "maxRating": 1800}]}
        users = validate_response(response, ["a", "b"])
        self.assertEqual([user["handle"] for user in users], ["A", "B"])
        self.assertEqual(users[1]["maxRating"], 2100)

    def test_invalid_incomplete_or_wrong_identity_responses_are_rejected(self):
        for response in ({"status": "FAILED", "comment": "not found"},
                         {"status": "OK", "result": []},
                         {"status": "OK", "result": [{"handle": "B", "rating": 1800}]},
                         {"status": "OK", "result": [{"handle": "A", "rating": "1800"}]}):
            with self.assertRaises(ValueError):
                validate_response(response, ["A"])

    def test_unrated_is_null_not_zero_and_manual_secondary_is_refreshed(self):
        with tempfile.TemporaryDirectory() as temporary:
            seed = {"meta": {}, "honors": [], "training": [], "accountBindings": [
                {"name": "Test", "createMember": True, "accounts": {"codeforces": [{"handle": "A", "verified": True}]}}]}
            database = Database(Path(temporary) / "site.sqlite3")
            database.initialize(seed)
            member_id = database.payload(seed)["members"][0]["id"]
            database.set_handle(member_id, "codeforces", "B")
            sync_ratings(database, seed, response={"status": "OK", "result": [
                {"handle": "A"}, {"handle": "B", "rating": 1500, "maxRating": 2000}]})
            member = database.payload(seed)["members"][0]
            self.assertEqual(member["handles"]["codeforces"]["handle"], "B")
            self.assertIsNone(member["accounts"]["codeforces"][1]["rating"])
            self.assertIsNone(seed["accountBindings"][0]["accounts"]["codeforces"][0]["rating"])
            self.assertEqual(len(member["accounts"]["codeforces"]), 2)
