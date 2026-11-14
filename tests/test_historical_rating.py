import copy
import json
import unittest
from pathlib import Path
from unittest.mock import patch

from historical_rating import calculate_ratings, analytics_payload, rank_fraction, result_performance, verified_rank
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
        baseline = self.calculate([honors[0]])['players'][0]
        self.assertEqual(data['players'][0]['history'], baseline['history'])
        self.assertEqual(data['players'][0]['current'], baseline['current'])
        self.assertEqual(data['players'][0]['careerContests'], 2)
        self.assertEqual(len(data['players'][0]['unratedHistory']), 1)

    def test_duplicate_and_same_day_identity_conflicts_cannot_inflate_score(self):
        original = result()
        duplicate = {**original, 'id': 'copy'}
        data = self.calculate([original, duplicate])
        self.assertEqual(data['coverage']['ratedResults'], 1)
        self.assertEqual(data['players'][0]['careerContests'], 1)
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

    def test_shared_team_delta_preserves_gap_and_new_member_has_no_extra_update_weight(self):
        honors = [result(), result('b', '2016-11-01'), result('c', '2017-11-01'),
                  result('d', '2018-11-01', ids=(1,4,5), medal='银牌', rank='30 / 100')]
        data = self.calculate(honors)
        a, b = data['players'][0]['history'][-1], data['players'][3]['history'][-1]
        self.assertEqual(a['delta'], b['delta'])
        self.assertEqual(a['updateWeight'], .3)
        self.assertEqual(b['updateWeight'], .3)
        self.assertAlmostEqual(a['rating'] - b['rating'], a['before'] - b['before'], delta=.02)
        self.assertAlmostEqual(a['delta'], .3 * (a['performance'] - (a['before'] + 3000) / 3), places=1)
        # Below the team's prior makes everyone lose, regardless of sample size.
        honors[-1].update(medal='铁牌', rank='90 / 100')
        data = self.calculate(honors)
        self.assertLess(data['players'][0]['history'][-1]['delta'], 0)
        self.assertEqual(data['players'][0]['history'][-1]['delta'], data['players'][3]['history'][-1]['delta'])

    def test_final_priors_are_separate_for_ec_and_ccpc_and_do_not_depend_on_raw_team_count(self):
        for series, event, adjustment in [('ICPC', '第 43 届 ICPC 国际大学生程序设计竞赛东亚区决赛', 200),
                                         ('ICPC', '第 40 届 ICPC 国际大学生程序设计竞赛中国区决赛', 200),
                                         ('CCPC', '第 3 届 CCPC 中国大学生程序设计竞赛总决赛', 300)]:
            ordinary = self.calculate([result(medal='铜牌', rank='45 / 100')])['players'][0]['history'][0]
            final = result(medal='铜牌', rank='45 / 100')
            final.update(series=series, event=event)
            h = self.calculate([final])['players'][0]['history'][0]
            self.assertEqual(h['performance'] - ordinary['performance'], adjustment)
            self.assertEqual(h['fieldAdjustment'], adjustment)
            self.assertAlmostEqual(h['delta'] - ordinary['delta'], adjustment / 2)
        unknown = result(medal='')
        self.assertEqual(self.calculate([unknown])['coverage']['ratedResults'], 0)

    def test_unrated_awards_remain_visible_deduplicate_and_do_not_change_rating(self):
        good = result()
        iron = result('iron', '2016-11-01', rank='149', medal='铁牌')
        data = self.calculate([good, iron, {**iron, 'id': 'copy'},
                               dict(result('star', '2017-11-01', medal='铁牌'), official=False)])
        p = data['players'][0]
        self.assertEqual(p['current'], self.calculate([good])['players'][0]['current'])
        self.assertEqual(p['medals'], {'金牌': 1})
        self.assertEqual(p['careerMedals'], {'金牌': 1, '铁牌': 1})
        self.assertEqual(p['careerContests'], 2)
        self.assertEqual(len(p['unratedHistory']), 1)
        # A complete duplicate is chosen over an incomplete copy, regardless of id/order.
        complete = {**iron, 'id': 'z-complete', 'rank': '149 / 245'}
        p = self.calculate([good, iron, complete])['players'][0]
        self.assertEqual(p['contests'], 2)
        self.assertEqual(p['careerContests'], 2)
        self.assertEqual(p['unratedHistory'], [])

    def test_rank_evidence_recomputes_formal_counts_and_ranks_and_matches_exact_identity(self):
        evidence = json.loads((Path(__file__).resolve().parents[1] / 'data/historical_rating_evidence.json').read_text())
        contests = {c['id']: c for c in evidence['contests']}
        self.assertEqual(len(evidence['records']), 6)
        self.assertEqual(evidence['auditScope'], {**evidence['auditScope'], 'missingBefore': 18, 'supplemented': 6, 'remaining': 12})
        for row in evidence['records']:
            c = contests[row['contestId']]
            formal = [t for t in c['scoreEvidence'] if t['official']]
            self.assertEqual(len(formal), row['total'])
            self.assertEqual(c['officialTeams'], row['total'])
            self.assertEqual(len(c['scoreEvidence']), c['totalTeams'])
            rank = 1 + sum(t['solved'] > row['solved'] or t['solved'] == row['solved'] and t['penaltySeconds'] < row['penaltySeconds'] for t in formal)
            self.assertEqual(rank, row['rank'])
            self.assertIn(dict(official=True, solved=row['solved'], penaltySeconds=row['penaltySeconds']), formal)
            h = {**{k: row[k] for k in ('date','event','school','team','medal','members')}, 'id':row['honorId'], 'rank':row['originalRank'], 'series':'CCPC', 'official':True, 'rosterConfirmed':True, 'memberDetails':[dict(id=i) for i in (1,2,3)]}
            self.assertEqual(verified_rank(h, {h['id']:row})[0], (row['rank'],row['total']))
            calculated = self.calculate([h])['players'][0]
            self.assertEqual(calculated['contests'], 1)
            self.assertEqual(calculated['history'][0]['rankSource'], row['source'])
            for field in ('date','event','school','team','medal','rank','members'):
                changed = {**h, field: ['其他成员'] if field == 'members' else '已修改'}
                self.assertIsNone(verified_rank(changed, {h['id']:row})[0])
            self.assertEqual(verified_rank({**h, 'rank':'1 / 100'}, {h['id']:row}), ((1,100),None))

    def test_current_cf_is_reference_only_and_cannot_rewrite_history(self):
        site = dict(members=members(), honors=[result()])
        before = calculate_ratings(site)
        site['members'][0]['handles'] = {'codeforces':dict(handle='x',rating=3000,maxRating=3500,verified=True)}
        after = calculate_ratings(site)
        self.assertEqual(before['inputHash'], after['inputHash'])
        self.assertEqual(before['players'][0]['history'], after['players'][0]['history'])
        self.assertEqual(after['players'][0]['cfReference']['rating'], 3000)

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
