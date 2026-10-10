"""SQLite storage (stdlib sqlite3, WAL mode). One connection per process."""
from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from typing import Any, Iterable

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);

-- rate limiting
CREATE TABLE IF NOT EXISTS rate_state (
  bucket TEXT NOT NULL,
  key TEXT NOT NULL DEFAULT '',
  day TEXT NOT NULL DEFAULT '',
  day_count INTEGER NOT NULL DEFAULT 0,
  run_id TEXT NOT NULL DEFAULT '',
  run_count INTEGER NOT NULL DEFAULT 0,
  last_at REAL NOT NULL DEFAULT 0,
  cooldown_until REAL NOT NULL DEFAULT 0,
  cooldown_reason TEXT NOT NULL DEFAULT '',
  fail_streak INTEGER NOT NULL DEFAULT 0,
  alerted_day TEXT NOT NULL DEFAULT '',
  PRIMARY KEY (bucket, key)
);
CREATE TABLE IF NOT EXISTS requests_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts REAL NOT NULL,
  day TEXT NOT NULL,
  run_id TEXT,
  bucket TEXT NOT NULL,
  key TEXT,
  method TEXT,
  url TEXT,
  status INTEGER,
  bytes INTEGER,
  duration_ms INTEGER,
  from_cache INTEGER DEFAULT 0,
  note TEXT
);
CREATE INDEX IF NOT EXISTS requests_log_day ON requests_log(day, bucket);
CREATE TABLE IF NOT EXISTS pages (
  url TEXT PRIMARY KEY,
  domain TEXT NOT NULL,
  fetched_at REAL NOT NULL,
  status INTEGER,
  etag TEXT,
  last_modified TEXT,
  content_type TEXT,
  path TEXT,
  sha256 TEXT
);
CREATE TABLE IF NOT EXISTS robots (
  domain TEXT PRIMARY KEY,
  fetched_day TEXT NOT NULL,
  status INTEGER,
  body TEXT
);

-- data
CREATE TABLE IF NOT EXISTS datasets (
  name TEXT PRIMARY KEY, dataset_id TEXT, downloaded_at TEXT, rows INTEGER, path TEXT);
CREATE TABLE IF NOT EXISTS comparables_cache (key TEXT PRIMARY KEY, computed_at TEXT, json TEXT);
CREATE TABLE IF NOT EXISTS indices (
  segment TEXT, area TEXT, type TEXT, years INTEGER, cagr REAL,
  from_date TEXT, to_date TEXT, source TEXT, computed_at TEXT);
CREATE TABLE IF NOT EXISTS rates (
  date TEXT PRIMARY KEY, sora_3m REAL, bank_rate REAL, hdb_rate REAL, cpf_oa REAL, tbill REAL,
  source_json TEXT);
CREATE TABLE IF NOT EXISTS rules_history (
  rule_id TEXT, value_json TEXT, effective_from TEXT, checked_at TEXT, url TEXT, quote TEXT,
  replaced_at TEXT);
CREATE TABLE IF NOT EXISTS onemap_cache (query TEXT PRIMARY KEY, fetched_at TEXT, json TEXT);
CREATE TABLE IF NOT EXISTS ura_token (day TEXT PRIMARY KEY, token TEXT);

-- pipeline
CREATE TABLE IF NOT EXISTS runs (
  run_id TEXT PRIMARY KEY, kind TEXT, started_at TEXT, finished_at TEXT, status TEXT, note TEXT);
CREATE TABLE IF NOT EXISTS candidates (
  id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT, category_key TEXT, name TEXT, address TEXT,
  area TEXT, district TEXT, price REAL, price_label TEXT, price_source TEXT, price_date TEXT,
  size_sqft REAL, tenure TEXT, lease_start INTEGER, remaining_lease REAL, floor TEXT,
  rent_asking REAL, url TEXT, source_json TEXT, evidence_level TEXT, geo_json TEXT,
  fingerprint TEXT);
CREATE INDEX IF NOT EXISTS candidates_fp ON candidates(fingerprint);
CREATE TABLE IF NOT EXISTS evidence (
  id INTEGER PRIMARY KEY AUTOINCREMENT, candidate_id INTEGER, url TEXT, site TEXT, fetched_at TEXT,
  confirmed INTEGER, extracted_json TEXT, page_path TEXT);
CREATE TABLE IF NOT EXISTS cards (
  id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT, candidate_id INTEGER, eligibility_json TEXT,
  costs_json TEXT, financing_json TEXT, projection_json TEXT, verdict_json TEXT,
  rules_used_json TEXT, comparables_json TEXT, created_at TEXT);
