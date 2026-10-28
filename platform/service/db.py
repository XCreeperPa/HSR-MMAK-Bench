"""SQLite storage for the formal test service.

Design rules taken from ``reference/V2正式测试架构设计.md``:

* one row per test in ``assessments``; the code and the bank snapshot are fixed
  at creation time and never change afterwards;
* answers are append only and are keyed by ``(assessment_id, question_id)``
  and ``(assessment_id, submission_id)`` so a retry can never score twice;
* every timing observation is an appended ``events`` row. Derived durations
  are recomputed from those rows and never overwrite them;
* the admin console only ever reads raw records plus an explicit marker.
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from .config import settings

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS contestants (
  id INTEGER PRIMARY KEY,
  roster_key TEXT UNIQUE,
  subject_type TEXT NOT NULL CHECK(subject_type IN ('human','agent')),
  roster_status TEXT NOT NULL DEFAULT 'confirmed',
  display_name TEXT,
  reasoning_effort TEXT,
  agent_host TEXT,
  invocation_route TEXT,
  model_id TEXT,
  runtime_config_json TEXT NOT NULL DEFAULT '{}',
  source_refs_json TEXT NOT NULL DEFAULT '[]',
  profile_note TEXT,
  admin_note TEXT,
  include_in_statistics INTEGER NOT NULL DEFAULT 1 CHECK(include_in_statistics IN (0,1)),
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS assessments (
  id INTEGER PRIMARY KEY,
  contestant_id INTEGER NOT NULL REFERENCES contestants(id),
  code TEXT NOT NULL UNIQUE,
  code_digest TEXT NOT NULL UNIQUE,
  code_cipher TEXT NOT NULL,
  code_status TEXT NOT NULL DEFAULT 'active'
    CHECK(code_status IN ('active','used','voided')),
  bank_version TEXT NOT NULL,
  scoring_rules_version TEXT NOT NULL,
  timing_policy_version TEXT NOT NULL,
  question_order_json TEXT NOT NULL,
  option_order_json TEXT NOT NULL,
  task_document_version TEXT NOT NULL,
  status TEXT NOT NULL CHECK(status IN ('issued','ready','in_progress','completed','voided')),
  survey_required INTEGER NOT NULL DEFAULT 0,
  test_started_at TEXT,
  test_timeout_at TEXT,
  completed_at TEXT,
  voided_at TEXT,
  void_reason TEXT,
  total_score REAL,
  total_score_max REAL,
  include_in_statistics INTEGER NOT NULL DEFAULT 1 CHECK(include_in_statistics IN (0,1)),
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS login_sessions (
  token_hash TEXT PRIMARY KEY,
  assessment_id INTEGER REFERENCES assessments(id),
  role TEXT NOT NULL,
  expires_at REAL NOT NULL,
  revoked INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS surveys (
  assessment_id INTEGER NOT NULL REFERENCES assessments(id),
  question_id TEXT NOT NULL,
  value TEXT NOT NULL,
  survey_version TEXT NOT NULL,
  submitted_at TEXT NOT NULL,
  PRIMARY KEY (assessment_id, question_id)
);

CREATE TABLE IF NOT EXISTS question_deliveries (
  id INTEGER PRIMARY KEY,
  assessment_id INTEGER NOT NULL REFERENCES assessments(id),
  question_id TEXT NOT NULL,
  delivery_id TEXT NOT NULL UNIQUE,
  question_order INTEGER NOT NULL,
  issued_at TEXT NOT NULL,
  first_send_started_at TEXT,
  first_send_finished_at TEXT,
  delivery_complete_at TEXT,
  client_ready_at TEXT,
  UNIQUE (assessment_id, question_id)
);

CREATE TABLE IF NOT EXISTS answers (
  id INTEGER PRIMARY KEY,
  assessment_id INTEGER NOT NULL REFERENCES assessments(id),
  question_id TEXT NOT NULL,
  submission_id TEXT NOT NULL,
  question_order INTEGER NOT NULL,
  payload_digest TEXT NOT NULL,
  answer_json TEXT NOT NULL,
  reasons_json TEXT NOT NULL DEFAULT '{}',
  annotations_json TEXT NOT NULL DEFAULT '[]',
  selected_options TEXT NOT NULL DEFAULT '',
  score REAL NOT NULL,
  score_max REAL NOT NULL,
  answer_received_at TEXT NOT NULL,
  answer_committed_at TEXT NOT NULL,
  delivery_complete_at TEXT,
  duration_with_delivery_ms INTEGER,
  duration_post_delivery_ms INTEGER,
  duration_client_ready_ms INTEGER,
  delivery_incomplete INTEGER NOT NULL DEFAULT 0,
  abandoned INTEGER NOT NULL DEFAULT 0,
  UNIQUE (assessment_id, question_id),
  UNIQUE (assessment_id, submission_id)
);

-- Every successful byte range transfer is appended here so the coverage of a
-- media file can be rebuilt from the wire record, not from client claims.
CREATE TABLE IF NOT EXISTS asset_transfers (
  id INTEGER PRIMARY KEY,
  assessment_id INTEGER NOT NULL REFERENCES assessments(id),
  question_id TEXT NOT NULL,
  asset_id TEXT NOT NULL,
  transfer_id TEXT NOT NULL,
  byte_start INTEGER NOT NULL,
  byte_end INTEGER NOT NULL,
  bytes_sent INTEGER NOT NULL,
  http_status INTEGER,
  range_header TEXT,
  result TEXT NOT NULL,
  completed_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY,
  assessment_id INTEGER REFERENCES assessments(id),
  question_id TEXT,
  delivery_id TEXT,
  request_id TEXT,
  event_type TEXT NOT NULL,
  asset_id TEXT,
  transfer_id TEXT,
  http_status INTEGER,
  range_header TEXT,
  bytes_sent INTEGER,
  byte_start INTEGER,
  byte_end INTEGER,
  result TEXT,
  server_time TEXT NOT NULL,
  meta_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS assessment_tags (
  id INTEGER PRIMARY KEY,
  assessment_id INTEGER REFERENCES assessments(id),
  contestant_id INTEGER REFERENCES contestants(id),
  tag TEXT NOT NULL,
  note TEXT,
  exclude_from_statistics INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL,
  created_by TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS audit_log (
  id INTEGER PRIMARY KEY,
  at TEXT NOT NULL,
  actor TEXT NOT NULL,
  action TEXT NOT NULL,
  assessment_id INTEGER,
  contestant_id INTEGER,
  detail TEXT
);

CREATE TABLE IF NOT EXISTS bank_versions (
  version TEXT PRIMARY KEY,
  payload_json TEXT NOT NULL,
  content_digest TEXT NOT NULL,
  created_at TEXT NOT NULL
);

-- Public question payloads and private answers are stored separately so the
-- private table is never loaded into a contestant facing code path.
CREATE TABLE IF NOT EXISTS bank_keys (
  version TEXT PRIMARY KEY,
  key_json TEXT NOT NULL,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS system_state (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS login_attempts (
  id INTEGER PRIMARY KEY,
  ip TEXT NOT NULL,
  at REAL NOT NULL,
  ok INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_events_assessment ON events(assessment_id, event_type);
CREATE INDEX IF NOT EXISTS idx_answers_assessment ON answers(assessment_id, question_order);
CREATE INDEX IF NOT EXISTS idx_assessments_contestant ON assessments(contestant_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_assessments_active_code ON assessments(contestant_id)
  WHERE status IN ('issued','ready','in_progress');
"""


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%f')[:-3] + 'Z'


