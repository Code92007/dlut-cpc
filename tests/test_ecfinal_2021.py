import copy
import json
from pathlib import Path
import tempfile
import unittest

from database import Database, load_seed_file
from historical_rating import calculate_ratings
from tools.build_team_performances import append_archived_contests


ROOT = Path(__file__).resolve().parents[1]


class ECFinal2021Tests(unittest.TestCase):
    def setUp(self):
        self.batch = json.loads((ROOT / 'data/icpc_ecfinal_2021_honors.json').read_text())

    def test_official_ranks_and_awards_have_full_field_evidence(self):
        contest = self.batch['contests'][0]
        scores = contest['scoreEvidence']
        formal = [s for s in scores if s['official']]
        self.assertEqual((len(scores), len(formal)), (367, 363))
        self.assertEqual(contest['sourceHashes'], contest['statistics']['sha256'])
        limits = contest['statistics']['medal_rank_limits']
        for h in self.batch['honors']:
            own = h['archive']
            score = (-own['score']['value'], own['penaltySeconds'])
            rank = 1 + sum((-s['solved'], s['penaltySeconds']) < score for s in formal)
            overall = 1 + sum((-s['solved'], s['penaltySeconds']) < score for s in scores)
            self.assertEqual(h['rank'], f'{rank} / 363')
            self.assertEqual(h['overallRank'], f'{overall} / 367')
            self.assertIn(dict(official=True, solved=-score[0], penaltySeconds=score[1], accepted=own['accepted']), formal)
            medal = next((cn for key, cn in [('gold', '金牌'), ('silver', '银牌'), ('bronze', '铜牌')]
                          if rank <= limits[key]), '铁牌')
            self.assertEqual(h['medal'], medal)
        self.assertEqual({h['team']: h['rank'] for h in self.batch['honors']}, {
            '梦觉流莺时一声': '109 / 363', '三带一': '218 / 363',
            'Relaxing!': '252 / 363', '我们队没有GF': '262 / 363',
        })

    def test_additive_import_is_loaded_once_and_keeps_rosters_pending(self):
        loaded = load_seed_file(ROOT / 'data/site.json')
        self.assertIn(self.batch['batchId'], {b['batchId'] for b in loaded['officialImports']})
        with tempfile.TemporaryDirectory() as folder:
            db = Database(Path(folder) / 'site.sqlite3')
            seed = dict(meta={}, honors=[], training=[], officialImports=[self.batch])
            db.initialize(seed)
            before = db.payload(seed)
            db.initialize(seed)
            after = db.payload(seed)
            self.assertEqual(after['honors'], before['honors'])
            self.assertEqual(len(after['honors']), 4)
            self.assertTrue(all(not h['rosterConfirmed'] and not h['members'] for h in after['honors']))
            self.assertEqual(len(after['pendingHonors']), 4)
            self.assertEqual(calculate_ratings(after)['coverage']['ratedResults'], 0)
            h = after['honors'][0]
            db.confirm_honor_members(h['id'], ['甲', '乙', '丙'])
            db.initialize(seed)
            updated = next(r for r in db.payload(seed)['honors'] if r['id'] == h['id'])
            self.assertEqual(updated['members'], ['甲', '乙', '丙'])

    def test_performance_supplement_survives_rebuild_without_duplicates(self):
        archive = dict(records=[], stations=[])
        append_archived_contests(archive, self.batch)
        before = copy.deepcopy(archive)
        append_archived_contests(archive, self.batch)
        self.assertEqual(archive, before)
        bronze, *irons = archive['records']
        self.assertEqual((bronze['season'], bronze['contestSeason'], bronze['date']), (2022, 2021, '2022-07-20'))
        self.assertEqual((bronze['maxSolvedRating'], bronze['stage'], bronze['nextStage'], bronze['nextTwoStages']),
                         (1900, '稳铜', '速银·仅罚时', '稳银 2000'))
        self.assertTrue(all((r['maxSolvedRating'], r['stage'], r['nextStage'], r['nextTwoStages']) ==
                            (1000, '铁牌', '速铜·仅罚时', '稳铜 1900') for r in irons))
        committed = json.loads((ROOT / 'data/team_performances.json').read_text())
        self.assertEqual([r for r in committed['records'] if r.get('contestId') == 'icpc-2021-ECFinal'], archive['records'])


if __name__ == '__main__':
    unittest.main()
