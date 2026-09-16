import importlib.util
import json
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "tools" / "sync_public_data.py"
SPEC = importlib.util.spec_from_file_location("sync_public_data", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(MODULE)


class DataPipelineTests(unittest.TestCase):
    def test_parse_filters_old_rows_and_keeps_ranked_unawarded_results(self):
        document = """
        <table><tbody>
          <tr><td>赛事 A</td><td>Team A</td><td>金牌</td><td>2024-11-03</td><td>南京</td><td>12 / 300</td><td>15 / 320</td></tr>
          <tr><td>赛事 B</td><td>Team B</td><td></td><td>2024-11-03</td><td>南京</td><td>99 / 300</td><td>100 / 320</td></tr>
          <tr><td>赛事 C</td><td>Team C</td><td>银牌</td><td>2019-11-03</td><td>南京</td><td>50 / 300</td><td>55 / 320</td></tr>
        </tbody></table>
        """
        result = MODULE.parse_cpcfinder(document, "https://example.com")
        self.assertEqual(len(result), 2)
        self.assertEqual(next(row for row in result if row["team"] == "Team B")["medal"], "铁牌")

    def test_deduplicate_normalizes_team_punctuation(self):
        base = {
            "event": "ICPC 武汉站",
            "date": "2025-11-02",
            "location": "武汉",
            "medal": "金牌",
            "rank": "37 / 446",
            "series": "ICPC",
            "members": [],
            "source": {"name": "CPC Finder", "url": "https://example.com/a"},
        }
        records = [
            {**base, "team": "关注带工喵！"},
            {**base, "team": "关注带工喵 !", "members": ["甲", "乙", "丙"], "source": {"name": "官方榜单", "url": "https://example.com/b"}},
        ]
        result = MODULE.deduplicate(records)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["members"], ["甲", "乙", "丙"])
        self.assertEqual(result[0]["source"]["name"], "官方榜单")

    def test_parse_api_uses_official_rank_and_keeps_iron(self):
        document = json.dumps({"data": [
            {"awardId": 101, "contestId": 22, "teamId": 303, "contestName": "第 50 届 ICPC 亚洲区域赛武汉站", "teamName": "Team A", "medal": "金牌", "date": "2025-11-02", "place": "武汉", "rank": 49, "officialRank": 37, "totalTeams": 520, "totalOfficialTeams": 446},
            {"contestName": "第 50 届 ICPC 亚洲区域赛武汉站", "teamName": "Team B", "date": "2025-11-02", "place": "武汉", "rank": 100, "totalTeams": 520},
        ]}, ensure_ascii=False)
        result = MODULE.parse_cpcfinder(document, "https://cpcfinder.com/api/school/test-id/awards")
        self.assertEqual(len(result), 2)
        awarded = next(row for row in result if row["team"] == "Team A")
        self.assertEqual(awarded["rank"], "37 / 446")
        self.assertEqual(awarded["overallRank"], "49 / 520")
        self.assertEqual(awarded["source"]["url"], "https://cpcfinder.com/school/test-id")
        self.assertEqual(awarded["cpcfinderAwardId"], 101)
        self.assertEqual(awarded["cpcfinderContestId"], 22)
        self.assertEqual(next(row for row in result if row["team"] == "Team B")["medal"], "铁牌")

    def test_chinese_ccpc_title_without_acronym_is_classified_correctly(self):
        document = json.dumps({"data": [{
            "awardId": 22896,
            "contestName": "第 10 届中国大学生程序设计竞赛总决赛",
            "teamName": "逆元",
            "date": "2025-05-11",
            "rank": 93,
            "totalTeams": 124,
        }]}, ensure_ascii=False)
        row = MODULE.parse_cpcfinder_api(document, MODULE.DEFAULT_SOURCE_URL)[0]
        self.assertEqual(row["series"], "CCPC")
        self.assertEqual(MODULE.contest_series("第 50 届 ICPC 国际大学生程序设计竞赛亚洲区域赛上海站"), "ICPC")

    def test_iron_requires_real_rank_and_no_medal_not_unknown_result(self):
        self.assertEqual(MODULE.result_medal(None, 100, "NONE"), "铁牌")
        self.assertEqual(MODULE.result_medal("", "100 / 300"), "铁牌")
        self.assertIsNone(MODULE.result_medal(None, None, "NONE"))
        self.assertIsNone(MODULE.result_medal(None, 0, "NONE"))
        self.assertIsNone(MODULE.result_medal(None, 100, "PENDING"))

    def test_nonofficial_iron_uses_overall_rank_denominator(self):
        document = json.dumps({"data": [{
            "awardId": 8178, "contestId": 24, "contestName": "CCPC 桂林", "teamName": "暂无名",
            "date": "2022-10-30", "place": "桂林", "rank": 36, "official": False,
            "medalType": "NONE", "totalTeams": 323, "totalOfficialTeams": 240,
        }]}, ensure_ascii=False)
        row = MODULE.parse_cpcfinder_api(document, MODULE.DEFAULT_SOURCE_URL)[0]
        self.assertEqual(row["rank"], "36 / 323")
        self.assertFalse(row["official"])

    def test_same_team_name_different_awards_are_not_combined(self):
        base = {"event": "测试赛", "date": "2025-11-01", "team": "暂无名", "medal": "铁牌"}
        rows = MODULE.deduplicate([
            {**base, "cpcfinderAwardId": 1},
            {**base, "cpcfinderAwardId": 2},
            {**base, "cpcfinderAwardId": 1},
        ])
        self.assertEqual(len(rows), 2)
        self.assertEqual(len({row["id"] for row in rows}), 2)

    def test_member_lookup_filters_other_schools_before_iron_counting(self):
        document = json.dumps({"data": [
            {"awardId": 1, "schoolName": "大连理工大学", "members": [{"studentId": "a", "name": "甲"}]},
            {"awardId": 2, "schoolName": "其他学校", "members": [{"studentId": "a", "name": "甲"}]},
        ]}, ensure_ascii=False)
        lookup = MODULE.contest_member_map(document, {"1", "2"}, MODULE.SCHOOL_ALIASES)
        self.assertEqual(set(lookup), {"1"})
        members = [{"externalId": "a", "cpcfinder": {}}]
        records = [
            {"cpcfinderAwardId": 1, "schoolVerified": True},
            {"cpcfinderAwardId": 2, "schoolVerified": False},
        ]
        MODULE.apply_iron_counts(members, records, {"a": {"1", "2"}}, [])
        self.assertEqual(members[0]["cpcfinder"]["ironCount"], 1)

    def test_failed_contest_does_not_turn_unknown_iron_count_into_zero(self):
        members = [{"externalId": "a", "cpcfinder": {}}]
        records = [{"cpcfinderAwardId": 1, "cpcfinderContestId": 22}]
        MODULE.apply_iron_counts(members, records, {"a": {"1"}}, [22])
        self.assertNotIn("ironCount", members[0]["cpcfinder"])

    def test_parse_student_directory_keeps_stable_identity_and_summary(self):
        document = json.dumps({"data": [{
            "studentId": "student-xia",
            "name": "夏鸿康",
            "schoolName": "大连理工大学",
            "championCount": 0,
            "secCount": 0,
            "thiCount": 0,
            "goldCount": 2,
            "silverCount": 6,
            "bronzeCount": 2,
            "rating": 1182.0127,
            "rank": 1,
            "latestEventDate": "2023-03-25",
        }, {
            "studentId": "panjin-student",
            "name": "盘锦选手",
            "schoolName": "大连理工大学（盘锦校区）",
        }, {
            "studentId": "city-college-student",
            "name": "城市学院选手",
            "schoolName": "大连理工大学城市学院",
        }, {
            "studentId": "other",
            "name": "其他选手",
            "schoolName": "其他学校",
        }]}, ensure_ascii=False)

        result = MODULE.parse_cpcfinder_students(document)

        self.assertEqual(len(result), 3)
        self.assertEqual(result[0]["externalId"], "student-xia")
        self.assertEqual(result[0]["cpcfinder"]["silverCount"], 6)
        self.assertEqual(result[0]["cpcfinder"]["rating"], 1182.0127)
        self.assertIn("panjin-student", {item["externalId"] for item in result})
        self.assertIn("city-college-student", {item["externalId"] for item in result})
        self.assertEqual(next(item for item in result if item["externalId"] == "panjin-student")["school"], "大连理工大学盘锦校区")

    def test_contest_member_map_matches_award_and_keeps_student_identity(self):
        document = json.dumps({"data": [
            {
                "awardId": 101,
                "members": [
                    {"studentId": "student-a", "name": "甲"},
                    {"studentId": "student-b", "name": "乙"},
                    {"studentId": "student-c", "name": "丙"},
                ],
            },
            {"awardId": 102, "members": [{"studentId": "other", "name": "其他学校成员"}]},
        ]}, ensure_ascii=False)
        result = MODULE.contest_member_map(document, {"101"})
        self.assertEqual([item["name"] for item in result["101"]], ["甲", "乙", "丙"])
        self.assertEqual(result["101"][0]["externalId"], "student-a")
        self.assertNotIn("102", result)

    def test_existing_result_source_cannot_replace_exact_cpcfinder_roster(self):
        fetched = [{
            "id": "award-a",
            "event": "第 10 届 CCPC 中国大学生程序设计竞赛重庆站",
            "date": "2024-11-10",
            "location": "重庆",
            "team": "可持久化猫猫虫自动机",
            "medal": "银牌",
            "rank": "71 / 278",
            "series": "CCPC",
            "members": ["陈常杰", "连茗暄", "刘兆洲"],
            "memberDetails": [
                {"name": "陈常杰", "provider": "cpcfinder", "externalId": "student-chen"},
                {"name": "连茗暄", "provider": "cpcfinder", "externalId": "student-lian"},
                {"name": "刘兆洲", "provider": "cpcfinder", "externalId": "student-liu"},
            ],
            "cpcfinderAwardId": 19519,
            "source": {"name": "CPC Finder", "url": "https://cpcfinder.com/school/test"},
        }]
        existing = [{
            **fetched[0],
            "members": ["刘博文", "连茗暄", "刘兆洲"],
            "source": {"name": "QOJ 镜像榜", "url": "https://qoj.ac/results/example"},
        }]

        result = MODULE.merge_with_existing(fetched, existing, [])

        self.assertEqual(result[0]["members"], ["陈常杰", "连茗暄", "刘兆洲"])
        self.assertEqual(result[0]["memberDetails"][0]["externalId"], "student-chen")
        self.assertEqual(result[0]["source"]["name"], "CPC Finder")
        self.assertEqual(result[0]["sources"], [{"name": "CPC Finder", "url": "https://cpcfinder.com/school/test"}])

    def test_explicit_manual_roster_can_replace_cpcfinder_roster(self):
        fetched = [{
            "event": "赛事 A",
            "date": "2024-11-10",
            "location": "重庆",
            "team": "Team A",
            "medal": "银牌",
            "rank": "1 / 2",
            "series": "CCPC",
            "members": ["甲", "乙", "丙"],
            "source": {"name": "CPC Finder", "url": "https://cpcfinder.com"},
        }]
        existing = [{
            **fetched[0],
            "members": ["甲", "乙", "丁"],
            "memberRosterManual": True,
            "source": {"name": "人工录入", "url": ""},
        }]

        result = MODULE.merge_with_existing(fetched, existing, [])

        self.assertEqual(result[0]["members"], ["甲", "乙", "丁"])


if __name__ == "__main__":
    unittest.main()
