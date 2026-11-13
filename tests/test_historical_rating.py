import copy
import json
import unittest
from pathlib import Path
from unittest.mock import patch

from historical_rating import calculate_ratings, analytics_payload, rank_fraction, result_performance
from tools.build_team_performances import target_plans


def members(count=6):
    return [dict(id=i, name=f'成员{i}', school='大连理工大学', status='alumni') for i in range(1, count + 1)]


def result(key='a', date='2015-11-01', ids=(1, 2, 3), rank='10 / 100', **extra):
    return dict(id=key, event='第 1 届 CCPC 中国大学生程序设计竞赛南阳站', series='CCPC', date=date,
                team=key, rosterConfirmed=True, official=True, rank=rank, medal=extra.pop('medal', '金牌'),
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
        self.assertEqual([p['current'] for p in data['players'][:3]], [2065] * 3)
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
        honors = [result(), result('b', '2016-11-01'), result('c', '2017-11-01', rank='100 / 100', medal='铁牌')]
        p = self.calculate(honors)['players'][0]
        self.assertLess(p['current'], p['history'][1]['rating'])
        self.assertEqual(p['peak'], p['current'])

    def test_bad_dates_duplicate_identity_and_invalid_fraction_are_unrated(self):
        honors = [result(date='2015'), result('b', ids=(1, 1, 2)), result('c', rank='101 / 100')]
        self.assertEqual(self.calculate(honors)['coverage']['ratedResults'], 0)
        for rank in ['0 / 10', '2 / 1', '1 / 1', '100', '', '1 / 0']:
            self.assertIsNone(rank_fraction(rank))

    def test_awards_and_rank_anchor_performance_and_repeated_silvers_do_not_accumulate_forever(self):
        self.assertGreater(result_performance('金牌', 20, 100), result_performance('银牌', 1, 100))
        self.assertGreater(result_performance('银牌', 11, 100), result_performance('银牌', 25, 100))
        golds = [result(str(i), f'{2010+i}-11-01', rank='10 / 100') for i in range(5)]
        silvers = [result(str(i), f'{2010+i}-11-01', rank='11 / 100', medal='银牌') for i in range(6)]
        self.assertGreater(self.calculate(golds)['players'][0]['peak'], self.calculate(silvers)['players'][0]['peak'])
        many = [result(str(i), f'{1900+i}-11-01', rank='11 / 100', medal='银牌') for i in range(100)]
        p = self.calculate(many)['players'][0]
        self.assertLessEqual(p['current'], 2400)
        self.assertLess(abs(p['history'][-1]['delta']), .01)

    def test_each_member_updates_against_own_prior_and_final_is_a_stronger_field(self):
        honors = [result(), result('b', '2016-11-01'), result('c', '2017-11-01'),
                  result('d', '2018-11-01', ids=(1,4,5), medal='银牌', rank='30 / 100')]
        data = self.calculate(honors)
        self.assertLess(data['players'][0]['history'][-1]['delta'], 0)
        self.assertGreater(data['players'][3]['history'][-1]['delta'], 0)
        for series, event in [('ICPC', '第 43 届 ICPC 国际大学生程序设计竞赛东亚区决赛'),
                              ('CCPC', '第 3 届 CCPC 中国大学生程序设计竞赛总决赛')]:
            ordinary = self.calculate([result(medal='铜牌', rank='45 / 100')])['players'][0]['history'][0]
            final = result(medal='铜牌', rank='45 / 100')
            final.update(series=series, event=event)
            h = self.calculate([final])['players'][0]['history'][0]
            self.assertEqual(h['performance'] - ordinary['performance'], 300)
            self.assertEqual(h['fieldAdjustment'], 300)
            self.assertEqual(h['delta'] - ordinary['delta'], 150)
        unknown = result(medal='')
        self.assertEqual(self.calculate([unknown])['coverage']['ratedResults'], 0)

    def test_target_plans_keep_existing_ac_and_preserve_collapsed_stage_slots(self):
        stats = dict(medal_boundaries={m: dict(last_solved=n) for m,n in [('gold',4),('silver',3),('bronze',2)]},
                     medal_safety={m: dict(safe_solved=n) for m,n in [('gold',5),('silver',4),('bronze',4)]})
        plans = target_plans(['E'], dict(A=800,B=900,C=1200,D=1500,E=2700), stats)
        self.assertEqual(plans['速铜']['text'], '速铜 800')
        self.assertEqual(plans['速铜']['problems'], [dict(index='A',rating=800)])
        self.assertEqual(plans['稳铜']['text'], '')
        self.assertEqual(plans['速银']['text'], '速银 900')
        self.assertEqual(target_plans(['A','B'], dict(A=800,B=900), stats)['速铜']['text'], '速铜·仅罚时')
        self.assertEqual(target_plans(['A'], dict(A=800), stats)['速铜']['text'], '')

    def test_performance_archive_has_full_workbook_and_six_verified_new_stations(self):
        path = Path(__file__).resolve().parents[1] / 'data/team_performances.json'
        archive = json.loads(path.read_text())
        rows = archive['records']
        self.assertEqual([r['sourceRow'] for r in rows if r['sourceKind']=='workbook'], list(range(2, 162)))
        self.assertEqual(len({r['id'] for r in rows}), 183)
        self.assertEqual(len(archive['stations']), 6)
        self.assertEqual(sum(r['sourceKind']=='cf-bot-2026' for r in rows), 23)
        self.assertEqual(max(r['maxSolvedRating'] or 0 for r in rows), 2700)
        self.assertEqual(sum(r['maxSolvedRating'] is None for r in rows), 7)
        payload = analytics_payload(dict(members=members(), honors=[]))
        self.assertEqual(len(payload['performances']['records']), 183)
        self.assertTrue(all(r['honor'] is None for r in payload['performances']['records']))


if __name__ == '__main__':
    unittest.main()
