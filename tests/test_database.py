import tempfile
import unittest
from pathlib import Path

from database import Database


def source(name="CPC Finder", url="https://example.com/source"):
    return {"name": name, "url": url}


def seed_data():
    return {
        "meta": {"school": "大连理工大学", "updatedAt": "2026-09-14"},
        "medalSummary": [],
        "ratingGroups": [{"name": "旧的不完整名单", "members": []}],
        "training": [],
        "honors": [
            {
                "id": "award-1",
                "event": "ICPC 测试站",
                "series": "ICPC",
                "date": "2025-11-01",
                "location": "测试",
                "team": "第一队",
                "members": ["张三", "李四", "王五"],
                "memberDetails": [
                    {"name": "张三", "provider": "cpcfinder", "externalId": "student-1"},
                    {"name": "李四", "provider": "cpcfinder", "externalId": "student-2"},
                    {"name": "王五", "provider": "cpcfinder", "externalId": "student-3"},
                ],
                "memberSource": source("CPC Finder 赛事榜单"),
                "medal": "金牌",
                "rank": "1 / 100",
                "overallRank": "1 / 120",
                "source": source(),
            }
        ],
    }


class DatabaseTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.path = Path(self.temporary.name) / "site.sqlite3"
        self.database = Database(self.path)
        self.seed = seed_data()
        self.database.initialize(self.seed)

    def tearDown(self):
        self.temporary.cleanup()

    def test_bootstrap_builds_full_member_directory_and_drops_legacy_groups(self):
        payload = self.database.payload(self.seed)
        self.assertEqual(len(payload["members"]), 3)
        self.assertEqual(payload["meta"]["memberCoverage"], 100)
        self.assertEqual(payload["honors"][0]["members"], ["张三", "李四", "王五"])
        self.assertNotIn("notes", payload["members"][0])
        self.assertNotIn("ratingGroups", payload)

    def test_manual_member_and_handle_survive_automatic_resync(self):
        member_id = self.database.add_manual_member(
            "远古成员",
            entry_year=2007,
            graduation_year=2011,
            source=source("队史补录", "https://example.com/archive"),
        )
        self.database.set_handle(member_id, "codeforces", "legacy_handle", rating=2100)
        self.database.initialize(self.seed)
        member = next(item for item in self.database.payload(self.seed)["members"] if item["id"] == member_id)
        self.assertTrue(member["manual"])
        self.assertEqual(member["firstYear"], 2007)
        self.assertEqual(member["handles"]["codeforces"]["handle"], "legacy_handle")

    def test_external_ids_keep_same_name_people_separate(self):
        second_seed = seed_data()
        second_seed["honors"].append(
            {
                **second_seed["honors"][0],
                "id": "award-2",
                "date": "2024-11-01",
                "team": "第二队",
                "memberDetails": [
                    {"name": "张三", "provider": "cpcfinder", "externalId": "different-student"}
                ],
                "members": ["张三"],
            }
        )
        other_path = Path(self.temporary.name) / "same-name.sqlite3"
        other_database = Database(other_path)
        other_database.initialize(second_seed)
        same_name = [item for item in other_database.payload(second_seed)["members"] if item["name"] == "张三"]
        self.assertEqual(len(same_name), 2)

    def test_manual_honor_can_link_existing_and_manual_members(self):
        member_id = self.database.add_manual_member("老队员", entry_year=2005)
        honor_id = self.database.add_manual_honor(
            {
                "event": "历史赛事",
                "series": "ICPC",
                "date": "2008-10-01",
                "location": "大连",
                "team": "历史队伍",
                "medal": "银牌",
                "source": source("队史补录"),
            }
        )
        self.database.link_member(honor_id, member_id)
        self.database.initialize(self.seed)
        honor = next(item for item in self.database.payload(self.seed)["honors"] if item["id"] == honor_id)
        self.assertEqual(honor["members"], ["老队员"])
        self.assertTrue(honor["manual"])


if __name__ == "__main__":
    unittest.main()
