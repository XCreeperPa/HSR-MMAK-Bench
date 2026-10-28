"""Test lifecycle: code issuance, automatic rotation, voiding and reissue.

One state machine drives both automatic rotation and the manual void
operation, so there is only one place where a code can be created or retired.

    issued -> ready -> in_progress -> completed
       |                              |
       |                              +-> next test issued (new code)
       +-> voided -> next test issued (new code)

A code is bound to exactly one test for its whole life and is never reused.
Each contestant holds at most one usable code; the database enforces that
with a partial unique index so a concurrent rotation cannot hand out two.
"""
from __future__ import annotations

import json
import sqlite3

from .bank import BankSnapshot, load_snapshot
from .config import settings
from .crypto import code_digest, decrypt_code, encrypt_code, new_six_digit_code
from .db import audit, bump_revision, now_iso

MAX_CODES = 1_000_000


class CodeSpaceExhausted(RuntimeError):
    """Raised when the six digit code space is exhausted; codes are never reused."""


def _allocate_code(conn: sqlite3.Connection) -> str:
    used = conn.execute('SELECT COUNT(*) FROM assessments').fetchone()[0]
    if used >= MAX_CODES:
        raise CodeSpaceExhausted('the six digit code space is exhausted')
    if used >= MAX_CODES - settings().code_space_warning:
        print(f'[lifecycle] warning: only {MAX_CODES - used} codes left in the code space')
    for _ in range(64):
        code = new_six_digit_code()
        if not conn.execute('SELECT 1 FROM assessments WHERE code=?', (code,)).fetchone():
            return code
    raise CodeSpaceExhausted('could not allocate a free six digit code')


def create_assessment(
    conn: sqlite3.Connection,
    contestant_id: int,
    snapshot: BankSnapshot,
    *,
    actor: str = 'system',
    include_in_statistics: int | None = None,
) -> tuple[int, str]:
    """Create one test with its own code. Returns (assessment_id, code)."""
    latest = conn.execute('SELECT version FROM bank_versions ORDER BY created_at DESC LIMIT 1').fetchone()
    if latest and latest['version'] != snapshot.version:
        snapshot = load_snapshot(conn, latest['version'])
    code = _allocate_code(conn)
    contestant = conn.execute('SELECT * FROM contestants WHERE id=?', (contestant_id,)).fetchone()
    if not contestant:
        raise ValueError(f'unknown contestant {contestant_id}')
    survey_required = 1 if contestant['subject_type'] == 'human' else 0
    statistics = (
        include_in_statistics
        if include_in_statistics is not None
        else int(contestant['include_in_statistics'])
    )
    cur = conn.execute(
        '''INSERT INTO assessments(contestant_id,code,code_digest,code_cipher,bank_version,
             scoring_rules_version,timing_policy_version,question_order_json,option_order_json,
             task_document_version,status,survey_required,include_in_statistics,created_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,'issued',?,?,?)''',
        (
            contestant_id,
            code,
            code_digest(code),
            encrypt_code(code),
            snapshot.version,
            snapshot.version,
            'timing-v1',
            json.dumps(snapshot.public['question_order'], ensure_ascii=False),
            json.dumps(snapshot.public['option_order'], ensure_ascii=False),
            settings().task_document_version,
            survey_required,
            statistics,
            now_iso(),
        ),
    )
    assessment_id = int(cur.lastrowid)
    audit(conn, actor, 'assessment.created', assessment_id=assessment_id, contestant_id=contestant_id,
          detail=f'status=issued survey_required={survey_required}')
    return assessment_id, code


def ensure_active_assessments(conn: sqlite3.Connection, snapshot: BankSnapshot) -> dict:
    """Make sure every official contestant holds exactly one usable code.

    Reading the admin page, refreshing it or restarting the server must never
    create a second code for the same contestant.
    """
    created: list[dict] = []
    for contestant in conn.execute(
        'SELECT * FROM contestants WHERE include_in_statistics=1 ORDER BY id'
    ).fetchall():
        active = conn.execute(
            "SELECT id FROM assessments WHERE contestant_id=? AND status IN ('issued','ready','in_progress')",
            (contestant['id'],),
        ).fetchone()
        if active:
            continue
        assessment_id, code = create_assessment(conn, contestant['id'], snapshot, actor='system')
        created.append({'contestant_id': contestant['id'], 'assessment_id': assessment_id, 'code': code})
    if created:
        bump_revision(conn)
    return {'created': created, 'count': len(created)}


