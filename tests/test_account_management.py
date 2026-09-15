import copy
import tempfile
import unittest
from pathlib import Path

from database import Database, SCHEMA_VERSION


class AccountManagementTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.database = Database(Path(self.temporary.name) / "site.sqlite3")
        self.identity = {"name": "杨君泓", "provider": "manual", "externalId": "yang"}
        self.seed = {"meta": {}, "publicMembers": [{**self.identity, "handles": {
            "codeforces": {"handle": "Old", "verified": True, "rating": 2400, "maxRating": 2600,
                           "ratingUpdatedAt": "2026-09-15T00:00:00+00:00"}}}],
            "accountBindings": [{**self.identity, "accounts": {"codeforces": [
                {"handle": "Old", "verified": True}, {"handle": "Secondary", "verified": True,
                 "rating": 1900, "maxRating": 2200, "ratingUpdatedAt": "2026-09-15T00:00:00+00:00"}]}}]}
        self.database.initialize(self.seed)
        self.member_id = self.member()["id"]

    def tearDown(self):
        self.temporary.cleanup()

    def member(self):
        return next(item for item in self.database.payload(self.seed)["members"] if item["name"] == "杨君泓")

    def handles(self):
        return {item["handle"] for item in self.member().get("accounts", {}).get("codeforces", [])}

    def test_edit_does_not_transfer_ratings_and_survives_all_seed_sources(self):
        self.database.edit_handle(self.member_id, "codeforces", "old", "Farewell")
        self.database.initialize(self.seed)
        self.assertEqual(self.handles(), {"Farewell", "Secondary"})
        self.assertNotIn("accountBindings", self.database.payload(self.seed))
        farewell = next(item for item in self.member()["accounts"]["codeforces"] if item["handle"] == "Farewell")
        self.assertIsNone(farewell["rating"])
        self.assertIsNone(farewell["maxRating"])
        self.database.update_account_ratings([{"handle": "Farewell", "rating": 1551, "maxRating": 1595}],
                                            "2026-09-16T00:00:00+00:00")
        self.assertEqual(self.member()["handles"]["codeforces"]["handle"], "Secondary")

    def test_delete_promotes_secondary_and_can_delete_last_account(self):
        self.database.delete_handle(self.member_id, "CODEFORCES", "old")
        self.database.initialize(self.seed)
        self.assertEqual(self.member()["handles"]["codeforces"]["handle"], "Secondary")
        self.database.delete_handle(self.member_id, "codeforces", "Secondary")
        self.database.initialize(self.seed)
        self.assertEqual(self.handles(), set())

    def test_explicit_readd_restores_a_deleted_account(self):
        self.database.delete_handle(self.member_id, "codeforces", "Old")
        self.database.set_handle(self.member_id, "codeforces", "old")
        self.database.initialize(self.seed)
        self.assertEqual({item.lower() for item in self.handles()}, {"old", "secondary"})

    def test_conflicting_edit_rolls_back_old_account_and_removal_record(self):
        other = self.database.add_manual_member("其他成员")
        self.database.set_handle(other, "codeforces", "Taken")
        with self.assertRaises(ValueError):
            self.database.edit_handle(self.member_id, "codeforces", "Old", "taken")
        self.assertEqual(self.handles(), {"Old", "Secondary"})
        self.assertEqual(self.member()["handles"]["codeforces"]["rating"], 2400)
        with self.database.connect() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM removed_member_handles").fetchone()[0], 0)

    def test_missing_or_other_members_account_cannot_be_edited_or_deleted(self):
        other = self.database.add_manual_member("其他成员")
        for member_id in (other, 999999):
            with self.assertRaises(ValueError):
                self.database.delete_handle(member_id, "codeforces", "Old")
            with self.assertRaises(ValueError):
                self.database.edit_handle(member_id, "codeforces", "Old", "New")
        self.assertEqual(self.handles(), {"Old", "Secondary"})

    def test_case_only_edit_retains_the_same_accounts_rating(self):
        self.database.edit_handle(self.member_id, "codeforces", "old", "OLD")
        self.database.initialize(self.seed)
        self.assertEqual(self.handles(), {"OLD", "Secondary"})
        self.assertEqual(self.member()["handles"]["codeforces"]["maxRating"], 2600)

    def test_one_time_correction_upgrades_existing_database_without_reviving_admin_edits(self):
        updated = copy.deepcopy(self.seed)
        updated["publicMembers"][0].pop("handles")
        updated["accountBindings"][0]["accounts"]["codeforces"][0]["handle"] = "Farewell"
        updated["accountCorrections"] = [{**self.identity, "id": "fix-yang", "platform": "codeforces", "removeHandles": ["Old"]}]
        self.database.initialize(updated)
        self.assertEqual(self.handles(), {"Farewell", "Secondary"})
        self.database.edit_handle(self.member_id, "codeforces", "Farewell", "Later")
        self.database.initialize(updated)
        self.database.initialize(self.seed)
        self.assertEqual(self.handles(), {"Later", "Secondary"})
        self.assertNotIn("accountCorrections", self.database.payload(updated))
        with self.database.connect() as connection:
            self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], SCHEMA_VERSION)

    def test_invalid_replacement_preserves_old_account(self):
        for handle in ("", " ", "a;b"):
            with self.assertRaises(ValueError):
                self.database.edit_handle(self.member_id, "codeforces", "Old", handle)
            self.assertEqual(self.handles(), {"Old", "Secondary"})
