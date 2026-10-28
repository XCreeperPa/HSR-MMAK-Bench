"""Append only timing events and the durations derived from them.

The service never trusts a client clock. Every observation is a server side
timestamp taken at a named moment, stored once, and never rewritten. Derived
durations are recomputed from the raw events, so the measurement policy can
change without losing the original observation.

Event names follow section 5 of the design document.
"""
from __future__ import annotations

import sqlite3
import time
import uuid
from datetime import datetime, timezone

from .db import connect, now_iso

EVENT_TYPES = (
    'timing_recovered_from_issued_at',
    'test_started_at',
    'test_timeout_at',
    'question_issued_at',
    'question_send_started_at',
    'question_send_finished_at',
    'asset_request_received_at',
    'asset_send_started_at',
    'asset_send_finished_at',
    'asset_transfer_failed_at',
    'client_materials_ready_received_at',
    'answer_received_at',
    'answer_committed_at',
    'test_completed_at',
)


def new_request_id() -> str:
    return uuid.uuid4().hex


def record_event(
    event_type: str,
    *,
    assessment_id: int | None = None,
    question_id: str | None = None,
    delivery_id: str | None = None,
    request_id: str | None = None,
    asset_id: str | None = None,
    transfer_id: str | None = None,
    http_status: int | None = None,
    range_header: str | None = None,
    bytes_sent: int | None = None,
    byte_start: int | None = None,
    byte_end: int | None = None,
    result: str | None = None,
    meta: dict | None = None,
    conn: sqlite3.Connection | None = None,
) -> str:
    """Append one event. Uses its own connection when none is supplied."""
    import json as _json

    payload = (
        assessment_id,
        question_id,
        delivery_id,
        request_id,
        event_type,
        asset_id,
        transfer_id,
        http_status,
        range_header,
        bytes_sent,
        byte_start,
        byte_end,
        result,
        now_iso(),
        _json.dumps(meta or {}, ensure_ascii=False),
    )
    sql = (
        'INSERT INTO events(assessment_id,question_id,delivery_id,request_id,event_type,asset_id,'
        'transfer_id,http_status,range_header,bytes_sent,byte_start,byte_end,result,server_time,meta_json) '
        'VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)'
    )
    if conn is not None:
        conn.execute(sql, payload)
    else:
        own = connect()
        try:
            own.execute(sql, payload)
        finally:
            own.close()
    return now_iso()


def first_event_time(conn: sqlite3.Connection, assessment_id: int, question_id: str, event_type: str) -> str | None:
    row = conn.execute(
        'SELECT server_time FROM events WHERE assessment_id=? AND question_id=? AND event_type=? '
        'ORDER BY id LIMIT 1',
        (assessment_id, question_id, event_type),
    ).fetchone()
    return row['server_time'] if row else None


def parse_time(value: str | None) -> float | None:
    if not value:
        return None
    text = value.replace('Z', '+00:00')
    try:
        return datetime.fromisoformat(text).timestamp()
    except ValueError:
        return None


def derive_durations(conn: sqlite3.Connection, assessment_id: int, question_id: str) -> dict:
    """Compute the three documented duration measures for one question."""
    delivery = conn.execute(
        'SELECT question_id, first_send_started_at, delivery_complete_at, client_ready_at '
        'FROM question_deliveries WHERE assessment_id=? AND question_id=?',
        (assessment_id, question_id),
    ).fetchone()
    answer = conn.execute(
        'SELECT answer_received_at FROM answers WHERE assessment_id=? AND question_id=?',
        (assessment_id, question_id),
    ).fetchone()
    if not delivery or not answer:
        return {}

    received = parse_time(answer['answer_received_at'])
    sent = parse_time(delivery['first_send_started_at'])
    complete = parse_time(delivery['delivery_complete_at'])
    ready = parse_time(delivery['client_ready_at'])

    def ms(later: float | None, earlier: float | None) -> int | None:
        if later is None or earlier is None:
            return None
        return max(0, round((later - earlier) * 1000))

    return {
        'duration_with_delivery_ms': ms(received, sent),
        'duration_post_delivery_ms': ms(received, complete),
        'duration_client_ready_ms': ms(received, ready),
    }


def monotonic_ms() -> float:
    return round(time.monotonic() * 1000, 3)


def merge_ranges(ranges: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """Merge byte ranges into the smallest set of contiguous intervals."""
    if not ranges:
        return []
    ordered = sorted(ranges)
    merged = [list(ordered[0])]
    for start, end in ordered[1:]:
        if start <= merged[-1][1] + 1:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return [(start, end) for start, end in merged]


def asset_covered(conn: sqlite3.Connection, assessment_id: int, question_id: str, asset_id: str, size: int) -> bool:
    """True when the whole asset was delivered by at least one successful transfer."""
    rows = conn.execute(
        'SELECT byte_start, byte_end FROM asset_transfers WHERE assessment_id=? AND question_id=? '
        "AND asset_id=? AND result='complete'",
        (assessment_id, question_id, asset_id),
    ).fetchall()
    merged = merge_ranges([(r['byte_start'], r['byte_end']) for r in rows])
    return bool(merged) and merged[0][0] <= 0 and merged[0][1] >= size - 1


def refresh_delivery_complete(
    conn: sqlite3.Connection,
    assessment_id: int,
    question_id: str,
    required_assets: list[tuple[str, int]],
) -> str | None:
    """Set the delivery completion moment once every required asset is covered.

    Repeated requests never overwrite an existing completion moment and an
    interrupted transfer never counts as a complete delivery.
    """
    row = conn.execute(
        'SELECT delivery_complete_at FROM question_deliveries WHERE assessment_id=? AND question_id=?',
        (assessment_id, question_id),
    ).fetchone()
    if not row or row['delivery_complete_at']:
        return row['delivery_complete_at'] if row else None
    for asset_id, size in required_assets:
        if not asset_covered(conn, assessment_id, question_id, asset_id, size):
            return None
    moment = now_iso()
    conn.execute(
        'UPDATE question_deliveries SET delivery_complete_at=? WHERE assessment_id=? AND question_id=? '
        'AND delivery_complete_at IS NULL',
        (moment, assessment_id, question_id),
    )
    return moment
