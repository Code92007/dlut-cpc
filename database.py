from __future__ import annotations

import copy
import datetime as dt
import hashlib
import json
import re
import sqlite3
from pathlib import Path
from typing import Iterable
from schools import MAINTENANCE_GROUPS, school_group


MEDAL_POINTS = {"金牌": 10, "银牌": 6, "铜牌": 3, "铁牌": 0}
SCHEMA_VERSION = 5


def load_seed_file(path: Path | str) -> dict:
    path = Path(path)
    data = json.loads(path.read_text(encoding="utf-8"))
    archive = path.parent / "historical_honors.json"
    if archive.exists():
        data["historicalImports"] = [json.loads(archive.read_text(encoding="utf-8"))]
    return data


def normalize_name(value: str) -> str:
    return re.sub(r"\s+", "", value).casefold()


def source_priority(name: str) -> int:
    lower = name.casefold()
    if "人工" in name or "manual" in lower:
        return 100
    if "官方" in name or "公示" in name:
        return 80
    if "qoj" in lower or "xcpc" in lower or "gym" in lower:
        return 60
    if "cpc finder" in lower:
        return 40
    return 50


SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS metadata (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sources (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    url TEXT NOT NULL DEFAULT '',
    kind TEXT NOT NULL DEFAULT 'public',
    priority INTEGER NOT NULL DEFAULT 50,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(name, url)
);

CREATE TABLE IF NOT EXISTS members (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    normalized_name TEXT NOT NULL,
    display_name TEXT,
    school TEXT NOT NULL DEFAULT '大连理工大学',
    entry_year INTEGER,
    graduation_year INTEGER,
    status TEXT NOT NULL DEFAULT 'auto',
    notes TEXT NOT NULL DEFAULT '',
    is_manual INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_members_normalized_name ON members(normalized_name);

CREATE TABLE IF NOT EXISTS member_identities (
    provider TEXT NOT NULL,
    external_id TEXT NOT NULL,
    member_id INTEGER NOT NULL REFERENCES members(id) ON DELETE CASCADE,
    source_id INTEGER REFERENCES sources(id),
    PRIMARY KEY(provider, external_id)
);

CREATE TABLE IF NOT EXISTS member_handles (
    member_id INTEGER NOT NULL REFERENCES members(id) ON DELETE CASCADE,
    platform TEXT NOT NULL,
    handle TEXT NOT NULL COLLATE NOCASE,
    rating INTEGER,
    max_rating INTEGER,
    rating_updated_at TEXT,
    verified INTEGER NOT NULL DEFAULT 0,
    source_id INTEGER REFERENCES sources(id),
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY(member_id, platform, handle)
);

CREATE TABLE IF NOT EXISTS member_aliases (
    member_id INTEGER NOT NULL REFERENCES members(id) ON DELETE CASCADE,
    alias TEXT NOT NULL,
    PRIMARY KEY(member_id, alias)
);

CREATE TABLE IF NOT EXISTS member_sources (
    member_id INTEGER NOT NULL REFERENCES members(id) ON DELETE CASCADE,
    source_id INTEGER NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
    role TEXT NOT NULL DEFAULT 'roster',
    is_manual INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY(member_id, source_id, role)
);

CREATE TABLE IF NOT EXISTS member_public_stats (
    member_id INTEGER NOT NULL REFERENCES members(id) ON DELETE CASCADE,
    provider TEXT NOT NULL,
    rating REAL,
    provider_rank INTEGER,
    champion_count INTEGER NOT NULL DEFAULT 0,
    second_count INTEGER NOT NULL DEFAULT 0,
    third_count INTEGER NOT NULL DEFAULT 0,
    gold_count INTEGER NOT NULL DEFAULT 0,
    silver_count INTEGER NOT NULL DEFAULT 0,
    bronze_count INTEGER NOT NULL DEFAULT 0,
    iron_count INTEGER,
    latest_event_date TEXT NOT NULL DEFAULT '',
    source_id INTEGER REFERENCES sources(id),
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY(member_id, provider)
);

CREATE TABLE IF NOT EXISTS honors (
    id TEXT PRIMARY KEY,
    event TEXT NOT NULL,
    series TEXT NOT NULL,
    date TEXT NOT NULL,
    location TEXT NOT NULL DEFAULT '',
    team TEXT NOT NULL,
    normalized_team TEXT NOT NULL,
    medal TEXT NOT NULL,
    rank TEXT NOT NULL DEFAULT '',
    overall_rank TEXT NOT NULL DEFAULT '',
    official INTEGER,
    external_provider TEXT,
    external_award_id TEXT,
    external_contest_id TEXT,
    external_team_id TEXT,
    primary_source_id INTEGER REFERENCES sources(id),
    is_manual INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_honors_external_award
ON honors(external_provider, external_award_id)
WHERE external_provider IS NOT NULL AND external_award_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS honor_sources (
    honor_id TEXT NOT NULL REFERENCES honors(id) ON DELETE CASCADE,
    source_id INTEGER NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
    role TEXT NOT NULL DEFAULT 'result',
    is_manual INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY(honor_id, source_id, role)
);

CREATE TABLE IF NOT EXISTS honor_members (
    honor_id TEXT NOT NULL REFERENCES honors(id) ON DELETE CASCADE,
    member_id INTEGER NOT NULL REFERENCES members(id) ON DELETE CASCADE,
    position INTEGER NOT NULL DEFAULT 0,
    source_id INTEGER REFERENCES sources(id),
    is_manual INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY(honor_id, member_id)
);

CREATE TABLE IF NOT EXISTS honor_roster_reviews (
    honor_id TEXT PRIMARY KEY REFERENCES honors(id) ON DELETE CASCADE,
    batch_id TEXT NOT NULL,
    expected_members INTEGER NOT NULL DEFAULT 3 CHECK(expected_members BETWEEN 1 AND 3),
    school TEXT NOT NULL DEFAULT '大连理工大学',
    original_school TEXT NOT NULL DEFAULT '',
    archive_json TEXT NOT NULL DEFAULT '{}',
    suggested_members_json TEXT NOT NULL DEFAULT '[]',
    confirmed_at TEXT,
    confirmed_source_id INTEGER REFERENCES sources(id)
);
"""


class Database:
    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)

    def connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(str(self.path), timeout=15)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        return connection

    def initialize(self, seed: dict | None = None) -> None:
        with self.connect() as connection:
            self._ensure_schema(connection)
            if seed:
                self._sync_site_data(connection, seed)

    @staticmethod
    def _ensure_schema(connection: sqlite3.Connection) -> None:
        connection.executescript(SCHEMA)
        for table, column in (("member_public_stats", "iron_count"), ("honors", "official")):
            columns = {row["name"] for row in connection.execute(f"PRAGMA table_info({table})")}
            if column not in columns:
                connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} INTEGER")
        member_columns = {row["name"] for row in connection.execute("PRAGMA table_info(members)")}
        if "display_name" not in member_columns:
            connection.execute("ALTER TABLE members ADD COLUMN display_name TEXT")
        if "school" not in member_columns:
            connection.execute("ALTER TABLE members ADD COLUMN school TEXT NOT NULL DEFAULT '大连理工大学'")
        handle_columns = list(connection.execute("PRAGMA table_info(member_handles)"))
        if not any(row["name"] == "handle" and row["pk"] for row in handle_columns):
            # Rebuild the old one-account-per-platform table without losing ownership.
            connection.execute(
                "CREATE TABLE member_handles_v4 ("
                "member_id INTEGER NOT NULL REFERENCES members(id) ON DELETE CASCADE, "
                "platform TEXT NOT NULL, handle TEXT NOT NULL COLLATE NOCASE, rating INTEGER, max_rating INTEGER, "
                "rating_updated_at TEXT, verified INTEGER NOT NULL DEFAULT 0, "
                "source_id INTEGER REFERENCES sources(id), updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, "
                "PRIMARY KEY(member_id, platform, handle))"
            )
            connection.execute(
                "INSERT INTO member_handles_v4(member_id, platform, handle, rating, rating_updated_at, verified, source_id, updated_at) "
                "SELECT member_id, platform, handle, rating, CASE WHEN rating IS NOT NULL "
                "THEN strftime('%Y-%m-%dT%H:%M:%S+00:00', updated_at) END, "
                "verified, source_id, updated_at FROM member_handles"
            )
            connection.execute("DROP TABLE member_handles")
            connection.execute("ALTER TABLE member_handles_v4 RENAME TO member_handles")
        elif not any(row["name"] == "max_rating" for row in handle_columns):
            connection.execute("ALTER TABLE member_handles ADD COLUMN max_rating INTEGER")
        connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")

    def _source(self, connection: sqlite3.Connection, source: dict | None, *, manual: bool = False) -> int:
        item = source or {"name": "人工录入" if manual else "未知来源", "url": ""}
        name = str(item.get("name") or ("人工录入" if manual else "未知来源"))
        url = str(item.get("url") or "")
        kind = str(item.get("kind") or ("manual" if manual else "public"))
        priority = int(item.get("priority") or source_priority(name))
        connection.execute(
            "INSERT INTO sources(name, url, kind, priority) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(name, url) DO UPDATE SET kind=excluded.kind, priority=MAX(sources.priority, excluded.priority)",
            (name, url, kind, priority),
        )
        row = connection.execute("SELECT id FROM sources WHERE name=? AND url=?", (name, url)).fetchone()
        assert row
        return int(row["id"])

    def _find_member(self, connection: sqlite3.Connection, detail: dict) -> int | None:
        if detail.get("_forceNew"):
            return None
        provider = detail.get("provider")
        external_id = detail.get("externalId")
        if provider and external_id:
            row = connection.execute(
                "SELECT member_id FROM member_identities WHERE provider=? AND external_id=?",
                (provider, str(external_id)),
            ).fetchone()
            if row:
                return int(row["member_id"])
        normalized = normalize_name(str(detail.get("name") or ""))
        if provider and external_id:
            rows = connection.execute(
                "SELECT m.id FROM members m WHERE m.normalized_name=? "
                "AND m.school=? "
                "AND NOT EXISTS (SELECT 1 FROM member_identities mi WHERE mi.member_id=m.id) "
                "ORDER BY m.is_manual DESC, m.id",
                (normalized, school_group(detail.get("school", "大连理工大学")) or "大连理工大学"),
            ).fetchall()
        else:
            rows = connection.execute(
                "SELECT id FROM members WHERE normalized_name=? AND school=? ORDER BY is_manual DESC, id",
                (normalized, school_group(detail.get("school", "大连理工大学")) or "大连理工大学"),
            ).fetchall()
        return int(rows[0]["id"]) if len(rows) == 1 else None

    def _upsert_member(
        self,
        connection: sqlite3.Connection,
        detail: dict,
        source_id: int,
        *,
        manual: bool = False,
    ) -> int:
        name = str(detail.get("name") or "").strip()
        if not name:
            raise ValueError("member name cannot be empty")
        member_id = self._find_member(connection, detail)
        if member_id is None:
            cursor = connection.execute(
                "INSERT INTO members(name, normalized_name, entry_year, graduation_year, status, notes, is_manual) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    name,
                    normalize_name(name),
                    detail.get("entryYear"),
                    detail.get("graduationYear"),
                    detail.get("status") or "auto",
                    detail.get("notes") or "",
                    int(manual),
                ),
            )
            member_id = int(cursor.lastrowid)
        else:
            row = connection.execute("SELECT is_manual FROM members WHERE id=?", (member_id,)).fetchone()
            if row and (manual or not row["is_manual"]):
                connection.execute(
                    "UPDATE members SET name=?, normalized_name=?, entry_year=COALESCE(?, entry_year), "
                    "graduation_year=COALESCE(?, graduation_year), status=CASE WHEN ?='auto' THEN status ELSE ? END, "
                    "notes=CASE WHEN ?='' THEN notes ELSE ? END, is_manual=MAX(is_manual, ?), updated_at=CURRENT_TIMESTAMP WHERE id=?",
                    (
                        name,
                        normalize_name(name),
                        detail.get("entryYear"),
                        detail.get("graduationYear"),
                        detail.get("status") or "auto",
                        detail.get("status") or "auto",
                        detail.get("notes") or "",
                        detail.get("notes") or "",
                        int(manual),
                        member_id,
                    ),
                )
        if detail.get("school"):
            connection.execute("UPDATE members SET school=? WHERE id=? AND (? OR is_manual=0)",
                               (school_group(detail["school"]) or "大连理工大学", member_id, int(manual)))
        provider = detail.get("provider")
        external_id = detail.get("externalId")
        if provider and external_id:
            connection.execute(
                "INSERT INTO member_identities(provider, external_id, member_id, source_id) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(provider, external_id) DO UPDATE SET member_id=excluded.member_id, source_id=excluded.source_id",
                (str(provider), str(external_id), member_id, source_id),
            )
        connection.execute(
            "INSERT OR IGNORE INTO member_sources(member_id, source_id, role, is_manual) VALUES (?, ?, 'roster', ?)",
            (member_id, source_id, int(manual)),
        )
        for platform, value in (detail.get("handles") or {}).items():
            for account in value if isinstance(value, list) else [value]:
                if account and account.get("handle"):
                    self._set_handle(connection, member_id, platform, account, source_id)
        return member_id

    def _set_handle(
        self,
        connection: sqlite3.Connection,
        member_id: int,
        platform: str,
        account: dict,
        source_id: int,
    ) -> None:
        platform = platform.strip().casefold()
        handle = str(account["handle"]).strip()
        if not platform or not handle or ";" in handle:
            raise ValueError("platform and a single account handle are required")
        owner = connection.execute(
            "SELECT member_id FROM member_handles WHERE platform=? AND handle=? COLLATE NOCASE AND member_id<>?",
            (platform, handle, member_id),
        ).fetchone()
        if owner:
            raise ValueError(f"{platform} account {handle} already belongs to member {owner['member_id']}")
        rating_updated_at = account.get("ratingUpdatedAt")
        if "rating" in account and not rating_updated_at:
            rating_updated_at = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
        connection.execute(
            "INSERT INTO member_handles(member_id, platform, handle, rating, max_rating, rating_updated_at, verified, source_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(member_id, platform, handle) DO UPDATE SET "
            "rating=CASE WHEN excluded.rating_updated_at IS NOT NULL AND "
            "(member_handles.rating_updated_at IS NULL OR excluded.rating_updated_at>=member_handles.rating_updated_at) "
            "THEN excluded.rating ELSE member_handles.rating END, "
            "max_rating=CASE WHEN excluded.rating_updated_at IS NOT NULL AND "
            "(member_handles.rating_updated_at IS NULL OR excluded.rating_updated_at>=member_handles.rating_updated_at) "
            "THEN COALESCE(excluded.max_rating, member_handles.max_rating) ELSE member_handles.max_rating END, "
            "rating_updated_at=CASE WHEN excluded.rating_updated_at IS NOT NULL AND "
            "(member_handles.rating_updated_at IS NULL OR excluded.rating_updated_at>=member_handles.rating_updated_at) "
            "THEN excluded.rating_updated_at ELSE member_handles.rating_updated_at END, "
            "verified=MAX(member_handles.verified, excluded.verified), "
            "source_id=CASE WHEN excluded.verified THEN excluded.source_id ELSE member_handles.source_id END, "
            "updated_at=CURRENT_TIMESTAMP",
            (
                member_id,
                platform,
                handle,
                account.get("rating"),
                account.get("maxRating"),
                rating_updated_at,
                int(bool(account.get("verified"))),
                source_id,
            ),
        )

    def _honor_sources(self, record: dict) -> list[dict]:
        sources = [item for item in record.get("sources", []) if isinstance(item, dict)]
        if isinstance(record.get("source"), dict):
            sources.append(record["source"])
        unique: dict[tuple[str, str], dict] = {}
        for item in sources:
            key = (str(item.get("name") or "未知来源"), str(item.get("url") or ""))
            unique[key] = item
        return list(unique.values()) or [{"name": "未知来源", "url": ""}]

    def _upsert_honor(self, connection: sqlite3.Connection, record: dict, *, manual: bool = False) -> str:
        honor_id = str(record.get("id") or self._manual_honor_id(record))
        external_provider = record.get("externalProvider") or ("cpcfinder" if record.get("cpcfinderAwardId") is not None else None)
        external_award_id = str(record["cpcfinderAwardId"]) if record.get("cpcfinderAwardId") is not None else record.get("externalAwardId")
        if external_provider and external_award_id:
            identity = connection.execute(
                "SELECT id FROM honors WHERE external_provider=? AND external_award_id=?",
                (external_provider, external_award_id),
            ).fetchone()
            if identity:
                honor_id = identity["id"]
        sources = self._honor_sources(record)
        source_ids = [(self._source(connection, item, manual=manual), item) for item in sources]
        primary_source_id = max(source_ids, key=lambda pair: source_priority(str(pair[1].get("name") or "")))[0]
        existing = connection.execute("SELECT is_manual FROM honors WHERE id=?", (honor_id,)).fetchone()
        values = (
            str(record.get("event") or ""),
            str(record.get("series") or "其他"),
            str(record.get("date") or ""),
            str(record.get("location") or ""),
            str(record.get("team") or ""),
            self._normalize_team(str(record.get("team") or "")),
            str(record.get("medal") or ""),
            str(record.get("rank") or ""),
            str(record.get("overallRank") or ""),
            int(record["official"]) if record.get("official") is not None else None,
            external_provider,
            external_award_id,
            str(record["cpcfinderContestId"]) if record.get("cpcfinderContestId") is not None else record.get("externalContestId"),
            str(record["cpcfinderTeamId"]) if record.get("cpcfinderTeamId") is not None else record.get("externalTeamId"),
            primary_source_id,
            int(manual),
        )
        if not existing:
            connection.execute(
                "INSERT INTO honors(id, event, series, date, location, team, normalized_team, medal, rank, overall_rank, official, "
                "external_provider, external_award_id, external_contest_id, external_team_id, primary_source_id, is_manual) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (honor_id, *values),
            )
        elif manual or not existing["is_manual"]:
            connection.execute(
                "UPDATE honors SET event=?, series=?, date=?, location=?, team=?, normalized_team=?, medal=?, rank=?, "
                "overall_rank=?, official=?, external_provider=?, external_award_id=?, external_contest_id=?, external_team_id=?, "
                "primary_source_id=?, is_manual=MAX(is_manual, ?), updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (*values, honor_id),
            )
        # A public sync is an authoritative snapshot of the currently verified
        # links. Drop stale imported mirrors while retaining human-curated links.
        connection.execute(
            "DELETE FROM honor_sources WHERE honor_id=? AND is_manual=0",
            (honor_id,),
        )
        for source_id, _ in source_ids:
            connection.execute(
                "INSERT OR IGNORE INTO honor_sources(honor_id, source_id, role, is_manual) VALUES (?, ?, 'result', ?)",
                (honor_id, source_id, int(manual)),
            )

        details = record.get("memberDetails") or [{"name": name} for name in record.get("members", [])]
        if details:
            connection.execute("DELETE FROM honor_members WHERE honor_id=? AND is_manual=0", (honor_id,))
            roster_source = record.get("memberSource") if isinstance(record.get("memberSource"), dict) else sources[0]
            roster_source_id = self._source(connection, roster_source, manual=manual)
            for position, detail in enumerate(details):
                member_id = self._upsert_member(connection, detail, roster_source_id, manual=manual)
                connection.execute(
                    "INSERT INTO honor_members(honor_id, member_id, position, source_id, is_manual) VALUES (?, ?, ?, ?, ?) "
                    "ON CONFLICT(honor_id, member_id) DO UPDATE SET position=excluded.position, source_id=excluded.source_id, "
                    "is_manual=MAX(honor_members.is_manual, excluded.is_manual)",
                    (honor_id, member_id, position, roster_source_id, int(manual)),
                )
        return honor_id

    def _sync_site_data(self, connection: sqlite3.Connection, site: dict) -> None:
        connection.execute("DELETE FROM member_public_stats WHERE provider='cpcfinder'")
        for detail in site.get("publicMembers", []):
            source_id = self._source(connection, detail.get("source"), manual=False)
            member_id = self._upsert_member(connection, detail, source_id, manual=False)
            stats = detail.get("cpcfinder") or {}
            if stats:
                connection.execute(
                    "INSERT INTO member_public_stats(member_id, provider, rating, provider_rank, champion_count, "
                    "second_count, third_count, gold_count, silver_count, bronze_count, iron_count, latest_event_date, source_id) "
                    "VALUES (?, 'cpcfinder', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                    "ON CONFLICT(member_id, provider) DO UPDATE SET rating=excluded.rating, "
                    "provider_rank=excluded.provider_rank, champion_count=excluded.champion_count, "
                    "second_count=excluded.second_count, third_count=excluded.third_count, "
                    "gold_count=excluded.gold_count, silver_count=excluded.silver_count, "
                    "bronze_count=excluded.bronze_count, iron_count=excluded.iron_count, latest_event_date=excluded.latest_event_date, "
                    "source_id=excluded.source_id, updated_at=CURRENT_TIMESTAMP",
                    (
                        member_id,
                        stats.get("rating"),
                        stats.get("rank"),
                        int(stats.get("championCount") or 0),
                        int(stats.get("secondCount") or 0),
                        int(stats.get("thirdCount") or 0),
                        int(stats.get("goldCount") or 0),
                        int(stats.get("silverCount") or 0),
                        int(stats.get("bronzeCount") or 0),
                        stats.get("ironCount"),
                        str(stats.get("latestEventDate") or ""),
                        source_id,
                    ),
                )
        for record in site.get("honors", []):
            self._upsert_honor(connection, record, manual=bool(record.get("manual")))
        for member in site.get("manualMembers", []):
            source_id = self._source(connection, member.get("source"), manual=True)
            self._upsert_member(connection, member, source_id, manual=True)
        for binding in site.get("accountBindings", []):
            member_id = self._find_member(connection, binding)
            source_id = self._source(connection, binding.get("source") or {"name": "队内人工确认"}, manual=True)
            if member_id is None:
                if not binding.get("createMember"):
                    raise ValueError(f"account binding has no unambiguous member: {binding.get('name')}")
                if not binding.get("externalId") and connection.execute("SELECT 1 FROM members WHERE normalized_name=?", (normalize_name(binding["name"]),)).fetchone():
                    raise ValueError(f"account binding is ambiguous: {binding['name']}")
                member_id = self._upsert_member(connection, {**binding, "status": "unknown"}, source_id, manual=True)
            else:
                name = connection.execute("SELECT name FROM members WHERE id=?", (member_id,)).fetchone()["name"]
                if normalize_name(name) != normalize_name(binding["name"]):
                    raise ValueError(f"account binding name does not match member {member_id}: {binding['name']}")
            if binding.get("provider") and binding.get("externalId"):
                connection.execute(
                    "INSERT OR IGNORE INTO member_identities(provider, external_id, member_id, source_id) VALUES (?, ?, ?, ?)",
                    (binding["provider"], binding["externalId"], member_id, source_id),
                )
            for platform, accounts in binding.get("accounts", {}).items():
                for account in accounts:
                    self._set_handle(connection, member_id, platform, account, source_id)
        for override in site.get("memberOverrides", []):
            member_id = self._find_member(connection, override)
            if member_id is None:
                raise ValueError(f"name override has no unambiguous member: {override.get('externalId')}")
            display_name = connection.execute("SELECT display_name FROM members WHERE id=?", (member_id,)).fetchone()["display_name"]
            if not display_name or display_name == override["displayName"]:
                self._set_display_name(connection, member_id, override["displayName"], override.get("aliases", []))
        for batch in site.get("historicalImports", []):
            self._import_historical_batch(connection, batch)
        connection.execute(
            "INSERT INTO metadata(key, value) VALUES ('data_updated_at', ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (str(site.get("meta", {}).get("updatedAt") or dt.date.today().isoformat()),),
        )

    def _import_historical_batch(self, connection: sqlite3.Connection, batch: dict) -> int:
        batch_id = batch.get("batchId")
        if not isinstance(batch_id, str) or not batch_id or len(batch_id) > 150:
            raise ValueError("Invalid historical import batch ID")
        marker = f"historical_import:{batch_id}"
        if connection.execute("SELECT 1 FROM metadata WHERE key=?", (marker,)).fetchone():
            return 0
        records = batch.get("honors", [])
        for record in records:
            expected = record.get("expectedMembers", 3)
            if type(expected) is not int or not 1 <= expected <= 3:
                raise ValueError("Invalid historical team size")
            if (record.get("school") or "大连理工大学") not in MAINTENANCE_GROUPS:
                raise ValueError("Invalid historical maintenance group")
            if record.get("date", "") >= "2020-01-01" or record.get("medal") not in {"金牌", "银牌", "铜牌"}:
                raise ValueError("Historical imports accept only pre-2020 medal results")
            dt.date.fromisoformat(record["date"])
            # A repeated team name is not evidence of the same roster. Always
            # leave historical people unlinked until an admin confirms the roster.
            honor_id = self._upsert_honor(connection, {**record, "members": [], "memberDetails": []})
            connection.execute(
                "INSERT OR IGNORE INTO honor_roster_reviews(honor_id, batch_id, expected_members, school, "
                "original_school, archive_json, suggested_members_json) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (honor_id, batch_id, expected, record.get("school") or "大连理工大学",
                 record.get("originalSchool") or "", json.dumps(record.get("archive", {}), ensure_ascii=False),
                 json.dumps(record.get("suggestedMembers", []), ensure_ascii=False)),
            )
        connection.execute("INSERT INTO metadata(key, value) VALUES (?, ?)",
                           (marker, json.dumps({"fetchedAt": batch.get("fetchedAt"), "count": len(records),
                                                "source": batch.get("source"), "contests": batch.get("contests", [])}, ensure_ascii=False)))
        return len(records)

    def import_historical_batch(self, batch: dict) -> int:
        with self.connect() as connection:
            self._ensure_schema(connection)
            return self._import_historical_batch(connection, batch)

    def _resolve_roster_member(self, connection: sqlite3.Connection, value: int | str,
                               school: str | None, source_id: int) -> int:
        if type(value) is int and value > 0:
            member = connection.execute("SELECT school FROM members WHERE id=?", (value,)).fetchone()
            if not member:
                raise ValueError(f"member {value} does not exist")
            if school and member["school"] != school:
                raise ValueError("参赛成员与成绩所属范围不一致；城市学院、盘锦校区须独立维护")
            return value
        if not isinstance(value, str) or not value.strip() or len(value) > 150:
            raise ValueError("参赛成员须为有效成员 ID 或一至 150 字的姓名")
        name = value.strip()
        normalized = normalize_name(name)
        target_school = school or "大连理工大学"
        rows = connection.execute(
            "SELECT m.id, m.name, m.display_name, a.alias FROM members m "
            "LEFT JOIN member_aliases a ON a.member_id=m.id WHERE m.school=?",
            (target_school,),
        ).fetchall()
        matches = {int(row["id"]) for row in rows
                   if any(normalize_name(candidate) == normalized
                          for candidate in (row["name"], row["display_name"], row["alias"]) if candidate)}
        if len(matches) > 1:
            raise ValueError(f"“{name}”有多个同名或同别名成员，请从名单中选择具体成员 ID")
        if matches:
            return next(iter(matches))
        return self._upsert_member(connection, {"name": name, "school": target_school,
                                               "status": "alumni", "_forceNew": True}, source_id, manual=True)

    def confirm_honor_members(self, honor_id: str, member_ids: list[int | str], *, source: dict | None = None) -> None:
        with self.connect() as connection:
            # Serialize creation and confirmation so concurrent requests cannot
            # create duplicate people or overwrite an already confirmed roster.
            connection.execute("BEGIN IMMEDIATE")
            honor = connection.execute("SELECT id FROM honors WHERE id=?", (honor_id,)).fetchone()
            if not honor:
                raise ValueError("参赛成绩不存在")
            review = connection.execute("SELECT * FROM honor_roster_reviews WHERE honor_id=?", (honor_id,)).fetchone()
            if review and review["confirmed_at"]:
                raise ValueError("该成绩的成员已经确认，请勿重复提交")
            if not review and connection.execute("SELECT 1 FROM honor_members WHERE honor_id=?", (honor_id,)).fetchone():
                raise ValueError("该成绩已有成员名单，不能作为待确认项覆盖")
            expected = review["expected_members"] if review else 3
            if not isinstance(member_ids, list) or len(member_ids) != expected:
                raise ValueError(f"请填写 {expected} 位不同的参赛成员")
            source_id = self._source(connection, source, manual=True)
            member_ids = [self._resolve_roster_member(connection, value, review["school"] if review else None, source_id)
                          for value in member_ids]
            if len(set(member_ids)) != expected:
                raise ValueError(f"请填写 {expected} 位不同的参赛成员，同一成员不能重复")
            connection.execute("DELETE FROM honor_members WHERE honor_id=?", (honor_id,))
            for position, member_id in enumerate(member_ids):
                connection.execute("INSERT INTO honor_members(honor_id, member_id, position, source_id, is_manual) VALUES (?, ?, ?, ?, 1)",
                                   (honor_id, member_id, position, source_id))
                connection.execute("INSERT OR IGNORE INTO member_sources(member_id, source_id, role, is_manual) VALUES (?, ?, 'roster', 1)",
                                   (member_id, source_id))
            connection.execute("UPDATE honor_roster_reviews SET confirmed_at=CURRENT_TIMESTAMP, confirmed_source_id=? WHERE honor_id=?",
                               (source_id, honor_id))

    def sync_site_data(self, site: dict) -> None:
        with self.connect() as connection:
            self._ensure_schema(connection)
            self._sync_site_data(connection, site)

    def add_manual_member(
        self,
        name: str,
        *,
        entry_year: int | None = None,
        graduation_year: int | None = None,
        status: str = "alumni",
        notes: str = "",
        source: dict | None = None,
        match_existing: bool = False,
        school: str = "大连理工大学",
    ) -> int:
        with self.connect() as connection:
            if school not in MAINTENANCE_GROUPS:
                raise ValueError("成员所属校区无效")
            source_id = self._source(connection, source, manual=True)
            return self._upsert_member(
                connection,
                {
                    "name": name,
                    "school": school,
                    "entryYear": entry_year,
                    "graduationYear": graduation_year,
                    "status": status,
                    "notes": notes,
                    "_forceNew": not match_existing,
                },
                source_id,
                manual=True,
            )

    def set_handle(
        self,
        member_id: int,
        platform: str,
        handle: str,
        *,
        rating: int | None = None,
        verified: bool = True,
        source: dict | None = None,
    ) -> None:
        with self.connect() as connection:
            if not connection.execute("SELECT 1 FROM members WHERE id=?", (member_id,)).fetchone():
                raise ValueError(f"member {member_id} does not exist")
            source_id = self._source(connection, source, manual=True)
            self._set_handle(
                connection,
                member_id,
                platform,
                {"handle": handle, "verified": verified, **({"rating": rating} if rating is not None else {})},
                source_id,
            )

    @staticmethod
    def _set_display_name(connection: sqlite3.Connection, member_id: int, name: str, aliases: list[str]) -> None:
        name = name.strip()
        if not name:
            raise ValueError("display name cannot be empty")
        row = connection.execute("SELECT name, display_name FROM members WHERE id=?", (member_id,)).fetchone()
        if not row:
            raise ValueError(f"member {member_id} does not exist")
        for alias in [row["name"], row["display_name"], *aliases]:
            if alias and alias.strip() and alias.strip() != name:
                connection.execute("INSERT OR IGNORE INTO member_aliases(member_id, alias) VALUES (?, ?)", (member_id, alias.strip()))
        connection.execute("UPDATE members SET display_name=?, updated_at=CURRENT_TIMESTAMP WHERE id=?", (name, member_id))

    def set_display_name(self, member_id: int, name: str, aliases: list[str] | None = None) -> None:
        with self.connect() as connection:
            self._set_display_name(connection, member_id, name, aliases or [])

    def update_account_ratings(self, updates: list[dict], timestamp: str) -> None:
        with self.connect() as connection:
            for update in updates:
                connection.execute(
                    "UPDATE member_handles SET rating=?, max_rating=?, rating_updated_at=?, updated_at=CURRENT_TIMESTAMP "
                    "WHERE platform='codeforces' AND handle=? COLLATE NOCASE "
                    "AND (rating_updated_at IS NULL OR rating_updated_at<=?)",
                    (update.get("rating"), update.get("maxRating"), timestamp, update["handle"], timestamp),
                )

    def add_manual_honor(self, record: dict) -> str:
        with self.connect() as connection:
            return self._upsert_honor(connection, {**record, "manual": True}, manual=True)

    def add_manual_honor_with_members(self, record: dict, member_ids: list[int]) -> str:
        with self.connect() as connection:
            existing = connection.execute(
                "SELECT id FROM honors WHERE date=? AND event=? AND normalized_team=? AND is_manual=0",
                (record.get("date", ""), record.get("event", ""), self._normalize_team(record.get("team", ""))),
            ).fetchone()
            if existing:
                raise ValueError("该队伍的公开参赛成绩已存在，不能重复补录")
            for member_id in member_ids:
                if not connection.execute("SELECT 1 FROM members WHERE id=?", (member_id,)).fetchone():
                    raise ValueError(f"member {member_id} does not exist")
            honor_id = self._upsert_honor(connection, {**record, "members": [], "memberDetails": [], "manual": True}, manual=True)
            source_id = self._source(connection, record.get("source"), manual=True)
            connection.execute("DELETE FROM honor_members WHERE honor_id=?", (honor_id,))
            for position, member_id in enumerate(member_ids):
                connection.execute(
                    "INSERT INTO honor_members(honor_id, member_id, position, source_id, is_manual) VALUES (?, ?, ?, ?, 1)",
                    (honor_id, member_id, position, source_id),
                )
            return honor_id

    def link_member(self, honor_id: str, member_id: int, *, source: dict | None = None) -> None:
        with self.connect() as connection:
            if not connection.execute("SELECT 1 FROM honors WHERE id=?", (honor_id,)).fetchone():
                raise ValueError(f"honor {honor_id} does not exist")
            if not connection.execute("SELECT 1 FROM members WHERE id=?", (member_id,)).fetchone():
                raise ValueError(f"member {member_id} does not exist")
            source_id = self._source(connection, source, manual=True)
            position_row = connection.execute(
                "SELECT COALESCE(MAX(position), -1) + 1 AS position FROM honor_members WHERE honor_id=?",
                (honor_id,),
            ).fetchone()
            connection.execute(
                "INSERT INTO honor_members(honor_id, member_id, position, source_id, is_manual) VALUES (?, ?, ?, ?, 1) "
                "ON CONFLICT(honor_id, member_id) DO UPDATE SET source_id=excluded.source_id, is_manual=1",
                (honor_id, member_id, int(position_row["position"]), source_id),
            )

    def payload(self, base: dict) -> dict:
        result = copy.deepcopy(base)
        result.pop("ratingGroups", None)
        result.pop("historicalImports", None)
        with self.connect() as connection:
            honors = self._honors_payload(connection)
            members = self._members_payload(connection)
            updated = connection.execute("SELECT value FROM metadata WHERE key='data_updated_at'").fetchone()
        result["honors"] = honors
        result["members"] = members
        result["pendingHonors"] = [item for item in honors if not item["rosterConfirmed"]]
        result["medalSummary"] = self._medal_summary(honors)
        result["meta"]["updatedAt"] = updated["value"] if updated else result["meta"].get("updatedAt")
        result["meta"]["memberCount"] = len(members)
        result["meta"]["firstYear"] = min((item["date"][:4] for item in honors), default="2020")
        result["meta"]["pendingHonorCount"] = len(result["pendingHonors"])
        regional_ranks = [int(match.group(1)) for item in honors
                          if item["series"] in {"ICPC", "CCPC"} and item["official"] is not False
                          and item["location"] != "总决赛" and not re.search(r"final|总决赛", item["event"], re.I)
                          for match in [re.match(r"^([1-9]\d*)(?:\s*/|$)", item["rank"])] if match]
        result["meta"]["bestRank"] = min(regional_ranks) if regional_ranks else "—"
        result["meta"]["honorsWithMembers"] = sum(item["rosterConfirmed"] for item in honors)
        result["meta"]["memberCoverage"] = round(
            100 * result["meta"]["honorsWithMembers"] / max(1, len(honors))
        )
        return result

    def _honors_payload(self, connection: sqlite3.Connection) -> list[dict]:
        rows = connection.execute(
            "SELECT h.*, s.name AS source_name, s.url AS source_url, r.expected_members, r.school, r.original_school, "
            "r.confirmed_at, r.batch_id, r.suggested_members_json FROM honors h "
            "LEFT JOIN honor_roster_reviews r ON r.honor_id=h.id "
            "LEFT JOIN sources s ON s.id=h.primary_source_id ORDER BY h.date DESC, h.event, h.team"
        ).fetchall()
        result = []
        for row in rows:
            member_rows = connection.execute(
                "SELECT m.id, m.school, COALESCE(m.display_name, m.name) AS name, mi.external_id AS cpcfinder_id FROM honor_members hm "
                "JOIN members m ON m.id=hm.member_id "
                "LEFT JOIN member_identities mi ON mi.member_id=m.id AND mi.provider='cpcfinder' "
                "WHERE hm.honor_id=? ORDER BY hm.position, m.id",
                (row["id"],),
            ).fetchall()
            source_rows = connection.execute(
                "SELECT s.name, s.url FROM honor_sources hs JOIN sources s ON s.id=hs.source_id "
                "WHERE hs.honor_id=? ORDER BY s.priority DESC, s.name",
                (row["id"],),
            ).fetchall()
            result.append(
                {
                    "id": row["id"],
                    "event": row["event"],
                    "series": row["series"],
                    "date": row["date"],
                    "location": row["location"],
                    "team": row["team"],
                    "school": row["school"] or (member_rows[0]["school"] if member_rows and len({item["school"] for item in member_rows}) == 1 else "大连理工大学"),
                    "originalSchool": row["original_school"] or "",
                    "expectedMembers": row["expected_members"] or len(member_rows) or 3,
                    "rosterConfirmed": bool(row["confirmed_at"]) if row["batch_id"] else bool(member_rows),
                    "suggestedMembers": json.loads(row["suggested_members_json"] or "[]"),
                    "members": [item["name"] for item in member_rows],
                    "memberDetails": [
                        {
                            "id": item["id"],
                            "name": item["name"],
                            **(
                                {"provider": "cpcfinder", "externalId": item["cpcfinder_id"]}
                                if item["cpcfinder_id"]
                                else {}
                            ),
                        }
                        for item in member_rows
                    ],
                    "medal": row["medal"],
                    "rank": row["rank"],
                    "overallRank": row["overall_rank"],
                    "official": bool(row["official"]) if row["official"] is not None else None,
                    "source": {"name": row["source_name"] or "未知来源", "url": row["source_url"] or ""},
                    "sources": [{"name": item["name"], "url": item["url"]} for item in source_rows],
                    "manual": bool(row["is_manual"]),
                }
            )
        return result

    def _members_payload(self, connection: sqlite3.Connection) -> list[dict]:
        rows = connection.execute("SELECT * FROM members ORDER BY id").fetchall()
        current_year = dt.date.today().year
        result = []
        for row in rows:
            honors = connection.execute(
                "SELECT h.id, h.date, h.team, h.medal, h.is_manual, h.external_provider, h.external_award_id FROM honor_members hm "
                "JOIN honors h ON h.id=hm.honor_id LEFT JOIN honor_roster_reviews r ON r.honor_id=h.id "
                "WHERE hm.member_id=? AND (r.honor_id IS NULL OR r.confirmed_at IS NOT NULL) ORDER BY h.date DESC",
                (row["id"],),
            ).fetchall()
            handles = connection.execute(
                "SELECT platform, handle, rating, max_rating, rating_updated_at, verified FROM member_handles WHERE member_id=? "
                "ORDER BY platform, max_rating DESC, rating DESC, handle COLLATE NOCASE",
                (row["id"],),
            ).fetchall()
            sources = connection.execute(
                "SELECT DISTINCT s.name, s.url FROM member_sources ms JOIN sources s ON s.id=ms.source_id "
                "WHERE ms.member_id=? ORDER BY s.priority DESC, s.name",
                (row["id"],),
            ).fetchall()
            public_stats = connection.execute(
                "SELECT * FROM member_public_stats WHERE member_id=? AND provider='cpcfinder'",
                (row["id"],),
            ).fetchone()
            years = [int(item["date"][:4]) for item in honors if item["date"][:4].isdigit()]
            medals = {"gold": 0, "silver": 0, "bronze": 0, "iron": 0}
            manual_medals = dict(medals)
            medal_fields = {"金牌": "gold", "银牌": "silver", "铜牌": "bronze", "铁牌": "iron"}
            for honor in honors:
                field = medal_fields.get(honor["medal"])
                if field:
                    medals[field] += 1
                    if (honor["is_manual"] or honor["external_provider"] == "rankland") and not (honor["external_provider"] == "cpcfinder" and honor["external_award_id"]):
                        manual_medals[field] += 1
            if public_stats:
                medals = {
                    "gold": int(public_stats["gold_count"]) + manual_medals["gold"],
                    "silver": int(public_stats["silver_count"]) + manual_medals["silver"],
                    "bronze": int(public_stats["bronze_count"]) + manual_medals["bronze"],
                    "iron": (
                        int(public_stats["iron_count"]) + manual_medals["iron"]
                        if public_stats["iron_count"] is not None else None
                    ),
                }
            elif any(honor["external_provider"] == "rankland" for honor in honors):
                # The archive contains medalists only, not a complete record of
                # participation. Do not reward unknown iron totals as zero.
                medals["iron"] = None
            latest_public_year = None
            if public_stats and str(public_stats["latest_event_date"] or "")[:4].isdigit():
                latest_public_year = int(str(public_stats["latest_event_date"])[:4])
            activity_years = [*years, *([latest_public_year] if latest_public_year else [])]
            status = row["status"]
            if status == "auto":
                status = "current" if activity_years and max(activity_years) >= current_year - 2 else "alumni"
            accounts: dict[str, list[dict]] = {}
            for item in handles:
                accounts.setdefault(item["platform"], []).append({
                    "handle": item["handle"],
                    "rating": item["rating"],
                    "maxRating": item["max_rating"],
                    "ratingUpdatedAt": item["rating_updated_at"],
                    "verified": bool(item["verified"]),
                })
            account_map = {platform: items[0] for platform, items in accounts.items()}
            aliases = [item["alias"] for item in connection.execute(
                "SELECT alias FROM member_aliases WHERE member_id=? ORDER BY alias", (row["id"],)
            )]
            if row["display_name"] and row["name"] != row["display_name"] and row["name"] not in aliases:
                aliases.append(row["name"])
            teams_by_key: dict[str, str] = {}
            for honor in honors:
                teams_by_key.setdefault(self._normalize_team(honor["team"]), honor["team"])
            teams = list(teams_by_key.values())
            result.append(
                {
                    "id": row["id"],
                    "name": row["display_name"] or row["name"],
                    "school": row["school"],
                    "aliases": aliases,
                    "entryYear": row["entry_year"],
                    "graduationYear": row["graduation_year"],
                    "status": status,
                    "manual": bool(row["is_manual"]),
                    "firstYear": min(years) if years else row["entry_year"],
                    "lastYear": max(activity_years) if activity_years else row["graduation_year"],
                    "teams": teams,
                    "honorCount": sum(medals[key] for key in ("gold", "silver", "bronze")) if public_stats else sum(honor["medal"] != "铁牌" for honor in honors),
                    "medals": medals,
                    "handles": account_map,
                    "accounts": accounts,
                    "cpcfinder": (
                        {
                            "rating": public_stats["rating"],
                            "rank": public_stats["provider_rank"],
                            "latestEventDate": public_stats["latest_event_date"],
                            "url": next((item["url"] for item in sources if "CPC Finder 选手库" in item["name"]), ""),
                        }
                        if public_stats else None
                    ),
                    "sources": [{"name": item["name"], "url": item["url"]} for item in sources],
                }
            )
        return sorted(
            result,
            key=lambda item: (-(item["lastYear"] or 0), item["name"], item["id"]),
        )

    def missing_honors(self) -> list[dict]:
        with self.connect() as connection:
            return [
                dict(row)
                for row in connection.execute(
                    "SELECT h.id, h.date, h.event, h.team FROM honors h "
                    "LEFT JOIN honor_members hm ON hm.honor_id=h.id LEFT JOIN honor_roster_reviews r ON r.honor_id=h.id "
                    "GROUP BY h.id HAVING COUNT(hm.member_id)=0 OR (r.honor_id IS NOT NULL AND r.confirmed_at IS NULL) "
                    "ORDER BY h.date DESC"
                ).fetchall()
            ]

    @staticmethod
    def _manual_honor_id(record: dict) -> str:
        raw = "|".join(str(record.get(key) or "") for key in ("date", "event", "team"))
        return "manual-" + hashlib.sha1(raw.encode("utf-8")).hexdigest()[:14]

    @staticmethod
    def _normalize_team(value: str) -> str:
        return re.sub(r"[^\w\u4e00-\u9fff]+", "", value.casefold())

    @staticmethod
    def _medal_summary(honors: Iterable[dict]) -> list[dict]:
        years: dict[str, dict] = {}
        fields = {"金牌": "gold", "银牌": "silver", "铜牌": "bronze", "铁牌": "iron"}
        for record in honors:
            year = record["date"][:4]
            item = years.setdefault(year, {"year": year, "gold": 0, "silver": 0, "bronze": 0, "iron": 0})
            field = fields.get(record["medal"])
            if field:
                item[field] += 1
        return [years[key] for key in sorted(years)]

    def export_json(self, base: dict, path: Path) -> None:
        payload = self.payload(base)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