CREATE TABLE IF NOT EXISTS outlooks (
  id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT, area TEXT, segment TEXT, json TEXT);
CREATE TABLE IF NOT EXISTS curated (
  run_id TEXT, card_id TEXT, verdict_why TEXT, risks_json TEXT, what_would_make_it_work TEXT,
  best_intent_note TEXT, what_it_means TEXT, eligibility_note TEXT, mop_note TEXT,
  PRIMARY KEY (run_id, card_id));
CREATE TABLE IF NOT EXISTS post_queue (
  id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT, day TEXT, position INTEGER, kind TEXT,
  ref_id TEXT, text TEXT, link_url TEXT, notify INTEGER DEFAULT 0, status TEXT DEFAULT 'pending');
CREATE TABLE IF NOT EXISTS posts (
  id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT,
  kind TEXT CHECK(kind IN ('listing','bto','outlook','rules_update')),
  ref_id TEXT, chat_id TEXT, message_id INTEGER, post_status TEXT, posted_at TEXT,
  deleted_at TEXT, delete_status TEXT);
CREATE INDEX IF NOT EXISTS posts_chat_msg ON posts(chat_id, message_id);
CREATE TABLE IF NOT EXISTS favorites (
  id INTEGER PRIMARY KEY AUTOINCREMENT, message_id INTEGER, card_id INTEGER, saved_at TEXT,
  last_reanalysed_at TEXT);
CREATE TABLE IF NOT EXISTS profile_overrides (key TEXT PRIMARY KEY, value TEXT);
-- open and upcoming BTO projects, replaced by the weekly check (propbot/bto.py)
CREATE TABLE IF NOT EXISTS bto_upcoming (
  project TEXT PRIMARY KEY, town TEXT, launch TEXT, status TEXT, classification TEXT, flat_types TEXT,
  price_from REAL, price_to REAL, completion TEXT, url TEXT, note TEXT, fetched_on TEXT);
CREATE TABLE IF NOT EXISTS alerts_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, run_id TEXT, kind TEXT, key TEXT, text TEXT,
  sent INTEGER);
CREATE TABLE IF NOT EXISTS pending_rule_changes (
  rule_id TEXT PRIMARY KEY, detected_at TEXT, json TEXT, posted INTEGER DEFAULT 0);

-- HDB resale pulse (data.gov.sg); key is the transaction itself, data.gov.sg _ids change on re-upload
CREATE TABLE IF NOT EXISTS hdb_resale (
  key TEXT PRIMARY KEY, month TEXT, town TEXT, flat_type TEXT, block TEXT, street TEXT, storey TEXT,
  sqm REAL, model TEXT, lease_start INTEGER, remaining_lease REAL, price REAL, first_seen TEXT);
CREATE INDEX IF NOT EXISTS hdb_resale_month ON hdb_resale(month);
CREATE TABLE IF NOT EXISTS hdb_rent (
  quarter TEXT, town TEXT, flat_type TEXT, median_rent REAL, PRIMARY KEY (quarter, town, flat_type));
CREATE TABLE IF NOT EXISTS pulse_alerted (key TEXT PRIMARY KEY, alerted_at TEXT);

-- URA private residential transactions (propbot/ura.py); key is the caveat itself
CREATE TABLE IF NOT EXISTS ura_resi (
  key TEXT PRIMARY KEY, project TEXT, street TEXT, district TEXT, segment TEXT, prop_type TEXT, sale_type TEXT,
  month TEXT, sqm REAL, floor TEXT, tenure TEXT, freehold INTEGER, lease_start INTEGER, price REAL, first_seen TEXT);
CREATE INDEX IF NOT EXISTS ura_resi_month ON ura_resi(month);
CREATE INDEX IF NOT EXISTS ura_resi_project ON ura_resi(project);

-- listings the weekly reports have seen (propbot/tracker.py): first and last seen, price changes, gone
CREATE TABLE IF NOT EXISTS report_listing (
  key TEXT PRIMARY KEY, kind TEXT, name TEXT, area TEXT, url TEXT, bedrooms INTEGER, tenure TEXT,
  lease_left REAL, sqft REAL, price REAL, prev_price REAL, price_changed_on TEXT,
  first_seen TEXT, last_seen TEXT, status TEXT DEFAULT 'listed', gone_on TEXT, gone_by TEXT);
CREATE INDEX IF NOT EXISTS report_listing_kind ON report_listing(kind, status);

