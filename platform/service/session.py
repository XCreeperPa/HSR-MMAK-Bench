"""Login sessions and contestant context resolution.

Both entry points resolve to the same kind of context. A browser receives an
HttpOnly cookie, an agent receives a bearer token; the server parses either
into exactly one test context. The difference between a human and an agent
lives in the session record, not in two parallel sets of endpoints.
"""
from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass

from fastapi import HTTPException, Request

from .config import settings
from .crypto import code_digest, decrypt_code, new_token, token_digest
from .db import now_iso


@dataclass
class ContestantContext:
    assessment: sqlite3.Row
    contestant: sqlite3.Row
    token_hash: str
    is_agent: bool


def bearer_token(request: Request) -> str | None:
    header = request.headers.get('Authorization', '')
    if header.lower().startswith('bearer '):
        return header[7:].strip() or None
    return None


def cookie_token(request: Request) -> str | None:
    return request.cookies.get('hsr_service_session') or None


def create_session(conn: sqlite3.Connection, assessment_id: int, role: str = 'contestant') -> str:
    token = new_token()
    ttl = settings().session_ttl_seconds
    conn.execute(
        'INSERT INTO login_sessions(token_hash,assessment_id,role,expires_at,revoked,created_at) VALUES (?,?,?,?,0,?)',
        (token_digest(token), assessment_id, role, time.time() + ttl, now_iso()),
    )
    return token


def resolve_token(conn: sqlite3.Connection, token: str | None) -> sqlite3.Row:
    if not token:
        raise HTTPException(401, '未登录')
    row = conn.execute(
        'SELECT * FROM login_sessions WHERE token_hash=? AND revoked=0 AND expires_at>?',
        (token_digest(token), time.time()),
    ).fetchone()
    if not row:
        raise HTTPException(401, '登录状态已失效，请重新输入身份验证码')
    return row


def contestant_context(conn: sqlite3.Connection, request: Request) -> ContestantContext:
    token = bearer_token(request) or cookie_token(request)
    session = resolve_token(conn, token)
    if session['role'] != 'contestant':
        raise HTTPException(401, '登录状态已失效，请重新输入身份验证码')
    assessment = conn.execute('SELECT * FROM assessments WHERE id=?', (session['assessment_id'],)).fetchone()
    if not assessment:
        raise HTTPException(401, '测试不存在')
    if assessment['status'] == 'voided':
        raise HTTPException(403, '本次测试已作废，请向管理员索取新的身份验证码')
    contestant = conn.execute('SELECT * FROM contestants WHERE id=?', (assessment['contestant_id'],)).fetchone()
    if not contestant:
        raise HTTPException(401, '测试不存在')
    return ContestantContext(
        assessment=assessment,
        contestant=contestant,
        token_hash=session['token_hash'],
        is_agent=contestant['subject_type'] == 'agent',
    )


def admin_context(conn: sqlite3.Connection, request: Request) -> str:
    token = bearer_token(request) or request.cookies.get('hsr_service_admin')
    session = resolve_token(conn, token)
    if session['role'] != 'admin':
        raise HTTPException(401, '管理员认证失败')
    return 'admin'


def revoke_assessment_sessions(conn: sqlite3.Connection, assessment_id: int) -> None:
    conn.execute('UPDATE login_sessions SET revoked=1 WHERE assessment_id=?', (assessment_id,))


def failed_login_recent(conn: sqlite3.Connection, ip: str) -> int:
    window = time.time() - settings().login_rate_window_seconds
    row = conn.execute(
        'SELECT COUNT(*) FROM login_attempts WHERE ip=? AND ok=0 AND at>?', (ip, window)
    ).fetchone()
    return int(row[0]) if row else 0


def record_login_attempt(conn: sqlite3.Connection, ip: str, ok: bool) -> None:
    conn.execute('INSERT INTO login_attempts(ip,at,ok) VALUES (?,?,?)', (ip, time.time(), int(ok)))
    # Keep the table small; old rows are never useful for rate limiting.
    conn.execute('DELETE FROM login_attempts WHERE at < ?', (time.time() - 86400,))


def current_code_for(conn: sqlite3.Connection, assessment_id: int) -> str | None:
    """Decrypt the code of an assessment for the admin console."""
    row = conn.execute('SELECT code_cipher FROM assessments WHERE id=?', (assessment_id,)).fetchone()
    if not row:
        return None
    try:
        return decrypt_code(row['code_cipher'])
    except ValueError:
        return None


def lookup_by_code(conn: sqlite3.Connection, code: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM assessments WHERE code_digest=? AND code_status='active'",
        (code_digest(code),),
    ).fetchone()
