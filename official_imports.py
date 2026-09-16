"""Conservative, additive merges of archived official results."""
from __future__ import annotations

import datetime as dt
import json
import re
import unicodedata

from schools import MAINTENANCE_GROUPS, school_group


ARCHIVE_PROVIDERS = frozenset({"ccpc-official", "icpc-official", "rankland"})
PROVIDER_SERIES = {
    "ccpc-official": {"CCPC"},
    "icpc-official": {"ICPC"},
    "rankland": {"ICPC", "CCPC"},
}
PROVIDER_SOURCE_ORIGINS = {
    "ccpc-official": ("https://ccpc.io/",),
    "icpc-official": ("https://icpc.global/", "https://web.archive.org/"),
    "rankland": ("https://rl.algoux.cn/",),
}


def normalized(value: str) -> str:
    return re.sub(r"[^\w\u4e00-\u9fff]", "", unicodedata.normalize("NFKC", value)).casefold()


def contest_key(record: dict) -> tuple[str, str, str] | None:
    text = record.get("event", "") + " " + record.get("location", "")
    # Final events can be held in the next calendar year. Their season is explicit.
    year = re.search(r"(?:19|20)\d{2}", record.get("event", ""))
    edition = re.search(r"第\s*(\d+)\s*届", record.get("event", ""))
    season = year[0] if year else str(2014 + int(edition[1])) if edition and record.get("series") == "CCPC" else record.get("date", "")[:4]
    if re.search(r"总决赛|final", text, re.I):
        region = "总决赛"
    else:
        region = next((name for name in ("秦皇岛", "哈尔滨", "杭州", "长春", "合肥", "南阳", "桂林", "吉林",
                                        "厦门", "威海", "绵阳", "广州", "深圳", "重庆", "济南", "郑州", "沈阳", "南京",
                                        "银川", "上海", "昆明", "武汉", "西安", "北京", "台北", "南昌", "青岛", "焦作", "徐州", "乌鲁木齐") if name in text), None)
        if not region:
            suffix = re.sub(r"^(?:icpc|ccpc)\d{4}", "", str(record.get("externalContestId") or ""))
            region = {"shenyang": "沈阳", "nanjing": "南京", "shanghai": "上海", "yinchuan": "银川", "nanchang": "南昌",
                      "qingdao": "青岛", "jiaozuo": "焦作", "xuzhou": "徐州", "urumchi": "乌鲁木齐", "xi_an": "西安", "beijing": "北京"}.get(suffix)
    return (record.get("series", ""), season, region) if region else None


def names(record: dict) -> set[str]:
    return {normalized(name) for name in [record.get("team", ""), *record.get("teamAliases", [])] if name}


def ccpc_official_public_pair(left: dict, right: dict) -> bool:
    providers = {left.get("externalProvider"), right.get("externalProvider")}
    official = left if left.get("externalProvider") == "ccpc-official" else right
    return (
        providers == {"ccpc-official", "cpcfinder"}
        and official.get("series") == "CCPC"
        and bool(left.get("date"))
        and left.get("date") == right.get("date")
    )


