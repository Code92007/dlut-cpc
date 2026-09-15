#!/usr/bin/env python3
"""Archive CCPC's official published standings for offline, duplicate-safe import."""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import quote, urlencode, urlsplit, urlunsplit
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from official_imports import contest_key, match_result, normalized, validate_batch  # noqa: E402
from schools import school_group  # noqa: E402
from tools.import_rankland import parse_initial_state, cached_page  # noqa: E402
ORIGIN = "https://ccpc.io"
# The official site's public frontend uses this read-only CMS API key.
PUBLIC_CMS_KEY = "84dfa45fd954ca8421904123b676c5e2"


def excluded_event(title: str) -> bool:
    return bool(re.search(r"网络|选拔|预选|女生|女子|高职|热身|省赛|省竞赛|邀请赛|地区赛|挑战赛|women|girls|vocational|prelim|warmup", title, re.I))


MEDALS = {"金奖": "金牌", "银奖": "银牌", "铜奖": "铜牌", "金牌": "金牌", "银牌": "银牌", "铜牌": "铜牌",
          "铁牌": "铁牌", "优胜奖": "铁牌", "冠军": "金牌", "亚军": "金牌", "季军": "金牌"}
OLD_KEYS = {"哈尔滨": "harbin", "秦皇岛": "qinhuangdao", "杭州": "hangzhou", "吉林": "jilin", "桂林": "guilin",
            "总决赛": "final", "厦门": "xiamen", "合肥": "hefei", "长春": "changchun", "南阳": "nanyang"}


def season_region(title: str) -> tuple[int, str]:
    year = re.search(r"20\d{2}", title)
    if year:
        season = int(year[0])
    else:
        edition = re.search(r"第\s*([0-9一二三四五六七八九十\ufeff]+)届", title)
        if not edition:
            raise ValueError("Contest season is not explicit: " + title)
        value = edition[1].replace("\ufeff", "")
        number = int(value) if value.isdigit() else {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10, "十一": 11}[value]
        season = 2014 + number
    region = "总决赛" if "总决赛" in title else next((name for name in (*OLD_KEYS, "威海", "绵阳", "广州", "深圳", "重庆", "济南", "郑州") if name in title), None)
    if not region and season == 2015:
        region = "南阳"
    if not region:
        raise ValueError("Unknown regional contest: " + title)
    return season, region


def columns(header: list[str]) -> dict:
    labels = [re.sub(r"\s+", "", label or "") for label in header]
    def find(predicate):
        return next((i for i, label in enumerate(labels) if predicate(label)), None)
    return {"school": find(lambda x: "学校" in x and not re.search(r"排名|名次|排行", x)),
            "team": find(lambda x: ("队名" in x or "团队名" in x or "队伍名" in x or x == "队伍") and not re.search(r"英文|排名|名次", x)),
            "alias": find(lambda x: x in {"英文名", "英文队名"}),
            "medal": find(lambda x: ("奖" in x and "队" not in x) or x == "获奖成绩"),
            "rank": find(lambda x: x in {"排名", "队伍排名", "队伍名次", "序号"}),
            "members": [i for i, label in enumerate(labels) if re.search(r"(?:队员|成员)[一二三123]$", label)]}


def rows_to_results(rows: list[list[str]], post_id: int, *, merged_medals: bool = False) -> tuple[list[dict], list[dict]]:
    layout = columns(rows[0]) if rows else {}
    if post_id == 190:  # This published award table omits its header entirely.
        layout = {"rank": 0, "medal": 2, "school": 3, "team": None, "alias": None, "members": [4, 5, 6]}
    result, unknown, previous_medal = [], [], ""
    for row in rows:
        cells = [re.sub(r"\s+", " ", cell or "").strip() for cell in row]
        get = lambda index: cells[index] if index is not None and index < len(cells) else ""
        if merged_medals and get(layout.get("medal")) in MEDALS:
            previous_medal = get(layout["medal"])
        if not any("大连理工" in cell for cell in cells):
            continue
        school = get(layout.get("school"))
        raw_medal = get(layout.get("medal")) or (previous_medal if merged_medals else "")
        if not school_group(school) or raw_medal not in MEDALS:
            unknown.append({"postId": post_id, "reason": "no-explicit-medal-or-school", "cells": cells})
            continue
        result.append({"school": school_group(school), "originalSchool": school, "team": get(layout.get("team")),
                       "teamAliases": [get(layout.get("alias"))] if get(layout.get("alias")) else [],
                       "medal": MEDALS[raw_medal], "rank": get(layout.get("rank")), "rawAward": raw_medal,
                       "suggestedMembers": [get(index) for index in layout.get("members", []) if get(index)], "rawCells": cells})
    return result, unknown


