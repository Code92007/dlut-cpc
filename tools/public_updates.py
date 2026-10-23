from __future__ import annotations

import copy
import hashlib
import json
from collections import Counter

from contest_names import honor_contest_name
from database import Database
from official_imports import contest_key, match_result, normalized
from schools import school_group


RESULT_FIELDS = ("event", "series", "date", "team", "medal", "rank", "overallRank", "official", "members")
RESULT_COLUMNS = {"event": "event", "series": "series", "date": "date", "team": "team", "medal": "medal",
                  "rank": "rank", "overallRank": "overall_rank", "official": "official"}


def source_identity(record: dict) -> tuple[str, str]:
    if record.get("cpcfinderAwardId") is not None:
        return "cpcfinder", str(record["cpcfinderAwardId"])
    return str(record["externalProvider"]), str(record["externalAwardId"])


def result_values(record: dict) -> dict:
    result = {field: record.get(field) for field in RESULT_FIELDS}
    result["event"] = honor_contest_name(record)
    result["members"] = record.get("members") or record.get("suggestedMembers") or []
    if record.get("official") is False and result["medal"] == "铁牌":
        result["medal"] = ""
    return result


def different_fields(before: dict, after: dict) -> list[str]:
    left, right = result_values(before), result_values(after)
    fields = []
    for field in RESULT_FIELDS:
        if right[field] is None or (field == "members" and not right[field]):
            continue
        if field == "medal" and not right[field] and after.get("medalStatus") == "unknown":
            continue
        if field == "members":
            before_local = [detail.get("id") for detail in before.get("memberDetails", [])]
            after_local = [detail.get("id") for detail in after.get("memberDetails", [])]
            before_ids = [detail.get("externalId") for detail in before.get("memberDetails", []) if detail.get("provider") == "cpcfinder"]
            after_ids = [detail.get("externalId") for detail in after.get("memberDetails", []) if detail.get("provider") == "cpcfinder"]
            if before_local and after_local and all(before_local) and all(after_local) and len(before_local) == len(left[field]) and len(after_local) == len(right[field]):
                equal = sorted(before_local) == sorted(after_local)
            elif before_ids and after_ids and all(before_ids) and all(after_ids) and len(before_ids) == len(left[field]) and len(after_ids) == len(right[field]):
                equal = sorted(before_ids) == sorted(after_ids)
            else:
                equal = sorted(normalized(name) for name in left[field]) == sorted(normalized(name) for name in right[field])
        elif field == "team":
            equal = normalized(left[field] or "") == normalized(right[field] or "")
        elif field == "official":
            equal = (left[field] is not False) == (right[field] is not False)
        else:
            equal = str(left[field] or "").strip() == str(right[field] or "").strip()
        if not equal:
            fields.append(field)
    return fields


def _guard(connection, honor_id: str) -> None:
    connection.execute("INSERT OR IGNORE INTO metadata(key,value) VALUES (?, '1')", (f"public_result_guard:{honor_id}",))


def _attach_source(database: Database, connection, honor_id: str, record: dict) -> None:
    provider, external_id = source_identity(record)
    for source in database._honor_sources(record):
        source_id = database._source(connection, source)
        connection.execute("INSERT OR IGNORE INTO honor_sources(honor_id,source_id,role) VALUES (?,?,'archive')", (honor_id, source_id))
    connection.execute("INSERT OR IGNORE INTO honor_source_records(provider,external_id,honor_id,record_json) VALUES (?,?,?,?)",
                       (provider, external_id, honor_id, json.dumps(record, ensure_ascii=False)))


def _add_result(database: Database, connection, record: dict) -> str:
    honor_id = database._upsert_honor(connection, record)
    _attach_source(database, connection, honor_id, record)
    if source_identity(record)[0] == "rankland":
        connection.execute(
            "INSERT OR IGNORE INTO honor_roster_reviews(honor_id,batch_id,expected_members,school,original_school,archive_json,suggested_members_json) "
            "VALUES (?, 'rankland-daily', ?, ?, ?, ?, ?)",
            (honor_id, record.get("expectedMembers", 3), record.get("school", "大连理工大学"), record.get("originalSchool", ""),
             json.dumps(record.get("archive", {}), ensure_ascii=False), json.dumps(record.get("suggestedMembers", []), ensure_ascii=False)),
        )
    _guard(connection, honor_id)
    return honor_id


