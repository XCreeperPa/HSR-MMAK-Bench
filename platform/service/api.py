"""The shared contestant API.

Human and agent use the same endpoints. The difference between the two lives
in the session record: a human answer carries no per option reasons, an agent
answer does, and the human flow is the only one that has a survey.

Delivery is strictly sequential. Submitting the current question is the only
way to receive the next one, and the response to that submission already
carries the next question.
"""
from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from .bank import BankSnapshot, load_snapshot
from .config import settings
from .crypto import payload_digest
from .db import audit, bump_revision, db, now_iso, safe_rollback
from .errors import ApiError, conflict, forbidden, rate_limited, timeout, unauthorized, unprocessable
from .events import derive_durations, record_event, refresh_delivery_complete
from .lifecycle import rotate_after_completion
from .media import asset_response, media_store
from .scoring import evaluate_points, score_choice
from .survey import SURVEY_VERSION, ActiveDaysRange
from .session import (contestant_context, create_session, failed_login_recent,
                      lookup_by_code, record_login_attempt)

router = APIRouter()

FAMILIARITY_LEVELS = {
    1: '几乎不了解',
    2: '了解较少',
    3: '了解一般',
    4: '比较熟悉',
    5: '非常熟悉主线、角色及背景',
}
# Point identification questions whose agent answers must carry a recognition
# reason for every annotation. The V2 bank has four module 1 questions and the
# design document asks for reasons on the fourth one only.
QUESTIONS_REQUIRING_ANNOTATION_REASONS = {'q0004'}


def _iso_now() -> str:
    return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%f')[:-3] + 'Z'


