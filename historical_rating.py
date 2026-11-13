"""Versioned, deterministic Elo-scale ratings from confirmed local results.

Each result supplies an award/rank performance; a player's prior determines
their signed update. No accumulated medal points or external rating is used.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import math
import re
from collections import Counter, defaultdict
from pathlib import Path

VERSION = "dlut-award-performance-v2"
INITIAL = 1500
SCALE = 400
MIN_CONTESTS = 3
ROOT = Path(__file__).resolve().parent

# Actual awards select disjoint bands. Nominal percentile intervals only place
# the official rank within that band; they never infer or override an award.
PERFORMANCE_BANDS = {
    "金牌": (2600, 3200, 0, .1), "银牌": (1900, 2400, .1, .3),
    "铜牌": (1300, 1800, .3, .6), "铁牌": (800, 1200, .6, 1),
}


def result_performance(medal, rank, total):
    low, high, start, end = PERFORMANCE_BANDS[medal]
    position = max(0, min(1, ((rank - .5) / total - start) / (end - start)))
    return high - (high - low) * position


def event_adjustment(honor):
    # A declared prior for qualification strength, not a fitted field estimate.
    # Apply to every award, and before updating each person's prior rating.
    return 300 if re.search(r"东亚区决赛|中国区决赛|总决赛", honor["event"]) else 0


def rank_fraction(value):
    match = re.fullmatch(r"\s*([1-9]\d*)\s*/\s*([1-9]\d*)\s*", str(value or ""))
    if match:
        rank, total = map(int, match.groups())
        if 1 <= rank <= total and total >= 2:
            return rank, total
    return None


def reference_band(contests):
    # A deliberately broad reference band; not a calibrated confidence interval.
    return round(max(150, 400 / math.sqrt(1 + contests / 3)))


def calculate_ratings(site):
    members = {m["id"]: m for m in site["members"]}
    players = {key: dict(rating=float(INITIAL), history=[], teams=set()) for key in members}
    eligible, skipped, seen = [], [], set()
    inputs = []
    for honor in sorted(site["honors"], key=lambda h: (h["date"], h["event"], h["id"])):
        reason = ""
        ids = [m.get("id") for m in honor.get("memberDetails", [])]
        ranks = rank_fraction(honor.get("rank"))
        try:
            dt.date.fromisoformat(honor["date"])
        except (ValueError, TypeError):
            reason = "日期不完整"
        if honor.get("series") not in {"ICPC", "CCPC"} or not re.search(r"区域赛|总决赛|区决赛|程序设计竞赛.+站$", honor.get("event", "")) or re.search(r"邀请|女生|网络|预选|省赛|世界总决赛", honor.get("event", "")):
            reason = "不在正式区域赛与区决赛范围"
        elif honor.get("official") is not True:
            reason = "打星或正式资格未确认"
        elif not honor.get("rosterConfirmed") or len(ids) != 3 or len(set(ids)) != 3 or any(i not in members for i in ids):
            reason = "三人名单未完整确认"
        elif not ranks:
            reason = "缺少有效的正式名次 / 正式队总数"
        elif honor.get("medal") not in PERFORMANCE_BANDS:
            reason = "实际奖项未确认"
        key = (honor["date"], honor["event"], tuple(sorted(ids))) if not reason else None
        if key in seen:
            reason = "同场同名单重复记录"
        if reason:
            skipped.append(dict(id=honor["id"], event=honor["event"], date=honor["date"], team=honor["team"], reason=reason))
            continue
        seen.add(key)
        eligible.append((honor, ids, ranks))

    # Freeze all priors at the beginning of each date. Reject impossible identity
    # collisions rather than allowing arbitrary event ordering to change scores.
    dates = defaultdict(list)
    for record in eligible:
        dates[record[0]["date"]].append(record)
    used = 0
    for date, records in sorted(dates.items()):
        appearances = Counter(i for _, ids, _ in records for i in ids)
        for honor, ids, (rank, total) in records:
            if any(appearances[i] > 1 for i in ids):
                skipped.append(dict(id=honor["id"], event=honor["event"], date=date, team=honor["team"], reason="同日多队身份冲突，待核验"))
                continue
            prior = sum(players[i]["rating"] for i in ids) / 3
            base_performance = result_performance(honor["medal"], rank, total)
            adjustment = event_adjustment(honor)
            performance = base_performance + adjustment
            for member_id in ids:
                player = players[member_id]
                count = len(player["history"])
                step = .5 if count < 3 else .3
                before = player["rating"]
                player["rating"] += step * (performance - before)
                player["teams"].add(tuple(sorted(ids)))
                player["history"].append(dict(
                    honorId=honor["id"], date=date, event=honor["event"], team=honor["team"],
                    members=[members[i]["name"] for i in ids], rank=rank, total=total,
                    percentile=round(100 * (rank - 0.5) / total, 2),
                    medal=honor["medal"], performance=round(performance, 2), teamPrior=round(prior),
                    basePerformance=round(base_performance, 2), fieldAdjustment=adjustment,
                    updateWeight=step,
                    before=round(before, 2), delta=round(player["rating"] - before, 2),
                    rating=round(player["rating"], 2), band=reference_band(count + 1),
                    source=honor.get("source"),
                ))
            inputs.append({k: honor.get(k) for k in ["id", "date", "event", "rank", "medal", "official", "memberDetails"]})
            used += 1
    result = []
    for key, member in members.items():
        player = players[key]
        history = player["history"]
        count = len(history)
        # A peak only becomes eligible after three actual observations, even if
        # the first contest happened to yield the highest score.
        mature = history[MIN_CONTESTS - 1:]
        peak = max(mature, key=lambda h: h["rating"]) if mature else None
        band = reference_band(count) if count else None
        result.append(dict(
            id=key, name=member["name"], school=member["school"], status=member.get("status"),
            contests=count, teamCount=len(player["teams"]), provisional=count < MIN_CONTESTS,
            medals=dict(Counter(h["medal"] for h in history)),
            current=round(player["rating"]) if count else None,
            peak=round(peak["rating"]) if peak else None, peakDate=peak["date"] if peak else None,
            conservative=round(player["rating"] - band) if count else None, band=band,
            firstDate=history[0]["date"] if count else None, lastDate=history[-1]["date"] if count else None,
            history=history,
        ))
    digest = hashlib.sha256(json.dumps(inputs, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    return dict(version=VERSION, initial=INITIAL, scale=SCALE, minContests=MIN_CONTESTS,
                rules=dict(performanceBands=PERFORMANCE_BANDS, finalsAdjustment=300,
                           provisionalWeight=.5, matureWeight=.3),
                inputHash=digest, players=result, skipped=skipped,
                coverage=dict(totalResults=len(site["honors"]), ratedResults=used,
                              ratedPlayers=sum(p["contests"] > 0 for p in result),
                              rankedPlayers=sum(not p["provisional"] for p in result),
                              missingPlayers=sum(p["contests"] == 0 for p in result),
                              firstDate=min(dates, default=None), lastDate=max(dates, default=None)))


def analytics_payload(site):
    archive = json.loads((ROOT / "data/team_performances.json").read_text(encoding="utf-8"))
    honors = {h["id"]: h for h in site["honors"]}
    records = []
    for row in archive["records"]:
        honor = honors.get(row.get("honorId"))
        records.append({**row, "honor": {key: honor.get(key) for key in [
            "id", "event", "date", "team", "school", "members", "rank", "medal", "official", "source"
        ]} if honor else None})
    return dict(performances={**archive, "records": records}, elo=calculate_ratings(site))
