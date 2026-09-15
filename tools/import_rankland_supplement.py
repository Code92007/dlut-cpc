#!/usr/bin/env python3
"""Archive selected RankLand contests, including participation with unknown awards."""
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
from pathlib import Path

from import_rankland import ROOT, ORIGIN, cached_page, parse_ranklist


def write_report(batch: dict, baseline: dict, path: Path) -> None:
    from official_imports import match_result
    outcomes = []
    for record in batch["honors"]:
        status, target, warnings = match_result(record, baseline["honors"])
        outcomes.append({**record, "status": status, "targetId": target["id"] if target else "", "warnings": warnings})
    cell = lambda value: str(value).replace("|", "\\|").replace("\n", " ")
    lines = ["# RankLand 补缺清单", "", "本清单与生成时线上公开数据比对。生产数据库实时预览/导入报告为最终结果；新增手工补录也参与去重。", "",
             f"检查 {len(batch['contests'])} 场；归档 {len(outcomes)} 条；新增 {sum(r['status']=='added' for r in outcomes)} 条；追加来源 {sum(r['status']=='merged' for r in outcomes)} 条；冲突 {sum(r['status']=='conflict' for r in outcomes)} 条。", "",
             "## 按场次", "", "| 榜单 | 日期 | 新增队伍 | 已有/追加来源 | 冲突 |", "| --- | --- | --- | --- | --- |"]
    for contest in batch["contests"]:
        rows = [r for r in outcomes if r["externalContestId"] == contest["key"]]
        names = lambda status: "、".join(r["team"] for r in rows if r["status"] == status) or "无"
        lines.append("| " + " | ".join(cell(value) for value in [f"[{contest['key']}]({contest['url']})", contest["date"], names("added"), names("merged"), names("conflict")]) + " |")
    lines += ["", "## 逐条结果", "", "| 操作 | 榜单 | 队伍 | 校区 | 成绩 | 排名 | 已有 ID | 说明 |", "| --- | --- | --- | --- | --- | --- | --- | --- |"]
    labels = {"added": "新增", "merged": "追加来源", "conflict": "跳过冲突"}
    for record in outcomes:
        award = "打星（不计牌）" if record.get("official") is False else record["medal"] or "奖项待确认"
        lines.append("| " + " | ".join(cell(value) for value in [labels[record["status"]], record["externalContestId"], record["team"], record["school"], award, record["rank"], record["targetId"], "; ".join(record["warnings"])]) + " |")
    lines += ["", "## 统计口径", "", "来源奖牌边界缺失/全为零时，导入参赛记录而不猜测奖项，显示“奖项待确认”，不计为铁牌或获奖。管理员确认成员时可同时补齐奖项；也可在已补录名单中补齐。",
              "打星保留参赛记录，但不进入年度正式奖牌统计、成员正式奖牌/铁牌次数或获奖次数。2011 大连站所有本校队伍按队内确认标记打星。",
              "新记录的榜单队员仅作为建议名单，不根据其他场次的同名队伍自动链接选手。已有字段、确认名单、账号和人工来源保持不变。", ""]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rank-id", action="append", required=True)
    parser.add_argument("--cache-dir", type=Path, default=ROOT / "runtime/source_cache/rankland-supplement")
    parser.add_argument("--output", type=Path, default=ROOT / "data/rankland_supplement_honors.json")
    parser.add_argument("--batch-id", default="rankland-supplement-20260916-v1")
    parser.add_argument("--baseline", type=Path)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    if args.output.exists():
        raise SystemExit("Snapshot already exists; create a new batch, do not replace an imported archive")
    honors, contests = [], []
    for key in dict.fromkeys(args.rank_id):
        if not re.fullmatch(r"(?:icpc|ccpc)\d{4}[a-zA-Z0-9_-]+", key):
            raise SystemExit("Invalid contest key")
        document = cached_page(f"{ORIGIN}/ranklist/{key}", args.cache_dir / f"{key}.html")
        records, audit = parse_ranklist(document, before_date=None, include_participation=True)
        honors.extend(records)
        contests.append(audit)
        print(f"{key}: {len(records)} results; {sum(not r['medal'] for r in records)} awards unknown", flush=True)
    batch = {"batchId": args.batch_id, "provider": "rankland", "source": f"{ORIGIN}/collection/official",
             "fetchedAt": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
             "contests": contests, "honors": honors}
    from official_imports import validate_batch
    validate_batch(batch)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(batch, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if args.baseline and args.report:
        write_report(batch, json.loads(args.baseline.read_text(encoding="utf-8")), args.report)


if __name__ == "__main__":
    main()