def compare_snapshot(snapshot: dict, baseline: dict, report_path: Path | None = None) -> dict:
    outcomes = [(record, *match_result(record, baseline["honors"])) for record in snapshot["honors"]]
    counts = {status: sum(item[1] == status for item in outcomes) for status in ("added", "merged", "conflict")}
    print(json.dumps(counts, ensure_ascii=False))
    if not report_path:
        return counts
    def cell(value):
        return str(value or "—").replace("|", "\\|").replace("\n", " ")
    lines = ["# CCPC 官方数据补缺报告", "", f"批次：`{snapshot['batchId']}`。官方归档时间：{snapshot['fetchedAt']}。",
             "", f"对照线上公开数据库：原有 {len(baseline['honors'])} 条成绩、{len(baseline.get('members', []))} 位成员。",
             f"官方明确成绩 {len(outcomes)} 条；新增 {counts['added']} 条，匹配已有 {counts['merged']} 条，冲突跳过 {counts['conflict']} 条。",
             "", "来源：[CCPC 榜单](https://ccpc.io/rank)、[官方获奖名单](https://ccpc.io/award)。",
             "所有来源取并集，包括队内手动补录。同场同队合并来源，不覆盖已有比赛字段、奖项和人工确认名单。",
             "排除网络选拔、女生专场、高职专场、省赛、邀请赛和热身赛。开放区域赛中的女队不被排除。",
             "", "## 新增成绩", "", "新增成绩进入待确认成员队列，姓名只预填，不自动关联到个人。以下名次为官方原表名次，不推算正式队排名或分母。",
             "", "| 日期 | 比赛 | 队伍 | 成绩 | 原表名次 | 参考队员 | 来源 |", "| --- | --- | --- | --- | --- | --- | --- |"]
    for record, status, target, warnings in outcomes:
        if status == "added":
            values = [record["date"], record["event"], record["team"], record["medal"], record["rank"],
                      "、".join(record.get("suggestedMembers", [])), f"[官方名单]({record['source']['url']})"]
            lines.append("| " + " | ".join(cell(value) for value in values) + " |")
    lines += ["", "## 仅合并来源", "", "以下不会新增成绩，不改动线上已确认的队员。", "",
              "| 比赛 | 队伍 | 成绩 | 已有成绩 ID | 官方来源 |", "| --- | --- | --- | --- | --- |"]
    for record, status, target, warnings in outcomes:
        if status == "merged":
            lines.append("| " + " | ".join(cell(value) for value in [record["event"], record["team"], record["medal"],
                                                                  target["id"], f"[名单]({record['source']['url']})"]) + " |")
    lines += ["", "## 差异保留", "", "奖项一致，但原表名次与已有排名可能口径不同。保留已有排名，官方原始行留在数据库归档中，不自动纠正。", "",
              "| 比赛 | 队伍 | 官方原表名次 | 已有排名 | 提醒 |", "| --- | --- | --- | --- | --- |"]
    for record, status, target, warnings in outcomes:
        if warnings:
            lines.append("| " + " | ".join(cell(value) for value in [record["event"], record["team"], record.get("rank"),
                                                                  target.get("rank") if target else "", "; ".join(warnings)]) + " |")
    lines += ["", "## 覆盖限制", "", "部分旧站文章在表格中途截断，未出现的队伍不能据此判定未参赛或铁牌；已完整出现且奖项明确的行仍可归档。",
              "2025 年区域赛及第十一届总决赛的部分 PDF 未标奖项，不按名次猜测铁牌。这批只导入奖项明确的结果，不宣称补齐了所有历史参赛。",
              "优胜奖映射为铁牌，表示未获得金银铜牌。榜单缺失或奖项未知不计为铁牌。城市学院、盘锦校区独立维护；本部与开发区/软件学院归为同一范围。",
              "", "部署以服务器实时 SQLite 再次去重；本报告的新增数量可能随线上补录减少，不可用本地数据库替换线上数据库。",
              "启动只导入此批次一次；成员确认后只移出待确认列表，原成绩及所有来源证据保留。", ""]
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text("\n".join(lines), encoding="utf-8")
    return counts


