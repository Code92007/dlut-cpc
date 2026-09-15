import copy
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from database import Database, load_seed_file
from schools import school_group
from tools.import_rankland import parse_initial_state, parse_ranklist, cached_page


def page(state):
    return "<html><script>window.__INITIAL_STATE__=" + repr(json.dumps(state, ensure_ascii=False)) + "</script></html>"


def ranklist():
    rows = []
    for index, (school, style) in enumerate((
        ("大连理工大学软件学院", 0), ("大连理工大学城市学院", 1),
        ("大连理工大学盘锦校区", 2), ("大连海事大学", 0),
    ), 1):
        rows.append({"user": {"id": str(index), "name": "同名队", "organization": school, "official": True},
                     "score": {"value": 5, "time": [index * 100, "min"]},
                     "rankValues": [{"rank": index, "segmentIndex": style}]})
    return {"ranklistData": {"info": {"uniqueKey": "icpc2018test", "name": "2018 ICPC Regional", "fileID": "file-test"},
            "srk": {"type": "static", "version": "0.3.13", "contest": {"title": "2018 ICPC Regional", "startAt": "2018-11-01T09:00:00+08:00"},
                    "series": [{"segments": [{"style": style} for style in ("gold", "silver", "bronze")],
                                "rule": {"preset": "ICPC", "options": {"count": {"value": [1, 1, 1]}}}}], "rows": rows}}}


def batch():
    return {"batchId": "rankland-test-v1", "source": "https://rl.algoux.cn/search", "honors": [{
        "id": "historic-1", "externalProvider": "rankland", "externalAwardId": "2018:team1",
        "externalContestId": "2018-test", "externalTeamId": "team1", "event": "2018 ICPC Regional",
        "series": "ICPC", "date": "2018-11-01", "team": "同名队", "medal": "金牌", "rank": "1 / 100",
        "school": "大连理工大学", "originalSchool": "大连理工大学软件学院", "expectedMembers": 3,
        "source": {"name": "RankLand 历史榜单", "url": "https://rl.algoux.cn/ranklist/icpc2018test"},
        "suggestedMembers": ["甲", "乙", "丙"], "archive": {"score": {"value": 5}, "fileId": "file-test"},
    }]}


class HistoricalTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.database = Database(self.root / "test.sqlite3")
        self.seed = {"meta": {}, "honors": [], "training": [], "historicalImports": [batch()]}
        self.database.initialize(self.seed)

    def tearDown(self):
        self.temporary.cleanup()

    def test_literal_state_parser_handles_quotes_entities_and_rejects_code(self):
        state = {"value": "O'Brien &amp; 队员\n"}
        self.assertEqual(parse_initial_state(page(state)), state)
        with self.assertRaises(ValueError):
            parse_initial_state("<script>window.__INITIAL_STATE__=__import__('os').system('echo bad')</script>")
        with self.assertRaises(ValueError):
            parse_initial_state(page({"loadFailed": True}))

    def test_campus_aliases_are_one_group_but_city_and_panjin_independent(self):
        self.assertEqual(school_group("大连理工大学开发区校区"), school_group("大连理工大学软件学院"))
        self.assertEqual(school_group("大连理工大学（盘锦校区）"), "大连理工大学盘锦校区")
        self.assertNotEqual(school_group("大连理工大学城市学院"), school_group("大连理工大学"))
        self.assertIsNone(school_group("大连海事大学"))

    def test_source_medal_markers_scope_and_team_ids_not_name_guessing(self):
        records, audit = parse_ranklist(page(ranklist()), convert=lambda value: value)
        self.assertEqual(len(records), 3)
        self.assertEqual([r["medal"] for r in records], ["金牌", "银牌", "铜牌"])
        self.assertEqual(len({r["id"] for r in records}), 3)
        self.assertEqual(records[0]["originalSchool"], "大连理工大学软件学院")
        self.assertEqual(records[1]["school"], "大连理工大学城市学院")
        self.assertTrue(all(not r["members"] for r in records))

    def test_excludes_province_invitation_prelim_and_post2019(self):
        for title in ("2018 ICPC Invitational", "2018 CCPC 省赛", "2018 ICPC 网络预选赛"):
            state = ranklist()
            state["ranklistData"]["srk"]["contest"]["title"] = title
            self.assertEqual(parse_ranklist(page(state), convert=lambda value: value)[0], [])
        state = ranklist()
        state["ranklistData"]["srk"]["contest"]["startAt"] = "2020-01-01T09:00:00+08:00"
        self.assertEqual(parse_ranklist(page(state), convert=lambda value: value)[0], [])

    def test_zero_medal_configuration_is_unknown_not_iron(self):
        state = ranklist()
        state["ranklistData"]["srk"]["series"][0]["rule"]["options"]["count"]["value"] = [0, 0, 0]
        records, audit = parse_ranklist(page(state), convert=lambda value: value)
        self.assertEqual(records, [])
        self.assertEqual(audit["excluded"], "missing-medal-boundaries")

    def test_award_visible_but_suggested_people_never_auto_linked(self):
        payload = self.database.payload(self.seed)
        self.assertEqual(len(payload["honors"]), 1)
        self.assertEqual(len(payload["pendingHonors"]), 1)
        self.assertEqual(payload["members"], [])
        self.assertEqual(payload["meta"]["memberCoverage"], 0)
        self.assertEqual(payload["medalSummary"][0]["gold"], 1)
        self.assertNotIn("historicalImports", payload)

    def test_atomic_confirm_keeps_award_and_survives_restart_resync_source_outage(self):
        ids = [self.database.add_manual_member(name) for name in ("甲", "乙", "丙")]
        for invalid in (ids[:2], [ids[0]] * 3, [ids[0], ids[1], 99999]):
            with self.assertRaises(ValueError):
                self.database.confirm_honor_members("historic-1", invalid)
            self.assertEqual(self.database.payload(self.seed)["honors"][0]["members"], [])
        self.database.confirm_honor_members("historic-1", ids)
        modified = copy.deepcopy(self.seed)
        modified["historicalImports"][0]["honors"][0]["members"] = ["不应覆盖的队员"]
        with patch("urllib.request.urlopen", side_effect=OSError("source offline")):
            self.database.initialize(modified)
            self.assertEqual(self.database.import_historical_batch(batch()), 0)
            payload = Database(self.database.path).payload(self.seed)
        self.assertEqual(payload["pendingHonors"], [])
        self.assertEqual(payload["honors"][0]["members"], ["甲", "乙", "丙"])
        self.assertEqual(payload["meta"]["memberCoverage"], 100)
        self.assertTrue(all(member["medals"]["gold"] == 1 for member in payload["members"]))
        with self.database.connect() as connection:
            self.assertEqual(json.loads(connection.execute("SELECT archive_json FROM honor_roster_reviews").fetchone()[0])["fileId"], "file-test")

    def test_partial_cli_links_remain_pending_and_do_not_attribute_medal(self):
        member_id = self.database.add_manual_member("甲")
        self.database.link_member("historic-1", member_id)
        payload = self.database.payload(self.seed)
        self.assertEqual(len(payload["pendingHonors"]), 1)
        self.assertEqual(payload["members"][0]["medals"]["gold"], 0)

    def test_independent_school_members_cannot_be_linked_to_main_campus_award(self):
        ids = [self.database.add_manual_member(name) for name in ("甲", "乙")]
        ids.append(self.database.add_manual_member("丙", school="大连理工大学城市学院"))
        with self.assertRaises(ValueError):
            self.database.confirm_honor_members("historic-1", ids)
        self.assertEqual(self.database.payload(self.seed)["honors"][0]["members"], [])

    def test_school_aware_name_matching_does_not_merge_independent_groups(self):
        for school in ("大连理工大学", "大连理工大学城市学院", "大连理工大学盘锦校区"):
            self.database.add_manual_member("同名选手", school=school, match_existing=True)
        self.assertEqual(len(self.database.payload(self.seed)["members"]), 3)

    def test_invalid_batch_rolls_back_and_does_not_set_import_marker(self):
        invalid = batch()
        invalid["batchId"] = "invalid-v1"
        invalid["honors"].append({**invalid["honors"][0], "id": "invalid", "date": "2020-01-01"})
        with self.assertRaises(ValueError):
            self.database.import_historical_batch(invalid)
        with self.database.connect() as connection:
            self.assertIsNone(connection.execute("SELECT 1 FROM metadata WHERE key='historical_import:invalid-v1'").fetchone())

    def test_local_archive_loader_and_cache_need_no_network(self):
        path = self.root / "site.json"
        path.write_text(json.dumps({"meta": {}, "honors": [], "training": []}))
        (self.root / "historical_honors.json").write_text(json.dumps(batch()))
        with patch("tools.import_rankland.urlopen", side_effect=AssertionError("must not fetch")):
            self.assertEqual(load_seed_file(path)["historicalImports"][0]["batchId"], "rankland-test-v1")
            self.assertEqual(cached_page("https://unused.example", path), path.read_text())

    def test_confirmed_history_adds_once_to_existing_public_member_medals(self):
        self.seed["publicMembers"] = [{"name": "甲", "provider": "cpcfinder", "externalId": "student-a",
                                      "cpcfinder": {"goldCount": 2, "silverCount": 1, "ironCount": 3}}]
        self.database.initialize(self.seed)
        a = self.database.payload(self.seed)["members"][0]["id"]
        ids = [a, self.database.add_manual_member("乙"), self.database.add_manual_member("丙")]
        self.database.confirm_honor_members("historic-1", ids)
        self.database.initialize(self.seed)
        member = next(m for m in self.database.payload(self.seed)["members"] if m["id"] == a)
        self.assertEqual(member["medals"], {"gold": 3, "silver": 1, "bronze": 0, "iron": 3})

    def test_best_regional_rank_includes_history_and_ignores_finals_unofficials(self):
        with self.database.connect() as connection:
            connection.execute("UPDATE honors SET rank='7 / 100' WHERE id='historic-1'")
        for identifier, event, rank, official in (("final", "2019 ICPC Final", "1", True),
                                                   ("unofficial", "2019 ICPC Regional", "1", False)):
            self.database.add_manual_honor({"id": identifier, "event": event, "date": "2019-11-01",
                                           "series": "ICPC", "team": identifier, "medal": "金牌",
                                           "rank": rank, "official": official})
        self.assertEqual(self.database.payload(self.seed)["meta"]["bestRank"], 7)

    def test_award_only_history_does_not_claim_zero_lifetime_iron(self):
        ids = [self.database.add_manual_member(name) for name in ("甲", "乙", "丙")]
        self.database.confirm_honor_members("historic-1", ids)
        payload = self.database.payload(self.seed)
        self.assertTrue(all(member["medals"]["iron"] is None for member in payload["members"]))

    @unittest.skipUnless(importlib.util.find_spec("standard_ranklist_utils"), "optional one-time import library")
    def test_official_srk_engine_excludes_unofficial_team_from_medal_ranking(self):
        state = ranklist()
        srk = state["ranklistData"]["srk"]
        srk["type"] = "general"
        srk["sorter"] = {"algorithm": "ICPC", "config": {}}
        srk["rows"][0]["user"]["official"] = False
        records, _ = parse_ranklist(page(state))
        self.assertEqual(len(records), 2)
        self.assertEqual([r["medal"] for r in records], ["金牌", "银牌"])


if __name__ == "__main__":
    unittest.main()
