from __future__ import annotations

import copy
import datetime as dt
import hashlib
import json
import re
import sqlite3
from pathlib import Path
from typing import Iterable


MEDAL_POINTS = {"金牌": 10, "银牌": 6, "铜牌": 3, "铁牌": 0}
SCHEMA_VERSION = 3


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
    handle TEXT NOT NULL,
    rating INTEGER,
    verified INTEGER NOT NULL DEFAULT 0,
    source_id INTEGER REFERENCES sources(id),
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY(member_id, platform)
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
                "AND NOT EXISTS (SELECT 1 FROM member_identities mi WHERE mi.member_id=m.id) "
                "ORDER BY m.is_manual DESC, m.id",
                (normalized,),
            ).fetchall()
        else:
            rows = connection.execute(
                "SELECT id FROM members WHERE normalized_name=? ORDER BY is_manual DESC, id",
                (normalized,),
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
        for platform, account in (detail.get("handles") or {}).items():
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
        connection.execute(
            "INSERT INTO member_handles(member_id, platform, handle, rating, verified, source_id) VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(member_id, platform) DO UPDATE SET handle=excluded.handle, rating=excluded.rating, "
            "verified=excluded.verified, source_id=excluded.source_id, updated_at=CURRENT_TIMESTAMP",
            (
                member_id,
                platform.casefold(),
                str(account["handle"]),
                account.get("rating"),
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
        connection.execute(
            "INSERT INTO metadata(key, value) VALUES ('data_updated_at', ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (str(site.get("meta", {}).get("updatedAt") or dt.date.today().isoformat()),),
        )

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
    ) -> int:
        with self.connect() as connection:
            source_id = self._source(connection, source, manual=True)
            return self._upsert_member(
                connection,
                {
                    "name": name,
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
                {"handle": handle, "rating": rating, "verified": verified},
                source_id,
            )

    def add_manual_honor(self, record: dict) -> str:
        with self.connect() as connection:
            return self._upsert_honor(connection, {**record, "manual": True}, manual=True)

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
        with self.connect() as connection:
            honors = self._honors_payload(connection)
            members = self._members_payload(connection)
            updated = connection.execute("SELECT value FROM metadata WHERE key='data_updated_at'").fetchone()
        result["honors"] = honors
        result["members"] = members
        result["medalSummary"] = self._medal_summary(honors)
        result["meta"]["updatedAt"] = updated["value"] if updated else result["meta"].get("updatedAt")
        result["meta"]["memberCount"] = len(members)
        result["meta"]["honorsWithMembers"] = sum(bool(item["members"]) for item in honors)
        result["meta"]["memberCoverage"] = round(
            100 * result["meta"]["honorsWithMembers"] / max(1, len(honors))
        )
        return result

    def _honors_payload(self, connection: sqlite3.Connection) -> list[dict]:
        rows = connection.execute(
            "SELECT h.*, s.name AS source_name, s.url AS source_url FROM honors h "
            "LEFT JOIN sources s ON s.id=h.primary_source_id ORDER BY h.date DESC, h.event, h.team"
        ).fetchall()
        result = []
        for row in rows:
            member_rows = connection.execute(
                "SELECT m.id, m.name, mi.external_id AS cpcfinder_id FROM honor_members hm "
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
                "JOIN honors h ON h.id=hm.honor_id WHERE hm.member_id=? ORDER BY h.date DESC",
                (row["id"],),
            ).fetchall()
            handles = connection.execute(
                "SELECT platform, handle, rating, verified FROM member_handles WHERE member_id=?",
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
                    if honor["is_manual"] and not (honor["external_provider"] == "cpcfinder" and honor["external_award_id"]):
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
            latest_public_year = None
            if public_stats and str(public_stats["latest_event_date"] or "")[:4].isdigit():
                latest_public_year = int(str(public_stats["latest_event_date"])[:4])
            activity_years = [*years, *([latest_public_year] if latest_public_year else [])]
            status = row["status"]
            if status == "auto":
                status = "current" if activity_years and max(activity_years) >= current_year - 2 else "alumni"
            account_map = {
                item["platform"]: {
                    "handle": item["handle"],
                    "rating": item["rating"],
                    "verified": bool(item["verified"]),
                }
                for item in handles
            }
            teams_by_key: dict[str, str] = {}
            for honor in honors:
                teams_by_key.setdefault(self._normalize_team(honor["team"]), honor["team"])
            teams = list(teams_by_key.values())
            result.append(
                {
                    "id": row["id"],
                    "name": row["name"],
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
                    "LEFT JOIN honor_members hm ON hm.honor_id=h.id GROUP BY h.id HAVING COUNT(hm.member_id)=0 "
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