def rotate_after_completion(conn: sqlite3.Connection, snapshot: BankSnapshot, assessment_id: int) -> dict:
    """Complete a test and hand the contestant the next code, in one transaction."""
    row = conn.execute('SELECT * FROM assessments WHERE id=?', (assessment_id,)).fetchone()
    if not row:
        raise ValueError(f'unknown assessment {assessment_id}')
    contestant_id = int(row['contestant_id'])
    conn.execute("UPDATE assessments SET code_status='used' WHERE id=?", (assessment_id,))
    new_id, new_code = create_assessment(conn, contestant_id, snapshot, actor='system')
    audit(conn, 'system', 'assessment.rotated', assessment_id=assessment_id, contestant_id=contestant_id,
          detail=f'next_assessment_id={new_id}')
    bump_revision(conn)
    return {'next_assessment_id': new_id, 'next_code': new_code}


def void_and_reissue(
    conn: sqlite3.Connection,
    snapshot: BankSnapshot,
    assessment_id: int,
    reason: str,
    actor: str = 'admin',
) -> dict:
    """Void the current attempt and issue a fresh code in the same transaction.

    The old test keeps all of its answers, events and markers; its code stops
    working immediately and any token issued for it can no longer submit.
    """
    row = conn.execute('SELECT * FROM assessments WHERE id=?', (assessment_id,)).fetchone()
    if not row:
        raise ValueError(f'unknown assessment {assessment_id}')
    if row['status'] == 'voided':
        raise ValueError('this attempt is already voided')
    if row['status'] not in ('issued', 'ready', 'in_progress'):
        raise ValueError(
            'only the current attempt can be voided; a finished test is excluded '
            'from the statistics with a management marker instead'
        )
    contestant_id = int(row['contestant_id'])
    conn.execute(
        "UPDATE assessments SET status='voided', code_status='voided', voided_at=?, void_reason=? WHERE id=?",
        (now_iso(), reason, assessment_id),
    )
    conn.execute('UPDATE login_sessions SET revoked=1 WHERE assessment_id=?', (assessment_id,))
    conn.execute(
        '''INSERT INTO assessment_tags(assessment_id,contestant_id,tag,note,exclude_from_statistics,
             created_at,created_by) VALUES (?,?,?,?,1,?,?)''',
        (assessment_id, contestant_id, 'voided', reason, now_iso(), actor),
    )
    new_id, new_code = create_assessment(conn, contestant_id, snapshot, actor=actor)
    audit(conn, actor, 'assessment.voided', assessment_id=assessment_id, contestant_id=contestant_id,
          detail=f'reason={reason}; next_assessment_id={new_id}')
    bump_revision(conn)
    return {'voided_assessment_id': assessment_id, 'next_assessment_id': new_id, 'next_code': new_code}


def add_tag(
    conn: sqlite3.Connection,
    *,
    assessment_id: int | None,
    contestant_id: int | None,
    tag: str,
    note: str,
    exclude_from_statistics: bool,
    actor: str = 'admin',
) -> int:
    cur = conn.execute(
        '''INSERT INTO assessment_tags(assessment_id,contestant_id,tag,note,exclude_from_statistics,
             created_at,created_by) VALUES (?,?,?,?,?,?,?)''',
        (assessment_id, contestant_id, tag, note, int(exclude_from_statistics), now_iso(), actor),
    )
    audit(conn, actor, 'tag.added', assessment_id=assessment_id, contestant_id=contestant_id,
          detail=f'tag={tag}; exclude_from_statistics={int(exclude_from_statistics)}')
    bump_revision(conn)
    return int(cur.lastrowid)


def current_code(conn: sqlite3.Connection, assessment_id: int) -> str | None:
    row = conn.execute('SELECT code_cipher FROM assessments WHERE id=?', (assessment_id,)).fetchone()
    if not row:
        return None
    try:
        return decrypt_code(row['code_cipher'])
    except ValueError:
        return None
