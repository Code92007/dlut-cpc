"""Build the reviewed archive from the original workbook and pinned evidence.

Build-only dependency: openpyxl. No network calls. See docs/historical-rating.md
for the input layout. Never modifies the workbook or the live results database.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import datetime as dt
import hashlib
import json
from pathlib import Path
import re
import unicodedata

SITES = {'西安': 'xian', '长春': 'changchun', '成都': 'chengdu',
         '武汉': 'wuhan', '乐山': 'leshan', '南京': 'nanjing'}
STAGES = ['铁牌', '速铜', '稳铜', '速银', '稳银', '速金', '稳金']
MEDALS = {'gold': '金牌', 'silver': '银牌', 'bronze': '铜牌'}


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def norm(value):
    return ''.join(c for c in unicodedata.normalize('NFKC', value)
                   if not unicodedata.category(c).startswith(('P', 'Z')) and not c.isspace())


def localized(value):
    if isinstance(value, dict):
        texts = value.get('texts', {})
        return texts.get('zh-CN') or texts.get(value.get('fallback_lang')) or next(iter(texts.values()), '')
    return value or ''


def read_board(folder, site):
    """Recover only identities and actual contest-time AC; no practice solves."""
    read = lambda name: json.loads((folder / f'{site}-{name}.json').read_text())
    accepted = defaultdict(set)
    if site in {'changchun', 'leshan'}:
        data = read('rank')
        start, end = (dt.datetime.fromisoformat(data['contest'][k]) for k in ('start_time', 'end_time'))
        labels = {str(p['problem_id']): chr(65 + int(p['num'])) for p in data['problem']}
        final = {str(s['solution_id']): s for s in data['solution']}
        for s in final.values():
            if start <= dt.datetime.fromisoformat(s['in_date']) < end and int(s['result']) == 4:
                accepted[str(s['team_id'])].add(labels[str(s['problem_id'])])
        teams = [dict(id=str(t['team_id']), team=t['name'], school=t['school'],
                      official=int(t['tkind']) in {0, 1}) for t in data['team']]
        hashes = {'rank': digest(folder / f'{site}-rank.json')}
    else:
        config, team, runs = read('config'), read('team'), read('run')
        unit = 1000 if config['start_time'] > 100_000_000_000 else 1
        duration = (config['end_time'] - config['start_time']) / unit
        run_unit = 1000 if config.get('options', {}).get('submission_timestamp_unit') == 'millisecond' else 1
        labels = {str(p['id']): p['label'] for p in config['problems']}
        final = {str(s.get('id', s.get('submission_id', i))): s for i, s in enumerate(runs)}
        for s in final.values():
            if 0 <= s['timestamp'] / run_unit < duration and not s.get('is_ignore', s.get('ignore', False)) and s['status'].upper() in {'ACCEPTED', 'AC', 'OK', 'CORRECT'}:
                accepted[str(s['team_id'])].add(labels[str(s['problem_id'])])
        organizations = {str(o['id']): localized(o['name']) for o in read('organizations')}
        teams = [dict(id=str(t['id']), team=localized(t['name']),
                      school=localized(t.get('organization')) or organizations.get(str(t.get('organization_id')), ''),
                      official='official' in t.get('group', []) and 'unofficial' not in t.get('group', [])) for t in team]
        hashes = {k: digest(folder / f'{site}-{k}.json') for k in ('config', 'team', 'run', 'organizations')}
    for t in teams:
        t['accepted'] = sorted(accepted[t['id']])
    return teams, hashes


def target_plans(accepted, ratings, stats):
    plans = {}
    for medal, cn in MEDALS.items():
        for mode, prefix in [('speed', '速'), ('safe', '稳')]:
            count = stats['medal_boundaries'][medal]['last_solved'] if mode == 'speed' else stats['medal_safety'][medal]['safe_solved']
            superseded = mode == 'safe' and any(count >= stats['medal_safety'][higher]['safe_solved'] for higher in list(MEDALS)[:list(MEDALS).index(medal)])
            todo = sorted((r, p) for p, r in ratings.items() if p not in accepted)
            missing = max(0, count - len(accepted))
            additions = todo[:missing]
            available = not superseded and len(additions) == missing
            stage = prefix + cn[0]
            value = max((r for r, _ in additions), default=None)
            text = (f'{stage} {value}' if missing else f'{stage}·仅罚时') if available else ''
            plans[stage] = dict(count=count, additional=missing, available=available,
                                problems=[dict(index=p, rating=r) for r, p in additions], text=text)
    return plans


def build(workbook, report, live, catalog, evidence, anonymous):
    import openpyxl
    original = json.loads(report.read_text())
    site = json.loads(live.read_text())
    current = json.loads(catalog.read_text())
    report_rows = original['records']
    records = []
    sheet = openpyxl.load_workbook(workbook, data_only=True)['阶段速览']
    def display(cell):
        if cell.value is None:
            return ''
        if isinstance(cell.value, (int, float)):
            prefix = re.match(r'^"([^"]*)"', cell.number_format)
            return (prefix[1] if prefix else '') + str(cell.value)
        return str(cell.value)
    for number in range(2, sheet.max_row + 1):
        cells = sheet[number]
        title, team, rating, stage, next_stage, next_two = [display(c) for c in cells[:6]]
        match = re.fullmatch(r'(\d{4})(ICPC|CCPC)(.+)', title)
        year, series, location = match.groups()
        suffix = {'总决赛': 'Final', '东亚决赛': 'ECFinal'}.get(location, location)
        matches = [r for r in report_rows if r['school'] == '大连理工大学'
                   and r['date'][:4] == year and series in r['event']
                   and ((suffix == 'Final' and '总决赛' in r['event'])
                        or (suffix == 'ECFinal' and '东亚区决赛' in r['event'])
                        or r['event'].endswith(location + '站'))
                   and norm(r['team']) == norm(team.rstrip('*'))
                   and r.get('max_rating') == cells[2].value
                   and (not stage or stage.endswith('*') or r['medal'][0] == ('铁' if stage == '铁牌' else stage[-1]))]
        if len(matches) != 1:
            raise ValueError(f'Workbook row {number}: {len(matches)} evidence matches')
        r = matches[0]
        assert r.get('max_rating') == cells[2].value, (number, r['team'])
        linked = [h for h in site['honors'] if h['date'] == r['date'] and h['school'] == r['school'] and norm(h['team']) == norm(r['team'])]
        assert len(linked) == 1, (number, r['team'], 'live identity')
        records.append(dict(id='performance-' + r['id'], honorId=linked[0]['id'], sourceRow=number,
                            sourceKind='workbook', season=int(year), contestSeason=r['season'], series=series,
                            location=location, team=team, school=r['school'], date=r['date'],
                            maxSolvedRating=cells[2].value, stage=stage, nextStage=next_stage, nextTwoStages=next_two,
                            solved=r.get('solved'), accepted=r.get('accepted', ''),
                            boardTeamId=r.get('board_team_id'), boardUrl=r['board_url'],
                            allTargets=r.get('all_targets', {}), note=r['status']))
    stations = []
    for contest in current['contests']:
        if contest['year'] != 2026 or contest['site'] not in SITES:
            continue
        key, slug = contest['id'], SITES[contest['site']]
        teams, hashes = read_board(evidence, slug)
        frozen_path = anonymous / contest['rating_evidence']['standings_path']
        frozen = json.loads(frozen_path.read_text())
        fingerprint = lambda t: (t['official'], tuple(sorted(t['accepted'])))
        assert Counter(map(fingerprint, teams)) == Counter(map(fingerprint, frozen['teams'])), (key, 'full-field AC/eligibility mismatch')
        stats = current['ratingRelease']['contests'][key]
        ratings = {p['index']: current['difficulty'][p['id']]['rating'] for p in contest['problems']}
        station = dict(id=key, date=contest['date'], ratings=ratings, statistics=stats,
                       sourceHashes=hashes, anonymousHash=digest(frozen_path), boardUrl=contest['ranklist_url'])
        stations.append(station)
        honors = [h for h in site['honors'] if h['date'] == contest['date'] and h['series'] == contest['series'] and h['location'] == contest['site']]
        for h in honors:
            matched = [t for t in teams if norm(t['team']) == norm(h['team']) and norm(t['school']) == norm(h['school'])]
            assert len(matched) == 1, (key, h['team'], 'not unique')
            t = matched[0]
            assert t['official'] == h['official']
            plans = target_plans(t['accepted'], ratings, stats)
            medal = h['medal']
            if not h['official']:
                stage = ''
            elif medal == '铁牌':
                stage = '铁牌'
            else:
                medal_key = next(k for k, cn in MEDALS.items() if cn == medal)
                stage = ('稳' if len(t['accepted']) >= stats['medal_safety'][medal_key]['safe_solved'] else '速') + medal[0]
            index = STAGES.index(stage) if stage else len(STAGES)
            def following(offset):
                return plans.get(STAGES[index + offset], {}).get('text', '') if index + offset < len(STAGES) else ''
            records.append(dict(id='performance-' + h['id'], honorId=h['id'], sourceRow=None, sourceKind='cf-bot-2026',
                                season=2026, contestSeason=2026, series=h['series'], location=h['location'], school=h['school'],
                                date=h['date'], team=h['team'], maxSolvedRating=max((ratings[p] for p in t['accepted']), default=None),
                                stage=stage, nextStage=following(1), nextTwoStages=following(2),
                                solved=len(t['accepted']), accepted=', '.join(f'{p}({ratings[p]})' for p in t['accepted']),
                                boardTeamId=t['id'], boardUrl=contest['ranklist_url'], allTargets=plans, contestId=key,
                                note='赛时 AC 与 CF-Bot 完整正式/打星逐题快照全场多重集核对；阶段按正式队终榜门槛计算。' +
                                ('乐山原榜 300 支正式队，成绩库为 CPC Finder 299 支口径；此处升级门槛用原榜。' if slug == 'leshan' else '')))
    return dict(version='team-performances-v2', updatedAt='2026-11-14',
                source=dict(name=workbook.name, note='原工作簿全部 160 条主校区记录；另补 2026 年六个已完赛区域站，含盘锦校区。原表空白与打星标记保留。',
                            workbookHash=digest(workbook), reportHash=digest(report), catalogHash=digest(catalog),
                            catalogUrl='https://cf-bot.wannafly.cn/api/regional-catalog', snapshotDate=original['as_of'],
                            sheet='阶段速览', workbookRecords=160), stations=stations, records=records)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('workbook', 'report', 'live', 'catalog', 'evidence', 'anonymous', 'output'):
        parser.add_argument('--' + name, type=Path, required=True)
    args = vars(parser.parse_args())
    output = args.pop('output')
    archive = build(**args)
    output.write_text(json.dumps(archive, ensure_ascii=False, indent=2) + '\n')
    print(f"Wrote {len(archive['records'])} records, {len(archive['stations'])} new stations")