def connect() -> sqlite3.Connection:
    cfg = settings()
    cfg.ensure_dirs()
    conn = sqlite3.connect(cfg.db_path, timeout=30, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA busy_timeout=30000')
    conn.execute('PRAGMA foreign_keys=ON')
    return conn


def init_db() -> None:
    cfg = settings()
    cfg.ensure_dirs()
    conn = connect()
    try:
        conn.executescript(SCHEMA)
        _migrate(conn)
    finally:
        conn.close()


# Columns added after the first release. Each entry is safe to re-run.
MIGRATIONS: tuple[tuple[str, str, str], ...] = (
    ('assessments', 'code_status', "TEXT NOT NULL DEFAULT 'active'"),
)


def _migrate(conn: sqlite3.Connection) -> None:
    for table, column, declaration in MIGRATIONS:
        columns = {row[1] for row in conn.execute(f'PRAGMA table_info({table})')}
        if column not in columns:
            conn.execute(f'ALTER TABLE {table} ADD COLUMN {column} {declaration}')
    conn.commit()


@contextmanager
def db() -> Iterator[sqlite3.Connection]:
    """Autocommit connection with an explicit transaction helper.

    ``conn.execute('BEGIN IMMEDIATE')`` inside the block starts a write
    transaction; everything before it is read-only.
    """
    conn = connect()
    try:
        yield conn
    finally:
        conn.close()


@contextmanager
def write_tx(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """Run a block inside one IMMEDIATE transaction, rolling back on error."""
    conn.execute('BEGIN IMMEDIATE')
    try:
        yield conn
    except Exception:
        conn.execute('ROLLBACK')
        raise
    else:
        conn.execute('COMMIT')


def bump_revision(conn: sqlite3.Connection) -> int:
    """Monotonic counter used by the admin console for incremental refresh."""
    row = conn.execute("SELECT value FROM system_state WHERE key='revision'").fetchone()
    current = int(row['value']) if row else 0
    nxt = current + 1
    conn.execute(
        "INSERT INTO system_state(key,value) VALUES('revision',?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (str(nxt),),
    )
    return nxt


def read_revision(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT value FROM system_state WHERE key='revision'").fetchone()
    return int(row['value']) if row else 0


def audit(
    conn: sqlite3.Connection,
    actor: str,
    action: str,
    *,
    assessment_id: int | None = None,
    contestant_id: int | None = None,
    detail: str | None = None,
) -> None:
    conn.execute(
        'INSERT INTO audit_log(at,actor,action,assessment_id,contestant_id,detail) VALUES (?,?,?,?,?,?)',
        (now_iso(), actor, action, assessment_id, contestant_id, detail),
    )


def safe_rollback(conn: sqlite3.Connection) -> None:
    """Roll back only when a transaction is actually open."""
    if conn.in_transaction:
        try:
            conn.execute('ROLLBACK')
        except sqlite3.OperationalError:  # pragma: no cover - defensive
            pass
