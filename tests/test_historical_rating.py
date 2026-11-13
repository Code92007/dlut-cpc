import copy
import json
import unittest
from pathlib import Path
from unittest.mock import patch

from historical_rating import calculate_ratings, analytics_payload, rank_fraction


def members(count=6):
    return [dict(id=i, name=f'成员{i}', school='大连理工大学', status='alumni') for i in range(1, count + 1)]


def result(key='a', date='2015-11-01', ids=(1, 2, 3), rank='10 / 100', **extra):
    return dict(id=key, event='第 1 届 CCPC 中国大学生程序设计竞赛南阳站', series='CCPC', date=date,
                team=key, rosterConfirmed=True, official=True, rank=rank, medal='金牌',
                memberDetails=[dict(id=i) for i in ids], **extra)


class HistoricalRatingTests(unittest.TestCase):
    def calculate(self, honors):
        return calculate_ratings(dict(members=members(), honors=honors))

    def test_old_ccpc_and_icpc_results_need_no_problem_or_external_ratings(self):
        honors = [result(), result('b', '2016-11-01'), result('c', '2017-11-01')]
        honors[1].update(event='第 41 届 ICPC 国际大学生程序设计竞赛亚洲区域赛北京站', series='ICPC')
        p = self.calculate(honors)['players'][0]
        self.assertEqual(p['contests'], 3)
        self.assertFalse(p['provisional'])
        self.assertGreater(p['peak'], 1500)
        self.assertEqual(p['peakDate'], '2017-11-01')

    def test_equal_teammates_share_result_and_no_data_is_not_1500(self):
        data = self.calculate([result()])
        self.assertEqual([p['current'] for p in data['players'][:3]], [1662] * 3)
        self.assertTrue(all(p['provisional'] and p['peak'] is None for p in data['players']))
        self.assertIsNone(data['players'][3]['current'])
        self.assertEqual(data['players'][0]['teamCount'], 1)

    def test_missing_results_do_not_count_as_losses_or_guess_from_medals(self):
        honors = [result('good'), result('pending', '2016-01-01'), result('star', '2016-02-01'),
                  result('norank', '2016-03-01'), result('unknown', '2016-04-01'),
                  result('network', '2016-05-01'), result('incomplete', '2016-06-01')]
        honors[1]['rosterConfirmed'] = False
        honors[2]['official'] = False
        honors[3]['rank'] = '金牌'
        honors[4]['official'] = None
        honors[5]['event'] = 'ICPC 网络预选赛'
        honors[6]['memberDetails'] = [dict(id=1), dict(id=2)]
        data = self.calculate(honors)
        self.assertEqual(data['coverage']['ratedResults'], 1)
        self.assertEqual(len(data['skipped']), 6)
        self.assertEqual(data['players'][0], self.calculate([honors[0]])['players'][0])

    def test_duplicate_and_same_day_identity_conflicts_cannot_inflate_score(self):
        original = result()
        duplicate = {**original, 'id': 'copy'}
        data = self.calculate([original, duplicate])
        self.assertEqual(data['coverage']['ratedResults'], 1)
        self.assertIn('重复', data['skipped'][0]['reason'])
        conflict = result('other', ids=(1, 4, 5))
        data = self.calculate([original, conflict])
        self.assertEqual(data['coverage']['ratedResults'], 0)
        self.assertEqual(len(data['skipped']), 2)

    def test_input_order_is_irrelevant_and_new_results_do_not_leak_into_history(self):
        honors = [result(), result('b', '2016-11-01', rank='90 / 100'), result('c', '2017-11-01')]
        original = copy.deepcopy(honors)
        data = self.calculate(honors)
        self.assertEqual(data, self.calculate(list(reversed(honors))))
        self.assertEqual(data['players'][0]['history'][:2], self.calculate(honors[:2])['players'][0]['history'])
        self.assertEqual(honors, original)
        honors[0]['rank'] = '11 / 100'
        self.assertNotEqual(data['inputHash'], self.calculate(honors)['inputHash'])

    def test_first_two_results_are_excluded_from_mature_peak(self):
        honors = [result(), result('b', '2016-11-01'), result('c', '2017-11-01', rank='100 / 100')]
        p = self.calculate(honors)['players'][0]
        self.assertLess(p['current'], p['history'][1]['rating'])
        self.assertEqual(p['peak'], p['current'])

    def test_bad_dates_duplicate_identity_and_invalid_fraction_are_unrated(self):
        honors = [result(date='2015'), result('b', ids=(1, 1, 2)), result('c', rank='101 / 100')]
        self.assertEqual(self.calculate(honors)['coverage']['ratedResults'], 0)
        for rank in ['0 / 10', '2 / 1', '1 / 1', '100', '', '1 / 0']:
            self.assertIsNone(rank_fraction(rank))

    def test_performance_archive_has_every_visible_row_and_provenance(self):
        path = Path(__file__).resolve().parents[1] / 'data/team_performances.json'
        archive = json.loads(path.read_text())
        rows = archive['records']
        self.assertEqual([r['sourceRow'] for r in rows], list(range(2, 49)))
        self.assertEqual(len({r['id'] for r in rows}), 47)
        self.assertEqual(max(r['maxSolvedRating'] for r in rows), 2700)
        payload = analytics_payload(dict(members=members(), honors=[]))
        self.assertEqual(len(payload['performances']['records']), 47)
        self.assertTrue(all(r['honor'] is None for r in payload['performances']['records']))


if __name__ == '__main__':
    unittest.main()