def match_result(record: dict, existing: list[dict]) -> tuple[str, dict | None, list[str]]:
    key = contest_key(record)
    school = school_group(record.get("school", ""))
    candidates = []
    for item in existing:
        same_day_official_public = ccpc_official_public_pair(record, item)
        same_series = item.get("series") == record.get("series")
        same_contest = (
            same_day_official_public
            or (item.get("date") == record.get("date") and normalized(item.get("event", "")) == normalized(record.get("event", "")))
            or (key and (contest_key(item) == key or (
                item.get("date") == record.get("date") and contest_key(item) and contest_key(item)[2] == key[2]
            )))
            or (not key and item.get("date") == record.get("date")
                and normalized(item.get("event", "")) == normalized(record.get("event", "")))
        )
        if (school_group(item.get("school", "大连理工大学")) == school
                and (same_series or same_day_official_public)
                and (item.get("official") is False) == (record.get("official") is False)
                and same_contest):
            candidates.append(item)
    matches = [item for item in candidates if names(item) & names(record)]
    if not matches:
        roster = {normalized(name) for name in record.get("suggestedMembers", []) if name}
        if len(roster) == 3:
            matches = [item for item in candidates if roster == {normalized(name) for name in item.get("members", [])}]
    if len(matches) > 1:
        return "conflict", None, ["multiple-existing-results:" + ",".join(item["id"] for item in matches)]
    if not matches:
        # A changed team name/roster at an occupied result rank needs a human check.
        rank = str(record.get("rank", "")).split("/")[0].strip()
        occupied = [item for item in candidates if rank and rank.isdigit() and str(item.get("rank", "")).split("/")[0].strip() == rank
                    and not (record.get("externalProvider") == "rankland" and record.get("externalContestId") and item.get("externalContestId") == record["externalContestId"] and item.get("externalProvider") == "rankland")]
        if occupied:
            return "conflict", None, ["same-rank-different-team:" + ",".join(item["id"] for item in occupied)]
        return "added", None, []
    target = matches[0]
    if record.get("medal") and target.get("medal") and target["medal"] != record["medal"]:
        return "conflict", target, ["medal-disagreement"]
    warnings = []
    for field in ("date", "team", "rank", "overallRank"):
        left, right = record.get(field), target.get(field)
        if field in {"rank", "overallRank"}:
            left, right = (str(value or "").split("/")[0].strip() for value in (left, right))
        if left and right and left != right:
            warnings.append(field + "-disagreement")
    roster = {normalized(name) for name in record.get("suggestedMembers", []) if name}
    if target.get("members") and roster and roster != {normalized(name) for name in target["members"]}:
        warnings.append("roster-disagreement-existing-members-preserved")
    return "merged", target, warnings


def _merge_duplicate_honor(connection, source_id: str, target_id: str) -> None:
    source_review = connection.execute(
        "SELECT * FROM honor_roster_reviews WHERE honor_id=?", (source_id,)
    ).fetchone()
    target_review = connection.execute(
        "SELECT * FROM honor_roster_reviews WHERE honor_id=?", (target_id,)
    ).fetchone()
    source_members = connection.execute(
        "SELECT * FROM honor_members WHERE honor_id=? ORDER BY position, member_id", (source_id,)
    ).fetchall()
    target_members = connection.execute(
        "SELECT * FROM honor_members WHERE honor_id=? ORDER BY position, member_id", (target_id,)
    ).fetchall()
    source_confirmed = bool(source_review and source_review["confirmed_at"])
    target_confirmed = bool(target_review and target_review["confirmed_at"])
    source_manual = source_confirmed or any(row["is_manual"] for row in source_members)
    adopt_source_roster = bool(source_members) and (not target_members or (source_manual and not target_confirmed))

    connection.execute(
        "INSERT OR IGNORE INTO honor_sources(honor_id,source_id,role,is_manual) "
        "SELECT ?,source_id,role,is_manual FROM honor_sources WHERE honor_id=?",
        (target_id, source_id),
    )
    connection.execute("UPDATE honor_source_records SET honor_id=? WHERE honor_id=?", (target_id, source_id))

    if adopt_source_roster:
        connection.execute("DELETE FROM honor_members WHERE honor_id=?", (target_id,))
        connection.execute(
            "INSERT INTO honor_members(honor_id,member_id,position,source_id,is_manual) "
            "SELECT ?,member_id,position,source_id,is_manual FROM honor_members WHERE honor_id=?",
            (target_id, source_id),
        )

    if source_review:
        if source_confirmed and not target_confirmed:
            connection.execute("DELETE FROM honor_roster_reviews WHERE honor_id=?", (target_id,))
            connection.execute("UPDATE honor_roster_reviews SET honor_id=? WHERE honor_id=?", (target_id, source_id))
        elif not target_review and not target_members:
            connection.execute("UPDATE honor_roster_reviews SET honor_id=? WHERE honor_id=?", (target_id, source_id))
        else:
            connection.execute("DELETE FROM honor_roster_reviews WHERE honor_id=?", (source_id,))

    target_has_roster = bool(target_members or adopt_source_roster)
    submissions = connection.execute(
        "SELECT id,status,fingerprint FROM roster_submissions WHERE honor_id=? ORDER BY id", (source_id,)
    ).fetchall()
    for submission in submissions:
        duplicate = submission["status"] == "pending" and connection.execute(
            "SELECT 1 FROM roster_submissions WHERE honor_id=? AND fingerprint=? AND status='pending'",
            (target_id, submission["fingerprint"]),
        ).fetchone()
        if submission["status"] == "pending" and (target_has_roster or duplicate):
            connection.execute(
                "UPDATE roster_submissions SET status='superseded',reviewed_at=CURRENT_TIMESTAMP,"
                "review_note='重复成绩合并后名单已存在' WHERE id=?",
                (submission["id"],),
            )
        connection.execute("UPDATE roster_submissions SET honor_id=? WHERE id=?", (target_id, submission["id"]))

    override = connection.execute("SELECT value FROM metadata WHERE key=?", (f"roster_override:{source_id}",)).fetchone()
    if override and adopt_source_roster:
        connection.execute(
            "INSERT INTO metadata(key,value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (f"roster_override:{target_id}", override["value"]),
        )
    connection.execute("DELETE FROM metadata WHERE key=?", (f"roster_override:{source_id}",))
    connection.execute(
        "UPDATE honors SET series='CCPC',updated_at=CURRENT_TIMESTAMP WHERE id=?", (target_id,)
    )
    connection.execute("DELETE FROM honors WHERE id=?", (source_id,))


