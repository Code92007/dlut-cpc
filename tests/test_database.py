import tempfile
import sqlite3
import unittest
from pathlib import Path

from database import Database, SCHEMA, SCHEMA_VERSION


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

    def test_public_directory_adds_all_students_and_uses_provider_medal_totals(self):
        enriched_seed = seed_data()
        enriched_seed["publicMembers"] = [
            {
                "name": "张三",
                "provider": "cpcfinder",
                "externalId": "student-1",
                "cpcfinder": {
                    "rating": 1182.0127,
                    "rank": 1,
                    "goldCount": 2,
                    "silverCount": 6,
                    "bronzeCount": 2,
                    "ironCount": 4,
                    "latestEventDate": "2023-03-25",
                },
                "source": source("CPC Finder 选手库", "https://cpcfinder.com/student/student-1"),
            },
            {
                "name": "未获奖成员",
                "provider": "cpcfinder",
                "externalId": "student-4",
                "cpcfinder": {
                    "rating": 321.5,
                    "rank": 154,
                    "goldCount": 0,
                    "silverCount": 0,
                    "bronzeCount": 0,
                    "ironCount": 0,
                    "latestEventDate": "2025-07-01",
                },
                "source": source("CPC Finder 选手库", "https://cpcfinder.com/student/student-4"),
            },
        ]

        self.database.initialize(enriched_seed)
        payload = self.database.payload(enriched_seed)
        zhang = next(item for item in payload["members"] if item["name"] == "张三")
        unawarded = next(item for item in payload["members"] if item["name"] == "未获奖成员")

        self.assertEqual(len(payload["members"]), 4)
        self.assertEqual(zhang["medals"], {"gold": 2, "silver": 6, "bronze": 2, "iron": 4})
        self.assertEqual(zhang["honorCount"], 10)
        self.assertEqual(zhang["cpcfinder"]["rank"], 1)
        self.assertEqual(unawarded["honorCount"], 0)

    def test_iron_counts_actual_members_and_does_not_increase_honor_count(self):
        enriched = seed_data()
        enriched["honors"].append({
            **enriched["honors"][0], "id": "iron-1", "team": "参赛队", "medal": "铁牌",
            "official": False, "members": ["张三"],
            "memberDetails": [{"name": "张三", "provider": "cpcfinder", "externalId": "student-1"}],
        })
        self.database.initialize(enriched)
        payload = self.database.payload(enriched)
        zhang = next(m for m in payload["members"] if m["name"] == "张三")
        li = next(m for m in payload["members"] if m["name"] == "李四")
        self.assertEqual(zhang["medals"]["iron"], 1)
        self.assertEqual(zhang["honorCount"], 1)
        self.assertEqual(li["medals"]["iron"], 0)
        self.assertEqual(payload["medalSummary"][0]["iron"], 1)
        self.assertFalse(next(h for h in payload["honors"] if h["id"] == "iron-1")["official"])

    def test_manual_iron_is_added_to_public_count_and_survives_resync(self):
        enriched = seed_data()
        enriched["publicMembers"] = [{
            "name": "张三", "provider": "cpcfinder", "externalId": "student-1",
            "cpcfinder": {"goldCount": 1, "ironCount": 2}, "source": source(),
        }]
        self.database.initialize(enriched)
        zhang = next(m for m in self.database.payload(enriched)["members"] if m["name"] == "张三")
        honor_id = self.database.add_manual_honor({
            "event": "历史参赛", "date": "2021-11-01", "team": "老队伍", "medal": "铁牌", "source": source("人工录入"),
        })
        self.database.link_member(honor_id, zhang["id"])
        self.database.initialize(enriched)
        zhang = next(m for m in self.database.payload(enriched)["members"] if m["name"] == "张三")
        self.assertEqual(zhang["medals"]["iron"], 3)
        self.assertEqual(zhang["honorCount"], 1)

    def test_v2_migration_preserves_manual_members_and_handles(self):
        old_path = Path(self.temporary.name) / "old.sqlite3"
        old_schema = SCHEMA.replace("    iron_count INTEGER,\n", "").replace("    official INTEGER,\n", "")
        old_schema = old_schema.replace("    display_name TEXT,\n", "").replace("    max_rating INTEGER,\n", "").replace("    rating_updated_at TEXT,\n", "")
        old_schema = old_schema.replace("PRIMARY KEY(member_id, platform, handle)", "PRIMARY KEY(member_id, platform)")
        with sqlite3.connect(old_path) as connection:
            connection.executescript(old_schema)
            connection.execute("INSERT INTO members(id, name, normalized_name, is_manual) VALUES (100, '远古成员', '远古成员', 1)")
            connection.execute("INSERT INTO member_handles(member_id, platform, handle) VALUES (100, 'codeforces', 'legacy')")
            connection.execute("PRAGMA user_version=2")
        migrated = Database(old_path)
        migrated.initialize(self.seed)
        member = next(m for m in migrated.payload(self.seed)["members"] if m["id"] == 100)
        self.assertTrue(member["manual"])
        self.assertEqual(member["handles"]["codeforces"]["handle"], "legacy")
        with migrated.connect() as connection:
            self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], SCHEMA_VERSION)

    def test_multiple_accounts_select_highest_max_rating_not_current_or_insertion_order(self):
        member_id = self.database.payload(self.seed)["members"][0]["id"]
        self.database.set_handle(member_id, "codeforces", "older", rating=2200)
        self.database.set_handle(member_id, "codeforces", "newer", rating=1500)
        self.database.update_account_ratings([
            {"handle": "older", "rating": 2200, "maxRating": 2250},
            {"handle": "newer", "rating": 1500, "maxRating": 2600},
        ], "2099-01-01T00:00:00+00:00")
        member = next(m for m in self.database.payload(self.seed)["members"] if m["id"] == member_id)
        self.assertEqual(member["handles"]["codeforces"]["handle"], "newer")
        self.assertEqual([a["handle"] for a in member["accounts"]["codeforces"]], ["newer", "older"])
        self.database.set_handle(member_id, "codeforces", "NEWER")
        self.database.initialize(self.seed)
        member = next(m for m in self.database.payload(self.seed)["members"] if m["id"] == member_id)
        self.assertEqual(len(member["accounts"]["codeforces"]), 2)
        self.assertEqual(member["handles"]["codeforces"]["rating"], 1500)

    def test_one_account_cannot_be_assigned_to_two_members(self):
        ids = [m["id"] for m in self.database.payload(self.seed)["members"]]
        self.database.set_handle(ids[0], "codeforces", "owner")
        with self.assertRaises(ValueError):
            self.database.set_handle(ids[1], "codeforces", "OWNER")

    def test_manual_account_identity_survives_another_same_name_member(self):
        self.seed["accountBindings"] = [{"name": "Old", "provider": "manual", "externalId": "old-1", "createMember": True,
                                        "accounts": {"codeforces": [{"handle": "old", "verified": True}]}}]
        self.database.initialize(self.seed)
        owner = next(m for m in self.database.payload(self.seed)["members"] if m["name"] == "Old")
        other = self.database.add_manual_member("Old")
        self.database.initialize(self.seed)
        payload = self.database.payload(self.seed)
        self.assertEqual(next(m for m in payload["members"] if m["id"] == owner["id"])["handles"]["codeforces"]["handle"], "old")
        self.assertEqual(next(m for m in payload["members"] if m["id"] == other)["accounts"], {})

    def test_seed_bindings_preserve_newer_db_ratings_and_secondary_accounts(self):
        self.seed["accountBindings"] = [{"provider": "cpcfinder", "externalId": "student-1", "name": "张三",
                                        "accounts": {"codeforces": [{"handle": "primary", "verified": True, "rating": 1800,
                                        "maxRating": 2000, "ratingUpdatedAt": "2025-01-01T00:00:00+00:00"}]}}]
        self.database.initialize(self.seed)
        member = next(m for m in self.database.payload(self.seed)["members"] if m["name"] == "张三")
        self.database.set_handle(member["id"], "codeforces", "secondary")
        self.database.update_account_ratings([{"handle": "primary", "rating": 2100, "maxRating": 2400}], "2099-01-01T00:00:00+00:00")
        self.database.initialize(self.seed)
        member = next(m for m in self.database.payload(self.seed)["members"] if m["name"] == "张三")
        self.assertEqual(member["handles"]["codeforces"]["rating"], 2100)
        self.assertEqual(member["handles"]["codeforces"]["maxRating"], 2400)
        self.assertEqual(len(member["accounts"]["codeforces"]), 2)

    def test_name_alias_applies_to_member_and_honor_without_changing_identity(self):
        self.seed["memberOverrides"] = [{"provider": "cpcfinder", "externalId": "student-1", "displayName": "中文名", "aliases": ["English Name"]}]
        self.database.initialize(self.seed)
        payload = self.database.payload(self.seed)
        member = next(m for m in payload["members"] if m["name"] == "中文名")
        self.assertEqual(len(payload["members"]), 3)
        self.assertIn("English Name", member["aliases"])
        self.assertEqual(payload["honors"][0]["members"][0], "中文名")
        self.database.set_display_name(member["id"], "人工修订名")
        self.database.initialize(self.seed)
        self.assertEqual(next(m for m in self.database.payload(self.seed)["members"] if m["id"] == member["id"])["name"], "人工修订名")

    def test_manual_honor_uses_ids_for_same_name_members_and_is_atomic(self):
        original = next(m for m in self.database.payload(self.seed)["members"] if m["name"] == "张三")
        other_id = self.database.add_manual_member("张三")
        record = {"event": "2009 老比赛", "date": "2009-10-01", "team": "历史队", "medal": "铁牌"}
        with self.assertRaises(ValueError):
            self.database.add_manual_honor_with_members(record, [other_id, 999999])
        self.assertEqual(len(self.database.payload(self.seed)["honors"]), 1)
        self.database.add_manual_honor_with_members(record, [other_id])
        self.database.initialize(self.seed)
        payload = self.database.payload(self.seed)
        self.assertEqual(next(m for m in payload["members"] if m["id"] == original["id"])["medals"]["iron"], 0)
        self.assertEqual(next(m for m in payload["members"] if m["id"] == other_id)["medals"]["iron"], 1)

    def test_same_external_award_different_snapshot_id_updates_existing_record(self):
        enriched = seed_data()
        enriched["honors"][0]["cpcfinderAwardId"] = 101
        self.database.initialize(enriched)
        enriched["honors"][0]["id"] = "new-snapshot-id"
        enriched["honors"][0]["rank"] = "2 / 100"
        self.database.initialize(enriched)
        honors = self.database.payload(enriched)["honors"]
        self.assertEqual(len(honors), 1)
        self.assertEqual(honors[0]["id"], "award-1")
        self.assertEqual(honors[0]["rank"], "2 / 100")

    def test_resync_removes_stale_public_result_link_but_keeps_manual_source(self):
        stale_seed = seed_data()
        stale_seed["honors"][0]["source"] = source("QOJ 镜像榜", "https://qoj.ac/results/wrong")
        stale_seed["honors"][0]["sources"] = [stale_seed["honors"][0]["source"]]
        self.database.initialize(stale_seed)
        with self.database.connect() as connection:
            manual_source_id = self.database._source(
                connection,
                source("队史人工核验", "https://example.com/manual"),
                manual=True,
            )
            connection.execute(
                "INSERT INTO honor_sources(honor_id, source_id, role, is_manual) VALUES (?, ?, 'result', 1)",
                ("award-1", manual_source_id),
            )

        self.database.initialize(self.seed)
        honor = self.database.payload(self.seed)["honors"][0]
        source_names = {item["name"] for item in honor["sources"]}

        self.assertEqual(honor["source"]["name"], "CPC Finder")
        self.assertNotIn("QOJ 镜像榜", source_names)
        self.assertIn("队史人工核验", source_names)

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