def _parse(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp()
    except ValueError:
        return None


def _snapshot_for(conn: sqlite3.Connection, assessment: sqlite3.Row) -> BankSnapshot:
    return load_snapshot(conn, assessment['bank_version'])


def _base_url(request: Request) -> str:
    configured = settings().public_base_url
    if configured:
        return configured
    return str(request.base_url).rstrip('/')


def _module_name(snapshot: BankSnapshot, module: object) -> str:
    """Resolve the display name of a module.

    The manifest numbers the modules ``module_1`` … ``module_4`` while a
    question carries the plain number, so both forms are accepted.
    """
    if module is None:
        return ''
    number = module
    if isinstance(module, str) and module.startswith('module_'):
        number = module.split('_')[-1]
    for entry in snapshot.public.get('modules', []):
        entry_id = str(entry.get('id', ''))
        if entry_id == str(module) or entry_id.split('_')[-1] == str(number):
            return str(entry.get('name', ''))
    return ''


def _question_payload(
    conn: sqlite3.Connection,
    assessment: sqlite3.Row,
    snapshot: BankSnapshot,
    question: dict,
    delivery_id: str,
    order: int,
    base_url: str,
) -> dict:
    """Build the public payload of one question. Never contains an answer."""
    store = media_store()
    assets = []
    for asset_id in snapshot.asset_ids(question):
        entry = store.manifest_entry(asset_id, base_url)
        assets.append(entry)
    payload = {
        'question_id': question['id'],
        'delivery_id': delivery_id,
        'order': order,
        'question_count': snapshot.question_count,
        'module': question.get('module'),
        'module_name': _module_name(snapshot, question.get('module')),
        'type': question['type'],
        'prompt': question['prompt'],
        'score': question['score'],
        'options': question.get('options', []),
        'assets': assets,
        'time_budget': _time_budget_state(assessment),
    }
    if question.get('instructions'):
        payload['instructions'] = question['instructions']
    if question.get('selection_target'):
        payload['selection_target'] = question['selection_target']
    if question['type'] == 'point_identification':
        payload['image_width'] = question.get('image_width')
        payload['image_height'] = question.get('image_height')
        payload['coordinate_space'] = question.get('coordinate_space', 'original_image_pixels')
        payload['characters'] = snapshot.public['characters']['characters']
    return payload


def _ensure_delivery(
    conn: sqlite3.Connection,
    assessment: sqlite3.Row,
    question: dict,
    order: int,
) -> str:
    row = conn.execute(
        'SELECT delivery_id FROM question_deliveries WHERE assessment_id=? AND question_id=?',
        (assessment['id'], question['id']),
    ).fetchone()
    if row:
        return row['delivery_id']
    delivery_id = uuid.uuid4().hex
    conn.execute(
        'INSERT INTO question_deliveries(assessment_id,question_id,delivery_id,question_order,issued_at) '
        'VALUES (?,?,?,?,?)',
        (assessment['id'], question['id'], delivery_id, order, now_iso()),
    )
    record_event('question_issued_at', assessment_id=assessment['id'], question_id=question['id'],
                 delivery_id=delivery_id, conn=conn)
    return delivery_id


def _current_question(snapshot: BankSnapshot, answered_count: int) -> dict | None:
    if answered_count >= snapshot.question_count:
        return None
    return snapshot.public['questions'][answered_count]


def _answered_count(conn: sqlite3.Connection, assessment_id: int) -> int:
    return int(conn.execute(
        'SELECT COUNT(*) FROM answers WHERE assessment_id=?', (assessment_id,)
    ).fetchone()[0])


def _survey_state(conn: sqlite3.Connection, assessment_id: int) -> dict:
    rows = conn.execute(
        'SELECT question_id, value, survey_version FROM surveys WHERE assessment_id=?', (assessment_id,)
    ).fetchall()
    values = {r['question_id']: r['value'] for r in rows}
    return {
        'complete': 'familiarity' in values and ('active_days_range' in values or 'active_days' in values),
        'active_days': values.get('active_days'),
        'active_days_range': values.get('active_days_range'),
        'familiarity': values.get('familiarity'),
        'survey_version': next((r['survey_version'] for r in rows if r['question_id'] == 'familiarity'), SURVEY_VERSION),
    }


def _effective_status(assessment: sqlite3.Row, survey_complete: bool) -> str:
    status = assessment['status']
    if status == 'issued':
        if assessment['survey_required']:
            return 'ready' if survey_complete else 'survey_required'
        return 'ready'
    return status


def _time_budget_state(assessment: sqlite3.Row) -> dict:
    started = _parse(assessment['test_started_at'])
    if started is None:
        return {'started_at': None, 'budget_seconds': settings().time_budget_seconds,
                'remaining_seconds': None, 'expired': False}
    elapsed = max(0.0, datetime.now(timezone.utc).timestamp() - started)
    budget = settings().time_budget_seconds
    return {
        'started_at': assessment['test_started_at'],
        'budget_seconds': budget,
        'remaining_seconds': max(0, int(budget - elapsed)),
        'expired': elapsed > budget,
    }


def _require_started(assessment: sqlite3.Row) -> None:
    if assessment['status'] in ('issued', 'ready') or not assessment['test_started_at']:
        raise conflict('请先完成问卷（如需要）并调用 start 开始计时', 'assessment_not_started')


def _expire_if_needed(conn, assessment, snapshot) -> bool:
    """Serialize timeout with submissions so polling cannot rotate a code twice."""
    if assessment['status'] != 'in_progress' or not _time_budget_state(assessment)['expired']:
        return False
    conn.execute('BEGIN IMMEDIATE')
    try:
        fresh = conn.execute('SELECT * FROM assessments WHERE id=?', (assessment['id'],)).fetchone()
        if fresh['status'] == 'in_progress' and _time_budget_state(fresh)['expired']:
            _finalize_timeout(conn, fresh, snapshot)
        conn.execute('COMMIT')
    except Exception:
        safe_rollback(conn)
        raise
    return True


# --------------------------------------------------------------------- login
class LoginPayload(BaseModel):
    model_config = ConfigDict(extra='forbid')

    code: str = Field(pattern=r'^[0-9]{6}$')


@router.post('/api/v1/session')
def login(payload: LoginPayload, request: Request, response: Response) -> dict:
    ip = request.client.host if request.client else 'unknown'
    with db() as conn:
        if failed_login_recent(conn, ip) >= settings().login_rate_limit:
            raise rate_limited('登录尝试过于频繁，请稍后再试')
        row = lookup_by_code(conn, payload.code)
        if row is None:
            record_login_attempt(conn, ip, False)
            raise unauthorized('登录码错误，请与管理员联系')
        if row['status'] == 'voided':
            record_login_attempt(conn, ip, False)
            raise forbidden('本次测试已作废，请向管理员索取新的身份验证码')
        record_login_attempt(conn, ip, True)
        token = create_session(conn, int(row['id']))
        contestant = conn.execute(
            'SELECT * FROM contestants WHERE id=?', (row['contestant_id'],)
        ).fetchone()
        snapshot = _snapshot_for(conn, row)
        survey = _survey_state(conn, int(row['id']))
        answered = _answered_count(conn, int(row['id']))
        contestant_type = 'agent' if contestant['subject_type'] == 'agent' else 'human'
    response.set_cookie(
        'hsr_service_session', token, httponly=True, samesite='strict',
        max_age=settings().session_ttl_seconds, path='/',
    )
    return {
        'accepted': True,
        'assessment_id': int(row['id']),
        'contestant_type': contestant_type,
        'status': _effective_status(row, survey['complete']),
        'question_count': snapshot.question_count,
        'answered_count': answered,
        'survey_required': bool(row['survey_required']),
        'survey': survey,
        'time_budget': _time_budget_state(row),
        'credential': {'type': 'bearer', 'token': token, 'expires_in': settings().session_ttl_seconds},
        'task_document': {
            'version': row['task_document_version'],
            'url': f'{_base_url(request)}/task',
        },
    }


@router.get('/api/v1/session')
def session_status(request: Request) -> dict:
    with db() as conn:
        context = contestant_context(conn, request)
        assessment = context.assessment
        snapshot = _snapshot_for(conn, assessment)
        if _expire_if_needed(conn, assessment, snapshot):
            assessment = conn.execute('SELECT * FROM assessments WHERE id=?', (assessment['id'],)).fetchone()
        survey = _survey_state(conn, assessment['id'])
        answered = _answered_count(conn, assessment['id'])
        return {
            'assessment_id': assessment['id'],
            'state_generation': (conn.execute("SELECT value FROM system_state WHERE key='reset_generation'").fetchone() or ['0'])[0],
            'contestant_type': 'agent' if context.is_agent else 'human',
            'status': _effective_status(assessment, survey['complete']),
            'question_count': snapshot.question_count,
            'answered_count': answered,
            'survey_required': bool(assessment['survey_required']),
            'survey': survey,
            'time_budget': _time_budget_state(assessment),
            'task_document': {
                'version': assessment['task_document_version'],
                'url': f'{_base_url(request)}/task',
            },
        }


# -------------------------------------------------------------------- survey
class SurveyPayload(BaseModel):
    model_config = ConfigDict(extra='forbid')

    active_days_range: ActiveDaysRange
    familiarity: int = Field(ge=1, le=5)


@router.put('/api/v1/survey')
def submit_survey(payload: SurveyPayload, request: Request) -> dict:
    with db() as conn:
        context = contestant_context(conn, request)
        assessment = context.assessment
        if assessment['status'] not in ('issued', 'ready'):
            raise conflict('测试开始后不能再修改问卷', 'survey_closed')
        if not assessment['survey_required']:
            raise conflict('本次测试不需要问卷', 'survey_not_required')
        conn.execute('BEGIN IMMEDIATE')
        try:
            for question_id, value in (('active_days_range', payload.active_days_range),
                                       ('familiarity', str(payload.familiarity))):
                conn.execute(
                    'INSERT INTO surveys(assessment_id,question_id,value,survey_version,submitted_at) '
                    'VALUES (?,?,?,?,?) ON CONFLICT(assessment_id,question_id) DO UPDATE SET '
                    'value=excluded.value, submitted_at=excluded.submitted_at',
                    (assessment['id'], question_id, value, SURVEY_VERSION, now_iso()),
                )
            conn.execute("UPDATE assessments SET status='ready' WHERE id=? AND status='issued'",
                         (assessment['id'],))
            bump_revision(conn)
            conn.execute('COMMIT')
        except Exception:
            safe_rollback(conn)
            raise
        survey = _survey_state(conn, assessment['id'])
    return {'accepted': True, 'survey': survey}


# --------------------------------------------------------------------- start
@router.post('/api/v1/start')
def start(request: Request) -> Response:
    with db() as conn:
        context = contestant_context(conn, request)
        assessment = context.assessment
        snapshot = _snapshot_for(conn, assessment)
        survey = _survey_state(conn, assessment['id'])
        if assessment['survey_required'] and not survey['complete']:
            raise conflict('请先完成问卷', 'survey_required')
        if assessment['status'] in ('completed', 'voided'):
            raise conflict('本次测试已经结束', 'assessment_closed')

        conn.execute('BEGIN IMMEDIATE')
        try:
            fresh = conn.execute('SELECT * FROM assessments WHERE id=?', (assessment['id'],)).fetchone()
            if fresh['status'] in ('completed', 'voided'):
                raise conflict('本次测试已经结束', 'assessment_closed')
            if _time_budget_state(fresh)['expired']:
                _finalize_timeout(conn, fresh, snapshot)
                conn.execute('COMMIT')
                raise timeout('已超过本场时长预算')
            if fresh['status'] in ('issued', 'ready') and fresh['test_started_at'] is None:
                conn.execute(
                    "UPDATE assessments SET status='in_progress', test_started_at=? WHERE id=?",
                    (now_iso(), assessment['id']),
                )
                record_event('test_started_at', assessment_id=assessment['id'], conn=conn)
                bump_revision(conn)
                fresh = conn.execute('SELECT * FROM assessments WHERE id=?', (assessment['id'],)).fetchone()
            answered = _answered_count(conn, assessment['id'])
            question = _current_question(snapshot, answered)
            if question is None:
                conn.execute('COMMIT')
                return _json_response({'accepted': True, 'done': True, 'status': 'completed',
                                       'receipt': _receipt_for(conn, assessment['id'])})
            delivery_id = _ensure_delivery(conn, fresh, question, answered + 1)
            conn.execute('COMMIT')
        except Exception:
            safe_rollback(conn)
            raise

        payload = _question_payload(conn, fresh, snapshot, question, delivery_id, answered + 1,
                                    _base_url(request))
        return _send_question(conn, fresh, question, delivery_id, payload)


def _receipt_for(conn: sqlite3.Connection, assessment_id: int) -> dict:
    row = conn.execute(
        'SELECT submission_id, question_id, answer_received_at FROM answers WHERE assessment_id=? '
        'ORDER BY question_order DESC LIMIT 1',
        (assessment_id,),
    ).fetchone()
    if not row:
        return {}
    return {
        'submission_id': row['submission_id'],
        'question_id': row['question_id'],
        'answer_received_at': row['answer_received_at'],
    }


def _send_question(conn, assessment, question, delivery_id, payload) -> Response:
    """Return the question and stamp the send moments around it."""
    record_event('question_send_started_at', assessment_id=assessment['id'],
                 question_id=question['id'], delivery_id=delivery_id)
    body = json.dumps(payload, ensure_ascii=False).encode('utf-8')
    conn.execute(
        'UPDATE question_deliveries SET first_send_started_at=COALESCE(first_send_started_at,?) '
        'WHERE assessment_id=? AND question_id=?',
        (now_iso(), assessment['id'], question['id']),
    )
    record_event('question_send_finished_at', assessment_id=assessment['id'],
                 question_id=question['id'], delivery_id=delivery_id, bytes_sent=len(body))
    conn.execute(
        'UPDATE question_deliveries SET first_send_finished_at=COALESCE(first_send_finished_at,?) '
        'WHERE assessment_id=? AND question_id=?',
        (now_iso(), assessment['id'], question['id']),
    )
    return Response(content=body, media_type='application/json',
                    headers={'Cache-Control': 'no-store'})


def _json_response(payload: dict, status_code: int = 200) -> Response:
    return Response(content=json.dumps(payload, ensure_ascii=False).encode('utf-8'),
                    status_code=status_code, media_type='application/json',
                    headers={'Cache-Control': 'no-store'})


# ------------------------------------------------------------------- current
@router.get('/api/v1/current')
def current(request: Request) -> Response:
    with db() as conn:
        context = contestant_context(conn, request)
        assessment = context.assessment
        snapshot = _snapshot_for(conn, assessment)
        if assessment['status'] == 'completed':
            return _json_response({'done': True, 'status': 'completed',
                                   'receipt': _receipt_for(conn, assessment['id'])})
        _require_started(assessment)
        if _expire_if_needed(conn, assessment, snapshot):
            raise timeout('已超过本场时长预算')
        answered = _answered_count(conn, assessment['id'])
        question = _current_question(snapshot, answered)
        if question is None:
            return _json_response({'done': True, 'status': assessment['status'],
                                   'receipt': _receipt_for(conn, assessment['id'])})
        delivery = conn.execute(
            'SELECT delivery_id FROM question_deliveries WHERE assessment_id=? AND question_id=?',
            (assessment['id'], question['id']),
        ).fetchone()
        delivery_id = delivery['delivery_id'] if delivery else _ensure_delivery(
            conn, assessment, question, answered + 1)
        payload = _question_payload(conn, assessment, snapshot, question, delivery_id,
                                    answered + 1, _base_url(request))
        return _send_question(conn, assessment, question, delivery_id, payload)


# -------------------------------------------------------------------- assets
@router.get('/api/v1/assets/{asset_id}')
def get_asset(asset_id: str, request: Request) -> Response:
    with db() as conn:
        context = contestant_context(conn, request)
        assessment = context.assessment
        snapshot = _snapshot_for(conn, assessment)
        _require_started(assessment)
        if _expire_if_needed(conn, assessment, snapshot):
            raise timeout('已超过本场时长预算')
        answered = _answered_count(conn, assessment['id'])
        question = None if assessment['status'] == 'completed' else _current_question(snapshot, answered)
        allowed = set(snapshot.asset_ids(question)) if question else set()
        if question is None:
            # A finished test may still replay the media it already received.
            allowed = {
                row['asset_id'] for row in conn.execute(
                    'SELECT DISTINCT asset_id FROM asset_transfers WHERE assessment_id=?',
                    (assessment['id'],),
                )
            }
        if asset_id not in allowed:
            raise forbidden('该素材不属于当前题目')
        store = media_store()
        meta = store.get(asset_id)
        if not meta:
            raise ApiError(404, 'not_found', '素材不存在')
        path = store.path_for(asset_id)
        delivery_id = None
        if question:
            row = conn.execute(
                'SELECT delivery_id FROM question_deliveries WHERE assessment_id=? AND question_id=?',
                (assessment['id'], question['id']),
            ).fetchone()
            delivery_id = row['delivery_id'] if row else None
    required = [
        (asset, store.get(asset)['bytes'])
        for asset in (snapshot.asset_ids(question) if question else [])
        if store.get(asset)
    ]
    return asset_response(
        asset_id=asset_id,
        assessment_id=assessment['id'],
        question_id=question['id'] if question else None,
        delivery_id=delivery_id,
        request_id=request.scope.get('state', {}).get('request_id', ''),
        range_header=request.headers.get('range'),
        path=path,
        meta=meta,
        required_assets=required,
    )


# ------------------------------------------------------------- ready report
class ReadyPayload(BaseModel):
    model_config = ConfigDict(extra='forbid')

    question_id: str
    delivery_id: str | None = None


@router.post('/api/v1/events/materials-ready')
def materials_ready(payload: ReadyPayload, request: Request) -> dict:
    with db() as conn:
        context = contestant_context(conn, request)
        assessment = context.assessment
        _require_started(assessment)
        if _expire_if_needed(conn, assessment, _snapshot_for(conn, assessment)):
            raise timeout('已超过本场时长预算')
        if assessment['status'] == 'completed':
            raise conflict('本次测试已经结束', 'assessment_closed')
        row = conn.execute(
            'SELECT delivery_id FROM question_deliveries WHERE assessment_id=? AND question_id=?',
            (assessment['id'], payload.question_id),
        ).fetchone()
        if not row:
            raise conflict('该题目尚未发放', 'question_not_issued')
        if payload.delivery_id and payload.delivery_id != row['delivery_id']:
            raise conflict('发放标识不匹配', 'delivery_mismatch')
        moment = now_iso()
        conn.execute(
            'UPDATE question_deliveries SET client_ready_at=COALESCE(client_ready_at,?) '
            'WHERE assessment_id=? AND question_id=?',
            (moment, assessment['id'], payload.question_id),
        )
        record_event('client_materials_ready_received_at', assessment_id=assessment['id'],
                     question_id=payload.question_id, delivery_id=row['delivery_id'])
    return {'accepted': True, 'received_at': moment}


# ------------------------------------------------------------------- answers
class AnnotationPayload(BaseModel):
    model_config = ConfigDict(extra='forbid')

    character_id: str = Field(min_length=1, max_length=100)
    x: float = Field(ge=0, allow_inf_nan=False)
    y: float = Field(ge=0, allow_inf_nan=False)
    reason: str = Field(default='', max_length=10000)


class AnswerPayload(BaseModel):
    model_config = ConfigDict(extra='forbid')

    question_id: str
    submission_id: str = Field(min_length=1, max_length=100)
    delivery_id: str | None = None
    selected_options: list[str] = Field(default_factory=list, max_length=4)
    reasons: dict[str, str] = Field(default_factory=dict)
    annotations: list[AnnotationPayload] = Field(default_factory=list, max_length=100)


def _validate_answer(
    question: dict,
    payload: AnswerPayload,
    snapshot: BankSnapshot,
    is_agent: bool,
) -> tuple[list[str], list[dict], bool]:
    """Return (selected options, annotations, abandoned) or raise."""
    fields = payload.model_fields_set
    if question['type'] == 'point_identification':
        if 'selected_options' in fields:
            raise unprocessable('标注题请提交 annotations，不要提交选择题字段')
        if 'annotations' not in fields:
            raise unprocessable('标注题必须提交 annotations 字段')
        annotations = [a.model_dump() for a in payload.annotations]
        roster = {c['id'] for c in snapshot.public['characters']['characters']}
        if len(annotations) > question['score']:
            raise unprocessable(f'本题最多标注 {question["score"]} 个目标')
        for annotation in annotations:
            if annotation['character_id'] not in roster:
                raise unprocessable('角色不在本题给出的名单中')
            if annotation['x'] > question['image_width'] or annotation['y'] > question['image_height']:
                raise unprocessable('标注坐标超出原图范围')
        needs_reason = question['id'] in QUESTIONS_REQUIRING_ANNOTATION_REASONS
        if is_agent and needs_reason:
            if any(not a['reason'].strip() for a in annotations):
                raise unprocessable('本题每个标注都需要给出识别与定位依据')
        if not is_agent and any(a['reason'] for a in annotations):
            raise unprocessable('人类测试者无需填写理由')
        if payload.reasons:
            raise unprocessable('标注题请把依据写在每个标注的 reason 中')
        return [], annotations, not annotations

    if 'annotations' in fields:
        raise unprocessable('选择题不能提交标注字段')
    if 'selected_options' not in fields:
        raise unprocessable('必须提交 selected_options 字段')
    selected = list(dict.fromkeys(payload.selected_options))
    if any(option not in {'A', 'B', 'C', 'D'} for option in selected):
        raise unprocessable('选项只能是 A、B、C 或 D')
    if question['type'] == 'single_choice' and len(selected) > 1:
        raise unprocessable('单选题只能选择一项；不能提交多项')
    if question['type'] == 'multi_choice' and len(selected) > question.get('max_selections', 4):
        raise unprocessable(f"本题最多选择 {question.get('max_selections', 4)} 个选项")
    if is_agent:
        if not selected:
            # An explicit abandon picks no option, so no option reason applies.
            if payload.reasons:
                raise unprocessable('明确放弃时不提交选项理由')
        else:
            if set(payload.reasons) != {'A', 'B', 'C', 'D'}:
                raise unprocessable('请为 A、B、C、D 每个选项给出简短理由')
            if any(not value.strip() or len(value) > 10000 for value in payload.reasons.values()):
                raise unprocessable('理由不能为空且不能超过 10000 字符')
    elif payload.reasons:
        raise unprocessable('人类测试者无需填写理由')
    return selected, [], not selected


@router.post('/api/v1/answers')
def submit_answer(payload: AnswerPayload, request: Request) -> Response:
    received_at = request.scope.get('state', {}).get('answer_received_at') or _iso_now()
    with db() as conn:
        context = contestant_context(conn, request)
        assessment = context.assessment
        snapshot = _snapshot_for(conn, assessment)
        key = snapshot.private['key']

        conn.execute('BEGIN IMMEDIATE')
        try:
            fresh = conn.execute('SELECT * FROM assessments WHERE id=?', (assessment['id'],)).fetchone()
            if fresh['status'] == 'voided':
                raise forbidden('本次测试已作废')
            if fresh['status'] == 'completed':
                # A late retry of an already finished test returns the receipt.
                existing = conn.execute(
                    'SELECT * FROM answers WHERE assessment_id=? AND submission_id=?',
                    (fresh['id'], payload.submission_id),
                ).fetchone()
                conn.execute('COMMIT')
                if existing:
                    if existing['payload_digest'] != payload_digest(payload.model_dump(exclude_none=False)):
                        raise conflict('同一 submission_id 的内容与已接收的提交不一致', 'submission_conflict')
                    return _replay(conn, fresh, snapshot, existing, request)
                raise conflict('本次测试已经结束', 'assessment_closed')

            _require_started(fresh)
            existing = conn.execute(
                'SELECT * FROM answers WHERE assessment_id=? AND submission_id=?',
                (fresh['id'], payload.submission_id),
            ).fetchone()
            if existing:
                digest = payload_digest(payload.model_dump(exclude_none=False))
                if existing['payload_digest'] != digest or existing['question_id'] != payload.question_id:
                    raise conflict('同一 submission_id 的内容与已接收的提交不一致', 'submission_conflict')
                if _time_budget_state(fresh)['expired']:
                    _finalize_timeout(conn, fresh, snapshot)
                    fresh = conn.execute('SELECT * FROM assessments WHERE id=?', (fresh['id'],)).fetchone()
                conn.execute('COMMIT')
                return _replay(conn, fresh, snapshot, existing, request)

            budget = _time_budget_state(fresh)
            if budget['expired']:
                _finalize_timeout(conn, fresh, snapshot)
                conn.execute('COMMIT')
                raise timeout('已超过本场 60 分钟时长预算，不再接受新的提交')

            answered = _answered_count(conn, fresh['id'])
            question = _current_question(snapshot, answered)
            if question is None:
                safe_rollback(conn)
                raise conflict('本题已经提交', 'question_already_answered')
            if payload.question_id != question['id']:
                safe_rollback(conn)
                raise conflict('只能提交当前题目，不能回退或重复提交', 'question_not_current')

            delivery = conn.execute(
                'SELECT * FROM question_deliveries WHERE assessment_id=? AND question_id=?',
                (fresh['id'], question['id']),
            ).fetchone()
            if not delivery:
                safe_rollback(conn)
                raise conflict('该题目尚未发放', 'question_not_issued')
            if payload.delivery_id and payload.delivery_id != delivery['delivery_id']:
                safe_rollback(conn)
                raise conflict('发放标识不匹配', 'delivery_mismatch')

            selected, annotations, abandoned = _validate_answer(
                question, payload, snapshot, context.is_agent)

            if question['type'] == 'point_identification':
                score = float(evaluate_points(key[question['id']]['targets'], annotations)['score'])
            else:
                score = score_choice(question, key[question['id']]['correct_options'], selected)

            committed_at = now_iso()
            delivery_complete = delivery['delivery_complete_at']
            conn.execute(
                '''INSERT INTO answers(assessment_id,question_id,submission_id,question_order,payload_digest,
                     answer_json,reasons_json,annotations_json,selected_options,score,score_max,
                     answer_received_at,answer_committed_at,delivery_complete_at,delivery_incomplete,abandoned)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                (
                    fresh['id'], question['id'], payload.submission_id, answered + 1,
                    payload_digest(payload.model_dump(exclude_none=False)),
                    json.dumps({'selected_options': selected, 'annotations': annotations},
                               ensure_ascii=False),
                    json.dumps(payload.reasons, ensure_ascii=False),
                    json.dumps(annotations, ensure_ascii=False),
                    ','.join(selected),
                    score, float(question['score']),
                    received_at, committed_at, delivery_complete,
                    0 if delivery_complete else 1,
                    int(abandoned),
                ),
            )
            record_event('answer_received_at', assessment_id=fresh['id'], question_id=question['id'],
                         delivery_id=delivery['delivery_id'], conn=conn)
            record_event('answer_committed_at', assessment_id=fresh['id'], question_id=question['id'],
                         delivery_id=delivery['delivery_id'], conn=conn)
            # The admin console polls this counter; a committed answer changes the table.
            bump_revision(conn)

            durations = derive_durations(conn, fresh['id'], question['id'])
            conn.execute(
                '''UPDATE answers SET duration_with_delivery_ms=?, duration_post_delivery_ms=?,
                     duration_client_ready_ms=? WHERE assessment_id=? AND question_id=?''',
                (durations.get('duration_with_delivery_ms'), durations.get('duration_post_delivery_ms'),
                 durations.get('duration_client_ready_ms'), fresh['id'], question['id']),
            )

            done = answered + 1 >= snapshot.question_count
            next_payload = None
            if done:
                totals = conn.execute(
                    'SELECT SUM(score), SUM(score_max) FROM answers WHERE assessment_id=?', (fresh['id'],)
                ).fetchone()
                conn.execute(
                    "UPDATE assessments SET status='completed', completed_at=?, total_score=?, total_score_max=? "
                    'WHERE id=?',
                    (now_iso(), totals[0], totals[1], fresh['id']),
                )
                record_event('test_completed_at', assessment_id=fresh['id'], conn=conn)
                rotate_after_completion(conn, snapshot, fresh['id'])
                bump_revision(conn)
            else:
                next_question = snapshot.public['questions'][answered + 1]
                next_delivery = _ensure_delivery(conn, fresh, next_question, answered + 2)
                next_payload = _question_payload(
                    conn, fresh, snapshot, next_question, next_delivery, answered + 2, _base_url(request))
            conn.execute('COMMIT')
        except ApiError:
            raise
        except Exception:
            safe_rollback(conn)
            raise

        response = {
            'accepted': True,
            'receipt': {
                'submission_id': payload.submission_id,
                'question_id': question['id'],
                'answer_received_at': received_at,
            },
            'done': done,
            'next_question': next_payload,
        }
        if next_payload is not None:
            return _send_question(conn, fresh, next_question, next_delivery, response)
        return _json_response(response)


def _replay(conn, assessment, snapshot, existing, request) -> Response:
    """Return the original receipt and the next question for a retry."""
    answered = _answered_count(conn, assessment['id'])
    next_payload = None
    done = assessment['status'] == 'completed' or answered >= snapshot.question_count
    if not done:
        next_question = snapshot.public['questions'][answered]
        delivery = conn.execute(
            'SELECT delivery_id FROM question_deliveries WHERE assessment_id=? AND question_id=?',
            (assessment['id'], next_question['id']),
        ).fetchone()
        if not delivery:
            delivery_id = _ensure_delivery(conn, assessment, next_question, answered + 1)
        else:
            delivery_id = delivery['delivery_id']
        next_payload = _question_payload(conn, assessment, snapshot, next_question, delivery_id,
                                         answered + 1, _base_url(request))
    response = {
        'accepted': True,
        'receipt': {
            'submission_id': existing['submission_id'],
            'question_id': existing['question_id'],
            'answer_received_at': existing['answer_received_at'],
        },
        'done': done,
        'next_question': next_payload,
    }
    if next_payload is not None:
        return _send_question(conn, assessment, next_question, delivery_id, response)
    return _json_response(response)


def _finalize_timeout(conn, assessment, snapshot) -> None:
    """End the test because the time budget ran out; keep what was submitted."""
    totals = conn.execute(
        'SELECT SUM(score), SUM(score_max) FROM answers WHERE assessment_id=?', (assessment['id'],)
    ).fetchone()
    moment = now_iso()
    conn.execute(
        "UPDATE assessments SET status='completed', completed_at=?, test_timeout_at=?, total_score=?, "
        'total_score_max=? WHERE id=?',
        (moment, moment, totals[0] or 0, snapshot.score_max, assessment['id']),
    )
    record_event('test_timeout_at', assessment_id=assessment['id'], conn=conn)
    record_event('test_completed_at', assessment_id=assessment['id'], conn=conn)
    rotate_after_completion(conn, snapshot, assessment['id'])
    bump_revision(conn)