def reconcile_ccpc_public_duplicates(database, connection) -> int:
    public_ids = {
        row["id"] for row in connection.execute("SELECT id FROM honors WHERE external_provider='cpcfinder'")
    }
    public_ids.update(
        row["honor_id"] for row in connection.execute(
            "SELECT DISTINCT honor_id FROM honor_source_records WHERE provider='cpcfinder'"
        )
    )
    if not public_ids:
        return 0
    payload = database._honors_payload(connection)
    public_results = [item for item in payload if item["id"] in public_ids]
    standalone = connection.execute(
        "SELECT sr.honor_id,sr.record_json FROM honor_source_records sr "
        "JOIN honors h ON h.id=sr.honor_id "
        "WHERE sr.provider='ccpc-official' AND h.external_provider='ccpc-official' "
        "ORDER BY h.date,h.id"
    ).fetchall()
    merged = 0
    for row in standalone:
        record = json.loads(row["record_json"])
        status, target, _ = match_result(record, public_results)
        if status != "merged" or target is None or target["id"] == row["honor_id"]:
            continue
        _merge_duplicate_honor(connection, row["honor_id"], target["id"])
        target["series"] = "CCPC"
        merged += 1
    return merged


def validate_batch(batch: dict) -> None:
    if not isinstance(batch.get("batchId"), str) or not re.fullmatch(r"[a-zA-Z0-9_-]{1,150}", batch["batchId"]):
        raise ValueError("Invalid official import batch ID")
    provider = batch.get("provider", "ccpc-official")
    if provider not in ARCHIVE_PROVIDERS:
        raise ValueError("Invalid archive provider")
    identities = set()
    for record in batch.get("honors", []):
        dt.date.fromisoformat(record["date"])
        unknown = provider == "rankland" and record.get("medal") == "" and record.get("medalStatus") == "unknown"
        if (record.get("medal") not in {"金牌", "银牌", "铜牌", "铁牌"} and not unknown) or record.get("series") not in PROVIDER_SERIES[provider]:
            raise ValueError("Invalid archived result")
        if record.get("school") not in MAINTENANCE_GROUPS or not record.get("team") or not record.get("event"):
            raise ValueError("Missing result identity or invalid maintenance group")
        if record.get("externalProvider") != provider or not record.get("externalAwardId"):
            raise ValueError("Missing official source identity")
        identity = record["externalAwardId"]
        if identity in identities:
            raise ValueError("Duplicate source identity in official batch")
        identities.add(identity)
        if type(record.get("expectedMembers", 3)) is not int or not 1 <= record.get("expectedMembers", 3) <= 3:
            raise ValueError("Invalid official team size")
        if not record.get("source", {}).get("url", "").startswith(PROVIDER_SOURCE_ORIGINS[provider]):
            raise ValueError("Archived result must retain its source")
        if re.search(r"网络|选拔|预选|女生|女子|高职|热身|省赛|省竞赛|邀请赛|地区赛|挑战赛|preliminary|invitational|women|girls", record["event"], re.I):
            raise ValueError("Excluded contest type")