-- listing photos for the family website (propbot/site.py); image '' = none found on checked_on
CREATE TABLE IF NOT EXISTS listing_image (key TEXT PRIMARY KEY, image TEXT, checked_on TEXT);
-- PropertyGuru pages opened through the NAS Chrome (propbot/pgcheck.py); only name_ok listed rows reach the website
CREATE TABLE IF NOT EXISTS pg_check (key TEXT PRIMARY KEY, checked_on TEXT, status TEXT, price REAL, name_ok INTEGER, title TEXT);
-- places near a home for the feng shui reading (propbot/fengshui.py), reloaded monthly
CREATE TABLE IF NOT EXISTS poi (kind TEXT, name TEXT, lat REAL, lon REAL, PRIMARY KEY (kind, name));

-- location signals (propbot/geo.py)
CREATE TABLE IF NOT EXISTS mrt_exits (station TEXT, exit_code TEXT, lat REAL, lon REAL, PRIMARY KEY (station, exit_code));

-- one profile per Telegram user for /propprofile and /propanalyse (the owner's lives in Profile.md)
CREATE TABLE IF NOT EXISTS user_profiles (user_id INTEGER, key TEXT, value TEXT, PRIMARY KEY (user_id, key));
"""


class DB:
    def __init__(self, path: str | Path):
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None,
                                    timeout=30)
        self.conn.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        if self.path != ":memory:":
            self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.execute("PRAGMA busy_timeout=30000")
        self.conn.executescript(SCHEMA)

    def execute(self, sql: str, params: Iterable[Any] = ()) -> sqlite3.Cursor:
        with self._lock:
            return self.conn.execute(sql, tuple(params))

    def executemany(self, sql: str, rows: Iterable[Iterable[Any]]) -> None:
        with self._lock:
            self.conn.executemany(sql, rows)

    # The scheduler thread and the Telegram listener share one connection. Reading the rows has to happen
    # inside the lock too, or the other thread's query resets the cursor mid-read (IndexError, wrong rows).
    def one(self, sql: str, params: Iterable[Any] = ()) -> sqlite3.Row | None:
        with self._lock:
            return self.conn.execute(sql, tuple(params)).fetchone()

    def all(self, sql: str, params: Iterable[Any] = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self.conn.execute(sql, tuple(params)).fetchall()

    def scalar(self, sql: str, params: Iterable[Any] = (), default: Any = None) -> Any:
        row = self.one(sql, params)
        return default if row is None or row[0] is None else row[0]

    def insert(self, table: str, values: dict[str, Any]) -> int:
        cols = ", ".join(values)
        marks = ", ".join("?" for _ in values)
        cur = self.execute(f"INSERT INTO {table} ({cols}) VALUES ({marks})",
                           [_to_db(v) for v in values.values()])
        return int(cur.lastrowid)

    def upsert(self, table: str, values: dict[str, Any], key: str | list[str]) -> None:
        keys = [key] if isinstance(key, str) else key
        cols = ", ".join(values)
        marks = ", ".join("?" for _ in values)
        updates = ", ".join(f"{c}=excluded.{c}" for c in values if c not in keys)
        conflict = ", ".join(keys)
        sql = f"INSERT INTO {table} ({cols}) VALUES ({marks}) ON CONFLICT({conflict}) DO "
        sql += f"UPDATE SET {updates}" if updates else "NOTHING"
        self.execute(sql, [_to_db(v) for v in values.values()])

    def meta_get(self, key: str, default: str | None = None) -> str | None:
        return self.scalar("SELECT value FROM meta WHERE key=?", (key,), default)

    def meta_set(self, key: str, value: str) -> None:
        self.upsert("meta", {"key": key, "value": value}, "key")

    def close(self) -> None:
        with self._lock:
            self.conn.close()


def _to_db(value: Any) -> Any:
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(value, default=str)
    if isinstance(value, bool):
        return int(value)
    return value


def profile_overrides(db: DB) -> dict[str, Any]:
    out = {}
    for row in db.all("SELECT key, value FROM profile_overrides"):
        out[row["key"]] = json.loads(row["value"])
    return out


def set_profile_override(db: DB, key: str, value: Any) -> None:
    db.upsert("profile_overrides", {"key": key, "value": json.dumps(value)}, "key")


def user_profile(db: DB, user_id: int) -> dict[str, Any]:
    return {r["key"]: json.loads(r["value"]) for r in db.all("SELECT key, value FROM user_profiles WHERE user_id=?",
                                                              (user_id,))}


def set_user_profile(db: DB, user_id: int, values: dict[str, Any]) -> None:
    for k, v in values.items():
        db.upsert("user_profiles", {"user_id": user_id, "key": k, "value": json.dumps(v)}, ["user_id", "key"])


def clear_user_profile(db: DB, user_id: int) -> None:
    db.execute("DELETE FROM user_profiles WHERE user_id=?", (user_id,))