def build_snapshot(args) -> None:
    baseline = json.loads(args.baseline.read_text(encoding="utf-8")) if args.baseline else {"honors": []}
    records, audits, unknown = [], [], []
    entries = json.loads((args.cache_dir / "catalog-18.json").read_text(encoding="utf-8"))["data"]
    for entry in entries:
        title, post_id = entry["title"], entry["id"]
        if excluded_event(title):
            audits.append({"postId": post_id, "title": title, "excluded": "contest-type"})
            continue
        season, region = season_region(title)
        event = f"{season} CCPC {region}" + ("站" if region != "总决赛" else "")
        stub = {"event": event, "series": "CCPC"}
        reference = None
        if season < 2020 or region == "总决赛":
            suffix = OLD_KEYS.get(region)
            key = f"ccpc{season}{suffix}" if suffix else None
            if key == "ccpc2019harbin":
                key = "ccpc2019haerbin"
            if key:
                page = cached_page(f"https://rl.algoux.cn/ranklist/{key}", args.rankland_cache / f"{key}.html")
                reference = parse_initial_state(page)["ranklistData"]["srk"]
                date = dt.datetime.fromisoformat(reference["contest"]["startAt"].replace("Z", "+00:00")).date().isoformat()
        if reference is None:
            dates = {h["date"] for h in baseline["honors"] if contest_key(h) == contest_key(stub)}
            if len(dates) != 1:
                raise ValueError("No unambiguous contest date: " + event)
            date = dates.pop()
        document = json.loads((args.cache_dir / f"post-{post_id}.json").read_text(encoding="utf-8"))["data"]["archivesInfo"]["content"]
        html = RankHTML()
        html.feed(document)
        parsed, issues = rows_to_results([[c["text"] for c in row["cells"]] for row in html.rows], post_id)
        unknown.extend(issues)
        for index, link in enumerate(html.links):
            if not link.lower().endswith(".pdf"):
                continue
            pdf_file = args.cache_dir / f"post-{post_id}-{index}-extracted.json"
            if not pdf_file.exists():
                raise ValueError("PDF extraction is missing: " + link)
            for page in json.loads(pdf_file.read_text(encoding="utf-8")):
                page_results = []
                for table in page["tables"]:
                    # Most older official PDFs repeat data but not headers across pages.
                    if post_id in {221,223,224,225}:
                        header = {221: ["排名","学校","队名","队员一","队员二","队员三","教练","奖项","过题数"],
                                  223: ["排名","学校","队名","教练","队员一","队员二","队员三","性质","通过数","奖项"],
                                  224: ["排名","学校","队名","队员一","队员二","队员三","教练","过题数","奖项"],
                                  225: ["排名","学校","队名","队员一","队员二","队员三","教练","性质","奖项"]}[post_id]
                    elif post_id in {276,279,282,286}:
                        header = ["排名","奖项","学校","队名"]
                    else:
                        raise ValueError("Unknown award PDF layout: " + str(post_id))
                    found, issues = rows_to_results([header, *table], post_id, merged_medals=True)
                    for item in found:
                        item["pdfPage"] = page["page"]
                        item["pdfUrl"] = link.replace("http://", "https://")
                    page_results.extend(found)
                    unknown.extend(issue for issue in issues if len(issue["cells"]) > 1)
                # Some official PDFs are printed text, not a grid. These two
                # documented layouts have an explicit award on each printed line.
                for line in page["text"].splitlines():
                    item = None
                    if post_id in {276,279,282,286}:
                        match = re.fullmatch(r"(\d+)\s+(金奖|银奖|铜奖|冠军|亚军|季军|优胜奖)\s+(大连理工大学(?:软件学院|开发区校区|盘锦校区|城市学院)?)\s+(.+)", line)
                        if match:
                            rank, award, school, team = match.groups()
                            item = {"rank":rank,"rawAward":award,"school":school_group(school),"originalSchool":school,"team":team,"teamAliases":[],"suggestedMembers":[],"medal":MEDALS[award],"rawCells":[line]}
                    elif post_id == 223:
                        tokens = line.split()
                        if len(tokens) == 10 and tokens[0].isdigit() and school_group(tokens[1]) and tokens[-1] in MEDALS:
                            item = {"rank":tokens[0],"rawAward":tokens[-1],"school":school_group(tokens[1]),"originalSchool":tokens[1],"team":tokens[2],"teamAliases":[],"suggestedMembers":tokens[4:7],"medal":MEDALS[tokens[-1]],"rawCells":tokens}
                    if item and not any(normalized(r["team"]) == normalized(item["team"]) and r["school"] == item["school"] for r in page_results):
                        item.update({"pdfPage":page["page"],"pdfUrl":link.replace("http://","https://")})
                        page_results.append(item)
                parsed.extend(page_results)
        for item in parsed:
            if not item["team"]:
                roster = {normalized(n) for n in item["suggestedMembers"]}
                matches = [r["user"] for r in (reference or {}).get("rows", [])
                           if school_group(str(r["user"].get("organization", ""))) == item["school"]
                           and roster == {normalized(m["name"]) for m in r["user"].get("teamMembers", []) if m.get("role", "contestant") not in {"coach", "reserve"}}]
                if len(matches) == 1:
                    item["team"] = matches[0]["name"]
                    item["teamNameReference"] = f"https://rl.algoux.cn/ranklist/{key}"
                else:
                    peers = [h for h in baseline["honors"] if contest_key(h) == contest_key(stub) and item["school"] == h.get("school", "大连理工大学")
                             and roster == {normalized(n) for n in h.get("members", [])}]
                    if len(peers) == 1 and len(roster) == 3:
                        item["team"] = peers[0]["team"]
                        item["teamNameReference"] = peers[0]["source"]["url"]
            if not item["team"]:
                unknown.append({"postId": post_id, "reason": "team-name-not-resolved", "cells": item["rawCells"]})
                continue
            identity = f"{season}:{region}:{item['school']}:{normalized(item['team'])}"
            record = {"id": "ccpc-official-" + hashlib.sha256(identity.encode()).hexdigest()[:20],
                      "externalProvider": "ccpc-official", "externalAwardId": identity,
                      "externalContestId": f"ccpc{season}:{region}", "event": event, "series": "CCPC", "date": date, "location": region,
                      "team": item["team"], "teamAliases": item["teamAliases"], "school": item["school"], "originalSchool": item["originalSchool"],
                      "medal": item["medal"], "rank": item["rank"], "official": True, "members": [], "expectedMembers": 3,
                      "suggestedMembers": item["suggestedMembers"], "source": {"name": "CCPC 官方获奖名单", "url": f"https://ccpc.io/post/{post_id}"},
                      "archive": {"postId": post_id, "rawAward": item["rawAward"], "rawCells": item["rawCells"],
                                  "pageSha256": hashlib.sha256(document.encode()).hexdigest(),
                                  "pageComplete": "</table>" in document[-1500:],
                                  "dateReference": f"https://rl.algoux.cn/ranklist/{key}" if reference else "existing-public-contest",
                                  **{k:item[k] for k in ("pdfPage", "pdfUrl", "teamNameReference") if k in item}}}
            records.append(record)
        audits.append({"postId": post_id, "title": title, "event": event, "date": date, "results": len(parsed),
                       "pageComplete": "</table>" in document[-1500:] if html.rows else None})
    if len({r["externalAwardId"] for r in records}) != len(records):
        raise ValueError("Duplicate official result in source tables")
    # Also archive the rank directory audit without classifying unlabelled scores.
    for entry in json.loads((args.cache_dir / "catalog-6.json").read_text(encoding="utf-8"))["data"]:
        if excluded_event(entry["title"]):
            continue
        content = json.loads((args.cache_dir / f"post-{entry['id']}.json").read_text(encoding="utf-8"))["data"]["archivesInfo"]["content"]
        html = RankHTML(); html.feed(content)
        audits.append({"postId": entry["id"], "title": entry["title"], "kind": "rank-audit", "pageComplete": "</table>" in content[-1500:],
                       "dlutRows": [[c["text"] for c in row["cells"][:6]] for row in html.rows if any("大连理工" in c["text"] for c in row["cells"])],
                       "note": "Use explicit award lists; ranking without medal labels is not evidence of iron"})
    snapshot = {"batchId": args.batch_id, "source": "https://ccpc.io/award", "rankSource": "https://ccpc.io/rank",
                "fetchedAt": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"), "contests": audits, "unresolved": unknown,
                "honors": sorted(records, key=lambda r:(r["date"],r["team"]))}
    validate_batch(snapshot)
    if args.output.exists() and not args.replace_unimported:
        raise ValueError("Archive already exists; do not silently change an imported batch")
    if args.replace_unimported:
        import sqlite3
        database = ROOT / "runtime/dlut_cpc.sqlite3"
        if database.exists():
            with sqlite3.connect(database) as connection:
                if connection.execute("SELECT 1 FROM metadata WHERE key=?", ("official_import:" + snapshot["batchId"],)).fetchone():
                    raise ValueError("Batch already imported; use a new batch ID for subsequent archives")
    args.output.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    for record in snapshot["honors"]:
        status, target, warnings = match_result(record, baseline["honors"])
        print(status, record["date"], record["event"], record["team"], record["medal"], target["id"] if target else "", warnings)
    print(f"Archived {len(records)} records; unresolved={len(unknown)}; {args.output}")
    compare_snapshot(snapshot, baseline, args.report)