def merge_batch(database, connection, batch: dict, *, dry_run: bool = False) -> dict:
    validate_batch(batch)
    marker = "official_import:" + batch["batchId"]
    previous = connection.execute("SELECT value FROM metadata WHERE key=?", (marker,)).fetchone()
    if previous:
        return {**json.loads(previous["value"]), "alreadyImported": True}
    existing = database._honors_payload(connection)
    report = {"batchId": batch["batchId"], "added": 0, "merged": 0, "conflict": 0, "records": [],
              "source": batch.get("source"), "fetchedAt": batch.get("fetchedAt")}
    for record in batch.get("honors", []):
        identity = connection.execute("SELECT honor_id FROM honor_source_records WHERE provider=? AND external_id=?",
                                      (record["externalProvider"], record["externalAwardId"])).fetchone()
        if identity:
            target = next(item for item in existing if item["id"] == identity["honor_id"])
            status, warnings = "merged", []
        else:
            status, target, warnings = match_result(record, existing)
        outcome = {"status": status, "sourceId": record["externalAwardId"], "honorId": target["id"] if target else record["id"],
                   "date": record["date"], "event": record["event"], "team": record["team"], "medal": record["medal"],
                   "school": record["school"], "warnings": warnings}
        report[status] += 1
        report["records"].append(outcome)
        if status == "conflict":
            continue
        if status == "added":
            if connection.execute("SELECT 1 FROM honors WHERE id=?", (record["id"],)).fetchone():
                raise ValueError("Official result ID collides with an unrelated honor")
            if connection.execute("SELECT 1 FROM honors WHERE external_provider=? AND external_award_id=?",
                                  (record["externalProvider"], record["externalAwardId"])).fetchone():
                raise ValueError("Official source identity collides with an unrelated honor")
            if not dry_run:
                honor_id = database._upsert_honor(connection, {**record, "members": [], "memberDetails": []})
                connection.execute("INSERT INTO honor_roster_reviews(honor_id,batch_id,expected_members,school,original_school,archive_json,suggested_members_json) VALUES (?,?,?,?,?,?,?)",
                                   (honor_id, batch["batchId"], record.get("expectedMembers", 3), record["school"], record.get("originalSchool", ""),
                                    json.dumps(record.get("archive", {}), ensure_ascii=False), json.dumps(record.get("suggestedMembers", []), ensure_ascii=False)))
            existing.append({**record, "members": []})
        honor_id = outcome["honorId"]
        correct_public_series = status == "merged" and ccpc_official_public_pair(record, target) and target.get("series") != "CCPC"
        if correct_public_series:
            target["series"] = "CCPC"
        fill_medal = status == "merged" and not target.get("medal") and record.get("medal")
        if fill_medal:
            target["medal"] = record["medal"]
        if not dry_run:
            if correct_public_series:
                connection.execute(
                    "UPDATE honors SET series='CCPC',updated_at=CURRENT_TIMESTAMP WHERE id=?", (honor_id,)
                )
            if fill_medal:
                connection.execute("UPDATE honors SET medal=?, updated_at=CURRENT_TIMESTAMP WHERE id=? AND medal=''",
                                   (record["medal"], honor_id))
            # Archived evidence persists across public snapshot refreshes. Never
            # replace known fields or the confirmed roster; only fill a missing award.
            for source in database._honor_sources(record):
                source_id = database._source(connection, source)
                connection.execute("INSERT OR IGNORE INTO honor_sources(honor_id,source_id,role) VALUES (?,?,'archive')", (honor_id, source_id))
            connection.execute("INSERT OR IGNORE INTO honor_source_records(provider,external_id,honor_id,record_json) VALUES (?,?,?,?)",
                               (record["externalProvider"], record["externalAwardId"], honor_id, json.dumps(record, ensure_ascii=False)))
    if not dry_run:
        connection.execute("INSERT INTO metadata(key,value) VALUES (?,?)", (marker, json.dumps(report, ensure_ascii=False)))
    return report
