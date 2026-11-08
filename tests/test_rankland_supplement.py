import importlib.util
import tempfile
import unittest
from pathlib import Path

from database import Database, load_seed_file
from tools.import_rankland import parse_ranklist
from test_historical_import import page, ranklist, batch as historical_batch


class SupplementTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.temporary.name) / "site.sqlite3")
        self.db.initialize()

    def tearDown(self):
        self.temporary.cleanup()

    def supplement(self):
        record = historical_batch()["honors"][0]
        return {"batchId": "rankland-supplement-test", "provider": "rankland", "honors": [{**record, "medal": "", "medalStatus": "unknown"}]}

    def test_zero_boundaries_preserve_participation_not_iron_and_accept_2020_season(self):
        state = ranklist()
        state["ranklistData"]["srk"]["contest"]["startAt"] = "2021-04-18T09:00:00+08:00"
        state["ranklistData"]["srk"]["series"][0]["rule"]["options"]["count"]["value"] = [0, 0, 0]
        records, _ = parse_ranklist(page(state), convert=lambda value: value, before_date=None, include_participation=True)
        self.assertEqual(len(records), 3)
        self.assertTrue(all(r["medal"] == "" and r["medalStatus"] == "unknown" for r in records))

    def test_starred_and_unawarded_teams_are_included_separately(self):
        state = ranklist()
        state["ranklistData"]["srk"]["rows"][0]["user"]["official"] = False
        state["ranklistData"]["srk"]["rows"][1]["rankValues"][0]["segmentIndex"] = None
        records, _ = parse_ranklist(page(state), convert=lambda value: value, include_participation=True)
        self.assertFalse(records[0]["official"])
        self.assertEqual(records[0]["medal"], "")
        self.assertEqual(records[1]["medal"], "铁牌")

    @unittest.skipUnless(importlib.util.find_spec("standard_ranklist_utils"), "RankLand conversion library")
    def test_ratio_awards_use_submitted_official_teams_and_source_rounding(self):
        state = ranklist()
        state["ranklistData"]["info"]["uniqueKey"] = "ccpc2026leshan"
        srk = state["ranklistData"]["srk"]
        srk.update(type="general", sorter={"algorithm": "ICPC", "config": {}})
        srk["contest"].update(title="第十二届中国大学生程序设计竞赛（乐山）", startAt="2026-11-08T09:00:00+08:00")
        srk["series"][0]["rule"]["options"] = {
            "ratio": {"value": [0.1, 0.2, 0.3], "denominator": "submitted", "rounding": "ceil"},
        }
        srk["rows"] = [
            {"user": {"id": str(i), "name": f"队伍{i}", "organization": "大连理工大学", "official": True},
             "score": {"value": 5 if i <= 10 else 0, "time": [i * 100, "min"]},
             "statuses": [{"result": "AC" if i <= 10 else None, "tries": 1 if i <= 10 else 0}]}
            for i in range(1, 12)
        ]
        srk["rows"].append({"user": {"id": "star", "name": "打星队", "organization": "大连理工大学", "official": False},
                            "score": {"value": 6, "time": [10, "min"]}, "statuses": [{"result": "AC", "tries": 1}]})
        records, _ = parse_ranklist(page(state), before_date=None, include_participation=True)
        medals = {record["team"]: record["medal"] for record in records}
        self.assertEqual(medals, {"打星队": "", "队伍1": "金牌", "队伍2": "银牌", "队伍3": "银牌",
                                  "队伍4": "铜牌", "队伍5": "铜牌", "队伍6": "铜牌",
                                  **{f"队伍{i}": "铁牌" for i in range(7, 12)}})
        self.assertTrue(all(record["archive"]["medalBoundariesKnown"] for record in records))
        self.assertTrue(all(record["location"] == "乐山" for record in records))

    def test_zero_ratio_still_leaves_awards_unknown(self):
        state = ranklist()
        state["ranklistData"]["srk"]["series"][0]["rule"]["options"] = {"ratio": {"value": [0, 0, 0]}}
        records, _ = parse_ranklist(page(state), convert=lambda value: value, include_participation=True)
        self.assertTrue(all(record["medal"] == "" and record["medalStatus"] == "unknown" for record in records))

    def test_localized_source_member_names_are_plain_strings(self):
        state = ranklist()
        state["ranklistData"]["srk"]["rows"][0]["user"]["teamMembers"] = [{"name": {"zh-CN": "甲", "fallback": "A"}}]
        records, _ = parse_ranklist(page(state), convert=lambda value: value)
        self.assertEqual(records[0]["suggestedMembers"], ["甲"])

    def test_overall_rank_uses_source_tied_rank_not_list_position(self):
        state = ranklist()
        source = state["ranklistData"]["srk"]
        source["series"].append({"rule": {"preset": "Normal"}})
        for row in source["rows"]:
            row["rankValues"].append({"rank": 1})
        records, _ = parse_ranklist(page(state), convert=lambda value: value)
        self.assertTrue(all(record["overallRank"] == "1 / 4" for record in records))

    def test_host_school_is_always_starred_even_when_source_official_flag_is_wrong(self):
        state = ranklist()
        state["ranklistData"]["info"]["uniqueKey"] = "icpc2011dalian"
        records, _ = parse_ranklist(page(state), convert=lambda value: value, include_participation=True)
        self.assertTrue(all(r["official"] is False and r["medal"] == "" for r in records))
        self.assertTrue(all(r["archive"]["sourceOfficial"] for r in records))

    def test_unknown_award_merge_is_durable_and_does_not_replace_manual_medal_or_members(self):
        record = self.supplement()["honors"][0]
        self.db.add_manual_honor({**record, "medal": "银牌", "members": ["人工名单"], "externalProvider": None, "externalAwardId": None})
        report = self.db.merge_official_batch(self.supplement())
        self.assertEqual((report["added"], report["merged"], report["conflict"]), (0, 1, 0))
        result = self.db.payload({"meta": {}})["honors"][0]
        self.assertEqual(result["medal"], "银牌")
        self.assertEqual(result["members"], ["人工名单"])
        self.assertTrue(self.db.merge_official_batch(self.supplement())["alreadyImported"])

    def test_unknown_award_can_be_confirmed_atomically_and_does_not_count_before_confirmation(self):
        self.db.merge_official_batch(self.supplement())
        self.db.confirm_honor_members("historic-1", ["甲", "乙", "丙"], medal="银牌")
        payload = self.db.payload({"meta": {}})
        self.assertFalse(payload["honors"][0]["medalPending"])
        self.assertTrue(all(m["medals"]["silver"] == 1 for m in payload["members"]))
        self.assertEqual(payload["pendingHonors"], [])
        self.db.initialize({"officialImports": [self.supplement()]})
        self.assertEqual(self.db.payload({"meta": {}})["honors"][0]["medal"], "银牌")

    def test_invalid_award_rolls_back_member_creation_and_roster_confirmation(self):
        self.db.merge_official_batch(self.supplement())
        with self.assertRaises(ValueError):
            self.db.confirm_honor_members("historic-1", ["甲", "乙", "丙"], medal="invalid")
        payload = self.db.payload({"meta": {}})
        self.assertEqual(payload["members"], [])
        self.assertEqual(len(payload["pendingHonors"]), 1)

    def test_later_public_source_fills_unknown_medal_without_replacing_fields_or_roster(self):
        value = self.supplement()
        original = value["honors"][0]
        self.db.merge_official_batch(value)
        self.db.confirm_honor_members("historic-1", ["甲", "乙", "丙"])
        public = {**original, "id": "public-new", "externalProvider": "cpcfinder", "externalAwardId": "public-award",
                  "medal": "银牌", "rank": "21 / 300", "members": ["其他甲", "其他乙", "其他丙"]}
        self.db.initialize({"honors": [public]})
        self.db.initialize({"honors": [public]})
        results = self.db.payload({"meta": {}})["honors"]
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["medal"], "银牌")
        self.assertEqual(results[0]["rank"], original["rank"])
        self.assertEqual(results[0]["members"], ["甲", "乙", "丙"])

    def test_later_archived_source_fills_unknown_medal_without_duplicate(self):
        value = self.supplement()
        self.db.merge_official_batch(value)
        followup = {**value, "batchId": "rankland-followup", "honors": [{**value["honors"][0], "externalAwardId": "followup-award", "medal": "铜牌"}]}
        preview = self.db.merge_official_batch(followup, dry_run=True)
        self.assertEqual((preview["added"], preview["merged"]), (0, 1))
        self.assertEqual(self.db.payload({"meta": {}})["honors"][0]["medal"], "")
        self.db.merge_official_batch(followup)
        results = self.db.payload({"meta": {}})["honors"]
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["medal"], "铜牌")

    def test_source_tied_ranks_do_not_drop_distinct_teams(self):
        value = self.supplement()
        value["honors"][0]["externalContestId"] = "icpc2018test"
        value["honors"].append({**value["honors"][0], "id": "historic-2", "externalAwardId": "other-id", "team": "另一队", "suggestedMembers": []})
        self.assertEqual(self.db.merge_official_batch(value)["added"], 2)

    def test_archive_covers_all_requested_contests_and_specific_missing_teams(self):
        source = load_seed_file(Path(__file__).resolve().parents[1] / "data/site.json")
        value = next(b for b in source["officialImports"] if b.get("provider") == "rankland")
        self.assertEqual(len(value["contests"]), 27)
        teams = {h["team"] for h in value["honors"]}
        for expected in ["水煮鱼 (Boiled Fish)", "zergling", "IBN5100", "GodSpeed", "BlueSky", "dlut_Real Power"]:
            self.assertIn(expected, teams)
        self.assertEqual(sum(h["externalContestId"] == "icpc2011dalian" and h["official"] is False for h in value["honors"]), 7)


if __name__ == "__main__":
    unittest.main()