def cached_json(url: str, path: Path) -> dict:
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    request = Request(url, headers={"User-Agent": "DLUT-CPC official archive/1.0", "Accept": "application/json"})
    with urlopen(request, timeout=25) as response:
        data = json.load(response)
    if data.get("status") != 1:
        raise ValueError("CCPC CMS did not return a successful response")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return data


class RankHTML(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.rows = []
        self.links = []
        self.images = []
        self.row = None
        self.cell = None
        self.in_style = False
        self.styles = []

    def handle_starttag(self, tag: str, attrs: list) -> None:
        attributes = dict(attrs)
        if tag == "tr":
            self.row = {"attrs": attributes, "cells": []}
        elif tag in {"td", "th"} and self.row is not None:
            self.cell = {"attrs": attributes, "text": ""}
        elif tag == "a" and attributes.get("href"):
            self.links.append(attributes["href"])
        elif tag == "img" and attributes.get("src"):
            self.images.append(attributes["src"])
        elif tag == "style":
            self.in_style = True
        elif tag == "br" and self.cell is not None:
            self.cell["text"] += "\n"

    def handle_data(self, data: str) -> None:
        if self.cell is not None:
            self.cell["text"] += data
        if self.in_style:
            self.styles.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag in {"td", "th"} and self.cell is not None and self.row is not None:
            self.cell["text"] = self.cell["text"].strip()
            self.row["cells"].append(self.cell)
            self.cell = None
        elif tag == "tr" and self.row is not None:
            self.rows.append(self.row)
            self.row = None
        elif tag == "style":
            self.in_style = False


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache-dir", type=Path, default=ROOT / "runtime/source_cache/ccpc")
    parser.add_argument("--channel", type=int, default=6)
    parser.add_argument("--pdfs", action="store_true")
    parser.add_argument("--build", action="store_true")
    parser.add_argument("--compare", action="store_true", help="Compare the archived snapshot to a saved public /api/site response, without fetching")
    parser.add_argument("--report", type=Path)
    parser.add_argument("--batch-id", default="ccpc-official-union-20260916-v1")
    parser.add_argument("--replace-unimported", action="store_true")
    parser.add_argument("--baseline", type=Path)
    parser.add_argument("--rankland-cache", type=Path, default=ROOT / "runtime/source_cache/rankland")
    parser.add_argument("--output", type=Path, default=ROOT / "data/ccpc_official_honors.json")
    args = parser.parse_args()
    if args.compare:
        if not args.baseline:
            parser.error("--compare requires --baseline")
        compare_snapshot(json.loads(args.output.read_text(encoding="utf-8")), json.loads(args.baseline.read_text(encoding="utf-8")), args.report)
        return
    if args.build:
        build_snapshot(args)
        return
    catalog = cached_json(ORIGIN + "/api/archive?" + urlencode({"apikey": PUBLIC_CMS_KEY, "pageSize": 500, "channel": args.channel, "index": -1}), args.cache_dir / f"catalog-{args.channel}.json")
    entries = catalog["data"]
    def inspect(entry: dict) -> dict:
        if excluded_event(entry["title"]):
            return {"id": entry["id"], "title": entry["title"], "excluded": True}
        post = cached_json(ORIGIN + f"/api/archive/{entry['id']}?" + urlencode({"apikey": PUBLIC_CMS_KEY}), args.cache_dir / f"post-{entry['id']}.json")
        content = post["data"]["archivesInfo"]["content"]
        html = RankHTML()
        html.feed(content)
        pdf_reports = []
        if args.pdfs:
            import pdfplumber
            for index, link in enumerate(html.links):
                if not link.lower().endswith(".pdf"):
                    continue
                parsed = urlsplit(link)
                url = urlunsplit(("https", parsed.netloc, quote(parsed.path.replace("//", "/")), parsed.query, ""))
                pdf_path = args.cache_dir / f"post-{entry['id']}-{index}.pdf"
                if not pdf_path.exists():
                    with urlopen(Request(url, headers={"User-Agent": "DLUT-CPC official archive/1.0"}), timeout=45) as response:
                        pdf_path.write_bytes(response.read())
                pdf_data = []
                with pdfplumber.open(pdf_path) as pdf:
                    for page in pdf.pages:
                        text = page.extract_text() or ""
                        if "大连理工" in text:
                            tables = page.extract_tables()
                            pdf_data.append({"page": page.page_number, "text": text, "tables": tables})
                (args.cache_dir / f"post-{entry['id']}-{index}-extracted.json").write_text(json.dumps(pdf_data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
                pdf_reports.append({"url": url, "pages": [{"page": p["page"], "header": p["text"].splitlines()[:3], "dlut": [line for line in p["text"].splitlines() if "大连理工" in line]} for p in pdf_data]})
        return {"id": entry["id"], "title": entry["title"], "length": len(content), "rows": len(html.rows),
                "dlutRows": [[c["text"] for c in row["cells"]] for row in html.rows if any("大连理工" in cell["text"] for cell in row["cells"])],
                "header": [c["text"] for c in html.rows[0]["cells"][:8]] if html.rows else None, "links": html.links, "images": html.images,
                "closedTable": "</table>" in content[-1500:], "pdf": pdf_reports}
    with ThreadPoolExecutor(max_workers=3) as pool:
        for report in pool.map(inspect, entries):
            print(json.dumps(report, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