def _queue_diff(connection, target: dict | None, record: dict, fields: list[str]) -> bool:
    provider, external_id = source_identity(record)
    before = target or {}
    identities = [[(detail.get("provider"), detail.get("externalId")) for detail in item.get("memberDetails", [])] for item in (before, record)]
    fingerprint = hashlib.sha256(json.dumps([result_values(before), result_values(record), fields, identities],
                                           ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    connection.execute(
        "UPDATE public_result_diffs SET status='superseded',reviewed_at=CURRENT_TIMESTAMP "
        "WHERE provider=? AND external_id=? AND status='pending' AND fingerprint<>?",
        (provider, external_id, fingerprint),
    )
    cursor = connection.execute(
        "INSERT OR IGNORE INTO public_result_diffs(honor_id,provider,external_id,fingerprint,before_json,after_json,fields_json) "
        "VALUES (?,?,?,?,?,?,?)",
        (target["id"] if target else None, provider, external_id, fingerprint, json.dumps(before, ensure_ascii=False),
         json.dumps(record, ensure_ascii=False), json.dumps(fields, ensure_ascii=False)),
    )
    return bool(cursor.rowcount)


def _target_for_record(connection, record: dict, existing: list[dict]) -> tuple[dict | None, bool]:
    provider, external_id = source_identity(record)
    identity = connection.execute(
        "SELECT honor_id FROM public_result_sources WHERE provider=? AND external_id=? "
        "UNION SELECT honor_id FROM honor_source_records WHERE provider=? AND external_id=? "
        "UNION SELECT id FROM honors WHERE external_provider=? AND external_award_id=?",
        (provider, external_id, provider, external_id, provider, external_id),
    ).fetchall()
    targets = [item for item in existing if item["id"] in {row[0] for row in identity}]
    if targets:
        return (targets[0], False) if len(targets) == 1 else (None, True)
    match, target, _ = match_result({"school": "大连理工大学", **record}, existing)
    if not target:
        related = [item for item in existing if normalized(item.get("team", "")) == normalized(record.get("team", ""))
                   and school_group(item.get("school", "大连理工大学")) == school_group(record.get("school", "大连理工大学"))
                   and item.get("series") == record.get("series")
                   and ((contest_key(record) and contest_key(item) == contest_key(record))
                        or (item.get("date") == record.get("date") and item.get("event") == honor_contest_name(record)))]
        if len(related) == 1:
            return related[0], False
        if len(related) > 1:
            return None, True
    return target, match == "conflict" and target is None


def _member_contributions(record: dict, identities: dict | None = None) -> Counter:
    result = Counter()
    if record.get("official") is False or record.get("medal") not in {"金牌", "银牌", "铜牌", "铁牌"}:
        return result
    for member in record.get("memberDetails", []):
        if member.get("provider") == "cpcfinder" and member.get("externalId"):
            identity = identities.get(member["externalId"], member["externalId"]) if identities is not None else member["externalId"]
            result[(identity, record["medal"])] += 1
    return result


def _refresh_member_stats(database: Database, connection, public_members: list[dict], pairs: list[tuple[dict, dict]]) -> None:
    identities = dict(connection.execute("SELECT external_id,member_id FROM member_identities WHERE provider='cpcfinder'"))
    adjustments = Counter()
    for incoming, accepted in pairs:
        adjustments.subtract(_member_contributions(incoming, identities))
        adjustments.update(_member_contributions(accepted, identities))
    members = copy.deepcopy(public_members)
    fields = {"金牌": "goldCount", "银牌": "silverCount", "铜牌": "bronzeCount", "铁牌": "ironCount"}
    for member in members:
        stats = member.setdefault("cpcfinder", {})
        for medal, field in fields.items():
            if stats.get(field) is not None:
                identity = identities.get(member["externalId"], member["externalId"])
                stats[field] = max(0, int(stats[field]) + adjustments[(identity, medal)])
    database._sync_site_data(connection, {"meta": {}, "honors": [], "publicMembers": members, "publicSyncRefresh": True})


def ingest_public_results(database: Database, records: list[dict], *, public_members: list[dict] | None = None) -> dict:
    report = {"added": 0, "unchanged": 0, "diffsCreated": 0, "addedResults": []}
    with database.connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        existing = database._honors_payload(connection)
        identities = dict(connection.execute("SELECT external_id,member_id FROM member_identities WHERE provider='cpcfinder'"))
        for result in existing:
            _guard(connection, result["id"])
        pairs = []
        for incoming in records:
            record = {"school": "大连理工大学", **copy.deepcopy(incoming), "event": honor_contest_name(incoming)}
            for detail in record.get("memberDetails", []):
                detail.pop("id", None)
                if detail.get("provider") == "cpcfinder" and detail.get("externalId") in identities:
                    detail["id"] = identities[detail["externalId"]]
            provider, external_id = source_identity(record)
            target, ambiguous = _target_for_record(connection, record, existing)
            if not target and not ambiguous:
                honor_id = _add_result(database, connection, record)
                target = database._honors_payload(connection, honor_ids=[honor_id])[0]
                existing.append(target)
                report["added"] += 1
                report["addedResults"].append({key: target[key] for key in ("id", "date", "event", "team", "medal", "rank")})
            else:
                fields = different_fields(target, record) if target else ["identity"]
                if fields:
                    report["diffsCreated"] += int(_queue_diff(connection, target, record, fields))
                else:
                    report["unchanged"] += 1
                    connection.execute(
                        "UPDATE public_result_diffs SET status='superseded',reviewed_at=CURRENT_TIMESTAMP "
                        "WHERE provider=? AND external_id=? AND status='pending'", (provider, external_id),
                    )
                if target:
                    _attach_source(database, connection, target["id"], record)
            connection.execute(
                "INSERT INTO public_result_sources(provider,external_id,honor_id,record_json) VALUES (?,?,?,?) "
                "ON CONFLICT(provider,external_id) DO UPDATE SET honor_id=excluded.honor_id,record_json=excluded.record_json,observed_at=CURRENT_TIMESTAMP",
                (provider, external_id, target["id"] if target else None, json.dumps(record, ensure_ascii=False)),
            )
            if provider == "cpcfinder" and target:
                pairs.append((record, target))
        if public_members is not None:
            _refresh_member_stats(database, connection, public_members, pairs)
        connection.execute("INSERT OR IGNORE INTO metadata(key,value) VALUES ('public_sync_initialized','1')")
    return report


def list_result_diffs(database: Database, *, status: str = "pending", page: int = 1) -> dict:
    if status not in {"pending", "closed", "all"} or page < 1:
        raise ValueError("无效的差异筛选条件")
    where = " WHERE status='pending'" if status == "pending" else " WHERE status<>'pending'" if status == "closed" else ""
    with database.connect() as connection:
        total = connection.execute("SELECT COUNT(*) FROM public_result_diffs" + where).fetchone()[0]
        pending = connection.execute("SELECT COUNT(*) FROM public_result_diffs WHERE status='pending'").fetchone()[0]
        rows = connection.execute("SELECT * FROM public_result_diffs" + where + " ORDER BY id DESC LIMIT 20 OFFSET ?", ((page - 1) * 20,)).fetchall()
        items = []
        for row in rows:
            item = dict(row)
            for column, field in (("before_json", "before"), ("after_json", "after"), ("fields_json", "fields")):
                item[field] = json.loads(item.pop(column))
            items.append(item)
    state_path = database.path.parent / "public_sync_status.json"
    try:
        sync = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else None
    except (OSError, ValueError):
        sync = {"status": "failed", "error": "同步状态文件不可读"}
    return {"items": items, "total": total, "pendingCount": pending, "page": page, "pages": max(1, (total + 19) // 20), "sync": sync}


def resolve_result_diff(database: Database, diff_id: int, choice: str, reviewer: str) -> None:
    if choice not in {"a", "b"} or not reviewer:
        raise ValueError("请选择 A 保留当前值或 B 接受来源值")
    with database.connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        diff = connection.execute("SELECT * FROM public_result_diffs WHERE id=?", (diff_id,)).fetchone()
        if not diff or diff["status"] != "pending":
            raise ValueError("差异已处理或不存在，请刷新列表")
        before, after, fields = json.loads(diff["before_json"]), json.loads(diff["after_json"]), json.loads(diff["fields_json"])
        if choice == "b":
            if diff["honor_id"]:
                matches = database._honors_payload(connection, honor_ids=[diff["honor_id"]])
                if not matches:
                    raise ValueError("原记录已不存在")
                current = matches[0]
                if (result_values(current) != result_values(before) or current.get("members") != before.get("members")
                        or [detail.get("id") for detail in current.get("memberDetails", [])] != [detail.get("id") for detail in before.get("memberDetails", [])]):
                    raise ValueError("当前记录已被修改，不能应用过期差异；请等待下次同步生成新对比")
                source = {"name": f"管理员差异合并（{reviewer}）", "url": after.get("source", {}).get("url", ""), "kind": "manual"}
                for field in fields:
                    if field in RESULT_COLUMNS:
                        value = result_values(after)[field]
                        connection.execute(f"UPDATE honors SET {RESULT_COLUMNS[field]}=?,updated_at=CURRENT_TIMESTAMP WHERE id=?", (value, current["id"]))
                if "team" in fields:
                    connection.execute("UPDATE honors SET normalized_team=? WHERE id=?", (database._normalize_team(after["team"]), current["id"]))
                if "members" in fields:
                    _replace_roster(database, connection, current["id"], after, source)
                _guard(connection, current["id"])
                source_id = database._source(connection, source, manual=True)
                connection.execute("INSERT OR IGNORE INTO honor_sources(honor_id,source_id,role,is_manual) VALUES (?,?,'result',1)", (current["id"], source_id))
                updated = database._honors_payload(connection, honor_ids=[current["id"]])[0]
                _adjust_accepted_stats(connection, current, updated)
            else:
                target, ambiguous = _target_for_record(connection, after, database._honors_payload(connection))
                if target or ambiguous:
                    raise ValueError("记录身份仍有歧义，请先在参赛成绩中人工核对或补录，再保留 A；不会自动新增重复成绩")
                honor_id = _add_result(database, connection, after)
                connection.execute("UPDATE public_result_sources SET honor_id=? WHERE provider=? AND external_id=?", (honor_id, diff["provider"], diff["external_id"]))
        connection.execute("UPDATE public_result_diffs SET status=?,reviewer=?,reviewed_at=CURRENT_TIMESTAMP WHERE id=?",
                           ("kept" if choice == "a" else "applied", reviewer, diff_id))


def _replace_roster(database: Database, connection, honor_id: str, record: dict, source: dict) -> None:
    details = record.get("memberDetails") or [{"name": name} for name in result_values(record)["members"]]
    if not 1 <= len(details) <= 3:
        raise ValueError("来源名单人数无效")
    source_id = database._source(connection, source, manual=True)
    members = [database._upsert_member(connection, {"school": record.get("school", "大连理工大学"), **detail}, source_id, manual=True) for detail in details]
    if len(set(members)) != len(members):
        raise ValueError("来源名单包含重复成员")
    connection.execute("DELETE FROM honor_members WHERE honor_id=?", (honor_id,))
    for position, member_id in enumerate(members):
        connection.execute("INSERT INTO honor_members(honor_id,member_id,position,source_id,is_manual) VALUES (?,?,?,?,1)", (honor_id, member_id, position, source_id))
    connection.execute("UPDATE honor_roster_reviews SET confirmed_at=CURRENT_TIMESTAMP,confirmed_source_id=? WHERE honor_id=?", (source_id, honor_id))
    connection.execute("INSERT INTO metadata(key,value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (f"roster_override:{honor_id}", str(source_id)))
    connection.execute("UPDATE roster_submissions SET status='superseded',reviewed_at=CURRENT_TIMESTAMP,review_note='管理员通过榜单差异合并了名单' WHERE honor_id=? AND status='pending'", (honor_id,))


def _adjust_accepted_stats(connection, before: dict, after: dict) -> None:
    covered = connection.execute("SELECT 1 FROM honor_source_records WHERE honor_id=? AND provider='cpcfinder'", (before["id"],)).fetchone()
    if before.get("externalProvider") != "cpcfinder" and not covered:
        return
    adjustments = _member_contributions(after)
    adjustments.subtract(_member_contributions(before))
    columns = {"金牌": "gold_count", "银牌": "silver_count", "铜牌": "bronze_count", "铁牌": "iron_count"}
    for (external_id, medal), delta in adjustments.items():
        if delta:
            column = columns[medal]
            connection.execute(f"UPDATE member_public_stats SET {column}=MAX(0,{column}+?) WHERE provider='cpcfinder' "
                               "AND member_id IN (SELECT member_id FROM member_identities WHERE provider='cpcfinder' AND external_id=?)",
                               (delta, external_id))
