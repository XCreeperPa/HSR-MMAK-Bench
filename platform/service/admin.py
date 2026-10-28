"""Admin console API.

The console is read only by design. The only two write operations are a
management marker on a test or a contestant, and voiding the current attempt
while issuing a fresh code. There is no endpoint that creates a contestant,
edits an answer, changes a score or rewrites a stored result.

The data table shows one row per test. Its summary rows follow the official
"equal weight per contestant" rule by default, so a contestant who sits the
test five times does not outweigh a contestant who sits it once.
"""
from __future__ import annotations

import csv
import io
import json
import sqlite3
from datetime import datetime, timezone

from fastapi import APIRouter, Request, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from .bank import load_snapshot
from .config import settings
from .db import bump_revision, now_iso, read_revision
from .errors import ApiError, conflict, forbidden, unauthorized, unprocessable
from .lifecycle import void_and_reissue
from .media import media_store
from .session import admin_context, create_session
from .xlsx import build_workbook
from .survey import activity_label

router = APIRouter(prefix='/api/v1/admin')

ADMIN_COOKIE = 'hsr_service_admin'
STATISTICS_MODES = ('by_contestant', 'by_test')


# --------------------------------------------------------------------- auth
class AdminLogin(BaseModel):
    model_config = ConfigDict(extra='forbid')

    username: str
    password: str


@router.post('/auth/login')
def admin_login(payload: AdminLogin, response: Response) -> dict:
    import hmac

    cfg = settings()
    ok = hmac.compare_digest(payload.username, cfg.admin_username) and hmac.compare_digest(
        payload.password, cfg.admin_password)
    if not ok:
        raise unauthorized('管理员认证失败')
    with request_connection() as conn:
        token = create_session(conn, None, role='admin')
    response.set_cookie(ADMIN_COOKIE, token, httponly=True, samesite='strict',
                        max_age=settings().session_ttl_seconds, path='/')
    return {'accepted': True}


def request_connection():
    """Open a database connection that is always closed again."""
    from .db import db

    return db()


@router.get('/session')
def admin_session(request: Request) -> dict:
    with request_connection() as conn:
        admin_context(conn, request)
        return {'accepted': True, 'admin': settings().admin_username}


# -------------------------------------------------------------------- codes
@router.get('/codes')
def current_codes(request: Request) -> dict:
    """One row per official contestant with the only code that currently works."""
    with request_connection() as conn:
        admin_context(conn, request)
        snapshot = load_snapshot(conn, _current_bank_version(conn))
        rows = conn.execute(
            '''SELECT a.*, c.roster_key, c.subject_type, c.display_name, c.reasoning_effort,
                      c.agent_host, c.invocation_route, c.include_in_statistics AS contestant_in_statistics
               FROM assessments a JOIN contestants c ON c.id=a.contestant_id
               WHERE a.status IN ('issued','ready','in_progress')
               ORDER BY c.subject_type, c.id'''
        ).fetchall()
        items = []
        for row in rows:
            items.append({
                'assessment_id': row['id'],
                'contestant_id': row['contestant_id'],
                'contestant_code': f"P{row['contestant_id']:04d}",
                'roster_key': row['roster_key'],
                'contestant_type': row['subject_type'],
                'display_name': row['display_name'],
                'reasoning_effort': row['reasoning_effort'],
                'agent_host': row['agent_host'],
                'invocation_route': row['invocation_route'],
                'status': row['status'],
                'code_status': row['code_status'],
                'code': row['code'],
                'test_number': _test_number(conn, row['contestant_id'], row['id']),
                'answered_count': _answered_count(conn, row['id']),
                'question_count': snapshot.question_count,
                'created_at': row['created_at'],
                'started_at': row['test_started_at'],
            })
        return {
            'revision': read_revision(conn),
            'entry_url': (settings().public_base_url or str(request.base_url).rstrip('/')) + '/',
            'task_document_url': (settings().public_base_url or str(request.base_url).rstrip('/')) + '/task',
            'time_budget_minutes': settings().time_budget_seconds // 60,
            'digits': 6,
            'question_count': snapshot.question_count,
            'assessments': items,
        }


def _test_number(conn: sqlite3.Connection, contestant_id: int, assessment_id: int) -> int:
    row = conn.execute(
        'SELECT COUNT(*) FROM assessments WHERE contestant_id=? AND id<=?', (contestant_id, assessment_id)
    ).fetchone()
    return int(row[0])


def _answered_count(conn: sqlite3.Connection, assessment_id: int) -> int:
    return int(conn.execute(
        'SELECT COUNT(*) FROM answers WHERE assessment_id=?', (assessment_id,)
    ).fetchone()[0])


def _current_bank_version(conn: sqlite3.Connection) -> str:
    row = conn.execute('SELECT version FROM bank_versions ORDER BY created_at DESC LIMIT 1').fetchone()
    if not row:
        raise ApiError(500, 'no_bank', '题库尚未装载')
    return row['version']


# ------------------------------------------------------------------- matrix
class MatrixFilters(BaseModel):
    model_config = ConfigDict(extra='forbid')

    contestant_type: str | None = None
    contestant_id: int | None = None
    include_non_statistics: bool = False
    mode: str = 'by_contestant'
    bank_version: str | None = None


def _module_of(question: dict) -> int:
    return int(question.get('module') or 0)


def build_matrix(conn: sqlite3.Connection, filters: MatrixFilters) -> dict:
    snapshot = load_snapshot(conn, filters.bank_version or _current_bank_version(conn))
    question_ids = snapshot.public['question_order']
    modules = snapshot.public.get('modules', [])

    assessments = conn.execute(
        '''SELECT a.*, c.roster_key, c.subject_type, c.display_name, c.reasoning_effort,
                  c.agent_host, c.invocation_route, c.include_in_statistics AS contestant_in_statistics
           FROM assessments a JOIN contestants c ON c.id=a.contestant_id
           ORDER BY c.subject_type, c.id, a.id'''
    ).fetchall()

    answers: dict[tuple[int, str], sqlite3.Row] = {}
    for row in conn.execute(
        'SELECT * FROM answers ORDER BY assessment_id, question_order'
    ).fetchall():
        answers[(row['assessment_id'], row['question_id'])] = row

    surveys: dict[int, dict] = {}
    survey_versions: dict[int, str] = {}
    for row in conn.execute('SELECT * FROM surveys').fetchall():
        surveys.setdefault(row['assessment_id'], {})[row['question_id']] = row['value']
        if row['question_id'] == 'familiarity':
            survey_versions[row['assessment_id']] = row['survey_version']

    tag_rows = conn.execute('SELECT * FROM assessment_tags ORDER BY id').fetchall()
    tags_by_assessment: dict[int, list[dict]] = {}
    tags_by_contestant: dict[int, list[dict]] = {}
    for row in tag_rows:
        target = tags_by_assessment if row['assessment_id'] is not None else tags_by_contestant
        target_id = row['assessment_id'] if row['assessment_id'] is not None else row['contestant_id']
        target.setdefault(target_id, []).append({
            'tag': row['tag'],
            'note': row['note'],
            'exclude_from_statistics': bool(row['exclude_from_statistics']),
            'created_at': row['created_at'],
            'created_by': row['created_by'],
        })

    recovered_timings = {(r['assessment_id'], r['question_id']) for r in conn.execute(
        "SELECT assessment_id,question_id FROM events WHERE event_type='timing_recovered_from_issued_at'")}
    rows: list[dict] = []
    for assessment in assessments:
        if assessment['bank_version'] != snapshot.version:
            continue
        if filters.contestant_type and assessment['subject_type'] != filters.contestant_type:
            continue
        if filters.contestant_id and assessment['contestant_id'] != filters.contestant_id:
            continue

        per_question: dict[str, dict] = {}
        module_scores: dict[str, float] = {}
        total = 0.0
        total_max = 0.0
        total_duration = 0
        anomalies: list[str] = []
        for question in snapshot.public['questions']:
            answer = answers.get((assessment['id'], question['id']))
            if answer is None:
                per_question[question['id']] = {
                    'score': None, 'score_max': question['score'],
                    'duration_with_delivery_ms': None, 'duration_post_delivery_ms': None,
                    'duration_client_ready_ms': None, 'answer_summary': None,
                    'answer_received_at': None, 'delivery_incomplete': None,
                    'abandoned': None, 'delivery_complete_at': None,
                }
                continue
            summary = _answer_summary(question, answer)
            per_question[question['id']] = {
                'score': answer['score'],
                'score_max': answer['score_max'],
                'duration_with_delivery_ms': answer['duration_with_delivery_ms'],
                'duration_post_delivery_ms': answer['duration_post_delivery_ms'],
                'duration_client_ready_ms': answer['duration_client_ready_ms'],
                'timing_source': 'issued_at_recovery' if (assessment['id'], question['id']) in recovered_timings else 'first_send_started_at',
                'answer_summary': summary,
                'answer_received_at': answer['answer_received_at'],
                'delivery_complete_at': answer['delivery_complete_at'],
                'delivery_incomplete': bool(answer['delivery_incomplete']),
                'abandoned': bool(answer['abandoned']),
            }
            total += answer['score']
            total_max += answer['score_max']
            total_duration += answer['duration_with_delivery_ms'] or 0
            module = str(_module_of(question))
            module_scores[module] = module_scores.get(module, 0.0) + answer['score']
            if answer['delivery_incomplete']:
                anomalies.append(f"{question['id']}:交付不完整")
            if answer['abandoned']:
                anomalies.append(f"{question['id']}:明确放弃")
            if answer['duration_with_delivery_ms'] is None:
                anomalies.append(f"{question['id']}:计时缺失")

        if assessment['status'] == 'voided':
            anomalies.append('已作废')
        if assessment['test_timeout_at']:
            anomalies.append('超时结束')
        answered_here = _answered_count(conn, assessment['id'])
        if assessment['status'] == 'completed' and answered_here < snapshot.question_count:
            anomalies.append('未完成全部题目')

        survey = surveys.get(assessment['id'], {})
        tags = (tags_by_assessment.get(assessment['id'], [])
                + tags_by_contestant.get(assessment['contestant_id'], []))
        excluded = any(t['exclude_from_statistics'] for t in tags)
        included = bool(
            assessment['include_in_statistics']
            and assessment['contestant_in_statistics']
            and assessment['status'] == 'completed'
            and not excluded
            and assessment['bank_version'] == snapshot.version
        )
        rows.append({
            'assessment_id': assessment['id'],
            'contestant_id': assessment['contestant_id'],
            'roster_key': assessment['roster_key'],
            'contestant_code': f"P{assessment['contestant_id']:04d}",
            'contestant_type': assessment['subject_type'],
            'display_name': assessment['display_name'],
            'reasoning_effort': assessment['reasoning_effort'],
            'agent_host': assessment['agent_host'],
            'invocation_route': assessment['invocation_route'],
            'test_number': _test_number(conn, assessment['contestant_id'], assessment['id']),
            'code': assessment['code'],
            'status': assessment['status'],
            'code_status': assessment['code_status'],
            'answered_count': _answered_count(conn, assessment['id']),
            'question_count': snapshot.question_count,
            'bank_version': assessment['bank_version'],
            'scoring_rules_version': assessment['scoring_rules_version'],
            'timing_policy_version': assessment['timing_policy_version'],
            'survey': {
                'active_days': survey.get('active_days'),
                'active_days_range': survey.get('active_days_range'),
                'active_days_label': activity_label(survey),
                'familiarity': survey.get('familiarity'),
                'survey_version': survey_versions.get(assessment['id']),
            },
            'questions': per_question,
            'module_scores': module_scores,
            'module_max': {str(m['id']): m['score_max'] for m in modules},
            'total_score': total if (assessment['status'] == 'completed' or answered_here) else None,
            'current_score': total,
            'total_score_max': snapshot.score_max,
            'score_rate': (total / snapshot.score_max) if (snapshot.score_max and answered_here) else None,
            'total_duration_ms': total_duration if answered_here else None,
            'started_at': assessment['test_started_at'],
            'completed_at': assessment['completed_at'],
            'timeout_at': assessment['test_timeout_at'],
            'include_in_statistics': included,
            'tags': tags,
            'anomalies': anomalies,
        })

    if not filters.include_non_statistics:
        rows = [r for r in rows if r['include_in_statistics'] or r['status'] != 'completed']
    # 每次测试一行，按选手再按测试次排开。两种口径下顺序都一样，
    # 管理员看「全部记录」和看「统计样本」时看到的行序才对得上。
    rows.sort(key=lambda r: (r['contestant_type'], r['contestant_id'], r['test_number']))

    summaries = _summarize(rows, snapshot, filters.mode)
    return {
        'revision': read_revision(conn),
        'generated_at': now_iso(),
        'bank_version': snapshot.version,
        'bank_versions': [dict(r) for r in conn.execute(
            'SELECT version,created_at FROM bank_versions ORDER BY created_at DESC')],
        'mode': filters.mode,
        'question_order': question_ids,
        'question_modules': {q['id']: _module_of_question(snapshot, q)
                             for q in snapshot.public['questions']},
        'question_meta': {
            q['id']: {
                'id': q['id'],
                'module': _module_of_question(snapshot, q),
                'module_name': _module_name(snapshot, _module_of_question(snapshot, q)),
                'type': q['type'],
                'score': q['score'],
                'prompt': q['prompt'],
            }
            for q in snapshot.public['questions']
        },
        'modules': modules,
        'rows': rows,
        'summaries': summaries,
        'filters': filters.model_dump(),
    }


def _answer_summary(question: dict, answer: sqlite3.Row) -> str:
    if question['type'] == 'point_identification':
        annotations = json.loads(answer['annotations_json'] or '[]')
        return f"{len(annotations)}个标注 / {question['score']}分"
    selected = answer['selected_options'] or ''
    if answer['abandoned']:
        return '明确放弃'
    return selected or '—'


def _summarize(rows: list[dict], snapshot, mode: str) -> dict:
    """Aggregate the matrix under the documented statistics rules."""
    question_ids = snapshot.public['question_order']
    included = [r for r in rows if r['include_in_statistics']]
    by_contestant: dict[int, list[dict]] = {}
    for row in included:
        by_contestant.setdefault(row['contestant_id'], []).append(row)

    def mean(values: list[float]) -> float | None:
        return round(sum(values) / len(values), 4) if values else None

    def per_contestant_mean(group: list[dict]) -> dict:
        # 每题有多少个纳入样本真的答了——「按题目看」的透视表要靠它算作答率。
        answered_by_question = {
            qid: len([r for r in group if r['questions'][qid]['score'] is not None])
            for qid in question_ids
        }
        if mode == 'by_test':
            totals = [r['current_score'] for r in group if r['total_score'] is not None]
            per_question = {
                qid: mean([r['questions'][qid]['score'] for r in group
                           if r['questions'][qid]['score'] is not None])
                for qid in question_ids
            }
            return {'mean_total': mean(totals), 'mean_by_question': per_question,
                    'answered_by_question': answered_by_question, 'tests': len(group)}
        contestant_means = []
        contestant_question_means: dict[str, list[float]] = {qid: [] for qid in question_ids}
        for contestant_rows in _group_by_contestant(group).values():
            totals = [r['current_score'] for r in contestant_rows if r['total_score'] is not None]
            contestant_means.append(mean(totals) if totals else None)
            for qid in question_ids:
                values = [r['questions'][qid]['score'] for r in contestant_rows
                          if r['questions'][qid]['score'] is not None]
                if values:
                    contestant_question_means[qid].append(mean(values))
        return {
            'mean_total': mean([v for v in contestant_means if v is not None]),
            'mean_by_question': {qid: mean(values) for qid, values in contestant_question_means.items()},
            'answered_by_question': answered_by_question,
            'contestants': len([v for v in contestant_means if v is not None]),
            'tests': len(group),
        }

    summaries: dict[str, dict] = {}
    summaries['all'] = per_contestant_mean(included)
    for kind, label in (('human', '人类'), ('agent', '智能体')):
        group = [r for r in included if r['contestant_type'] == kind]
        summaries[kind] = per_contestant_mean(group)
        summaries[kind]['label'] = label
    per_contestant: dict[str, dict] = {}
    for contestant_id, group in _group_by_contestant(included).items():
        first = group[0]
        per_contestant[str(contestant_id)] = {
            'contestant_id': contestant_id,
            'contestant_code': first['contestant_code'],
            'display_name': first['display_name'],
            'contestant_type': first['contestant_type'],
            'reasoning_effort': first['reasoning_effort'],
            'agent_host': first['agent_host'],
            'invocation_route': first['invocation_route'],
            'tests': len(group),
            **per_contestant_mean(group),
        }
    summaries['contestants'] = per_contestant
    summaries['mode'] = mode
    summaries['sample_unit'] = '选手' if mode == 'by_contestant' else '测试次'
    return summaries


def _group_by_contestant(rows: list[dict]) -> dict[int, list[dict]]:
    grouped: dict[int, list[dict]] = {}
    for row in rows:
        grouped.setdefault(row['contestant_id'], []).append(row)
    return grouped


@router.get('/matrix')
def matrix(request: Request, contestant_type: str | None = None, contestant_id: int | None = None,
           include_non_statistics: bool = False, mode: str = 'by_contestant',
           bank_version: str | None = None) -> dict:
    if mode not in STATISTICS_MODES:
        raise unprocessable(f'mode 只能是 {STATISTICS_MODES} 之一')
    filters = MatrixFilters(
        contestant_type=contestant_type,
        contestant_id=contestant_id,
        include_non_statistics=include_non_statistics,
        mode=mode,
        bank_version=bank_version,
    )
    with request_connection() as conn:
        admin_context(conn, request)
        return build_matrix(conn, filters)


@router.get('/revision')
def revision(request: Request) -> dict:
    with request_connection() as conn:
        admin_context(conn, request)
        return {'revision': read_revision(conn)}


# --------------------------------------------------------------------- void
class VoidPayload(BaseModel):
    model_config = ConfigDict(extra='forbid')

    reason: str = Field(min_length=1, max_length=500)


@router.post('/assessments/{assessment_id}/void')
def void_assessment(assessment_id: int, payload: VoidPayload, request: Request) -> dict:
    with request_connection() as conn:
        admin_context(conn, request)
        snapshot = load_snapshot(conn, _current_bank_version(conn))
        conn.execute('BEGIN IMMEDIATE')
        try:
            row = conn.execute('SELECT * FROM assessments WHERE id=?', (assessment_id,)).fetchone()
            if not row:
                raise conflict('测试不存在', 'not_found')
            if row['status'] == 'voided':
                raise conflict('该试次已经作废', 'already_voided')
            if row['status'] not in ('issued', 'ready', 'in_progress'):
                raise conflict(
                    '只能作废当前试次；已结束的测试请用管理标记排除统计', 'not_the_current_attempt')
            result = void_and_reissue(conn, snapshot, assessment_id, payload.reason, actor='admin')
            conn.execute('COMMIT')
        except Exception:
            from .db import safe_rollback

            safe_rollback(conn)
            raise
    return {'accepted': True, **result}


# --------------------------------------------------------------------- tags
class TagPayload(BaseModel):
    model_config = ConfigDict(extra='forbid')

    assessment_id: int | None = None
    contestant_id: int | None = None
    tag: str = Field(min_length=1, max_length=100)
    note: str = Field(default='', max_length=1000)
    exclude_from_statistics: bool = False


@router.post('/tags')
def add_tag(payload: TagPayload, request: Request) -> dict:
    if payload.assessment_id is None and payload.contestant_id is None:
        raise unprocessable('必须给出 assessment_id 或 contestant_id')
    with request_connection() as conn:
        admin_context(conn, request)
        from .lifecycle import add_tag as _add_tag

        if payload.assessment_id is not None:
            row = conn.execute('SELECT contestant_id FROM assessments WHERE id=?',
                               (payload.assessment_id,)).fetchone()
            if not row:
                raise conflict('测试不存在', 'not_found')
            contestant_id = int(row['contestant_id'])
        else:
            contestant_id = int(payload.contestant_id)
            if not conn.execute('SELECT 1 FROM contestants WHERE id=?', (contestant_id,)).fetchone():
                raise conflict('选手不存在', 'not_found')
        tag_id = _add_tag(
            conn,
            assessment_id=payload.assessment_id,
            contestant_id=contestant_id,
            tag=payload.tag,
            note=payload.note,
            exclude_from_statistics=payload.exclude_from_statistics,
            actor='admin',
        )
    return {'accepted': True, 'tag_id': tag_id}


# --------------------------------------------------------- annotation detail
def _annotation_payload(conn: sqlite3.Connection, assessment: sqlite3.Row,
                         question: dict) -> dict:
    """One annotation question: the standard answer polygons and the submitted points."""
    snapshot = load_snapshot(conn, assessment['bank_version'])
    key = snapshot.private['key']
    answer = conn.execute(
        'SELECT * FROM answers WHERE assessment_id=? AND question_id=?', (assessment['id'], question['id'])
    ).fetchone()
    contestant = conn.execute('SELECT * FROM contestants WHERE id=?',
                               (assessment['contestant_id'],)).fetchone()
    targets = [
        {
            'id': t['id'],
            'character_id': t['character_id'],
            'name': t.get('name'),
            'head_region': t['head_region'],
        }
        for t in key[question['id']]['targets']
    ]
    submitted = []
    if answer:
        from .scoring import evaluate_points

        raw = json.loads(answer['annotations_json'] or '[]')
        evaluation = evaluate_points(key[question['id']]['targets'], raw)
        by_index = {index: item for index, item in enumerate(evaluation['annotations'])}
        for index, item in enumerate(raw):
            judged = by_index.get(index, {})
            submitted.append({
                'character_id': item.get('character_id'),
                'x': item.get('x'),
                'y': item.get('y'),
                'reason': item.get('reason', ''),
                'score': judged.get('score', 0),
                'located': judged.get('located', False),
                'duplicate': judged.get('duplicate', False),
                'matched_target_id': judged.get('matched_target_id'),
            })
    store = media_store()
    asset_id = snapshot.asset_ids(question)[0]
    meta = store.get(asset_id)
    return {
        'question': {
            'id': question['id'],
            'order': question['order'],
            'module': question.get('module'),
            'prompt': question['prompt'],
            'score': question['score'],
            'image_width': question['image_width'],
            'image_height': question['image_height'],
        },
        'asset': {
            'asset_id': asset_id,
            'url': f'/api/v1/admin/assets/{asset_id}',
            'mime': meta['mime'] if meta else '',
            'width': meta.get('width') if meta else None,
            'height': meta.get('height') if meta else None,
        },
        'targets': targets,
        'submitted': submitted,
        'score': answer['score'] if answer else None,
        'score_max': answer['score_max'] if answer else question['score'],
        'answered': answer is not None,
    }


def _module_of_question(snapshot, question: dict) -> str:
    """The module number of a question, as the manifest numbers them."""
    number = str(question.get('module') or '')
    for entry in snapshot.public.get('modules', []):
        entry_id = str(entry.get('id', ''))
        if entry_id == number or entry_id.split('_')[-1] == number:
            return entry_id
    return number


def _module_name(snapshot, module_id: str) -> str:
    for entry in snapshot.public.get('modules', []):
        if str(entry.get('id', '')) == module_id:
            return str(entry.get('name', ''))
    return ''


@router.get('/assessments/{assessment_id}/annotations')
def annotations(assessment_id: int, request: Request) -> dict:
    """Every annotation question of the first module for one attempt, in one response.

    The single question route answers "is this one click right"; this one answers
    "how did this attempt do on the whole annotation module" without the reader
    having to type a question id four times.
    """
    with request_connection() as conn:
        admin_context(conn, request)
        assessment = conn.execute('SELECT * FROM assessments WHERE id=?', (assessment_id,)).fetchone()
        if not assessment:
            raise conflict('测试不存在', 'not_found')
        snapshot = load_snapshot(conn, assessment['bank_version'])
        points = [q for q in snapshot.public['questions'] if q['type'] == 'point_identification']
        if not points:
            raise conflict('该题库没有标注题', 'no_point_question')
        module_id = _module_of_question(snapshot, points[0])
        members = [q for q in points if _module_of_question(snapshot, q) == module_id]
        contestant = conn.execute('SELECT * FROM contestants WHERE id=?',
                                  (assessment['contestant_id'],)).fetchone()
        return {
            'assessment_id': assessment_id,
            'contestant_id': assessment['contestant_id'],
            'contestant_code': f"P{assessment['contestant_id']:04d}",
            'display_name': contestant['display_name'],
            'code': assessment['code'],
            'status': assessment['status'],
            'bank_version': assessment['bank_version'],
            'module': {'id': module_id, 'name': _module_name(snapshot, module_id)},
            'questions': [_annotation_payload(conn, assessment, question) for question in members],
        }


@router.get('/assessments/{assessment_id}/questions/{question_id}/annotation')
def annotation_detail(assessment_id: int, question_id: str, request: Request) -> dict:
    with request_connection() as conn:
        admin_context(conn, request)
        assessment = conn.execute('SELECT * FROM assessments WHERE id=?', (assessment_id,)).fetchone()
        if not assessment:
            raise conflict('测试不存在', 'not_found')
        snapshot = load_snapshot(conn, assessment['bank_version'])
        question = next((q for q in snapshot.public['questions'] if q['id'] == question_id), None)
        if not question:
            raise conflict('题目不存在', 'not_found')
        if question['type'] != 'point_identification':
            raise conflict('该题不是标注题', 'not_point_question')
        payload = _annotation_payload(conn, assessment, question)
        contestant = conn.execute('SELECT * FROM contestants WHERE id=?',
                                  (assessment['contestant_id'],)).fetchone()
        return {
            'assessment_id': assessment_id,
            'contestant_id': assessment['contestant_id'],
            'contestant_code': f"P{assessment['contestant_id']:04d}",
            'display_name': contestant['display_name'],
            'code': assessment['code'],
            'bank_version': assessment['bank_version'],
            **payload,
        }


@router.get('/assets/{asset_id}')
def admin_asset(asset_id: str, request: Request) -> Response:
    from .media import asset_response

    with request_connection() as conn:
        admin_context(conn, request)
        store = media_store()
        meta = store.get(asset_id)
        if not meta:
            raise ApiError(404, 'not_found', '素材不存在')
        path = store.path_for(asset_id)
    return asset_response(
        asset_id=asset_id,
        assessment_id=None,
        question_id=None,
        delivery_id=None,
        request_id=request.scope.get('state', {}).get('request_id', ''),
        range_header=request.headers.get('range'),
        path=path,
        meta=meta,
    )


# ------------------------------------------------------------------- events
@router.get('/events')
def events(request: Request, assessment_id: int) -> dict:
    with request_connection() as conn:
        admin_context(conn, request)
        rows = conn.execute(
            'SELECT * FROM events WHERE assessment_id=? ORDER BY id', (assessment_id,)
        ).fetchall()
        transfers = conn.execute(
            'SELECT * FROM asset_transfers WHERE assessment_id=? ORDER BY id', (assessment_id,)
        ).fetchall()
    return {
        'events': [dict(r) for r in rows],
        'asset_transfers': [dict(r) for r in transfers],
    }


# ------------------------------------------------------------------- export
def _filter_description(filters: dict) -> str:
    """One line that says exactly which slice of the data an export covers."""
    parts = []
    contestant_type = filters.get('contestant_type')
    parts.append(f"类型：{contestant_type or '全部'}")
    contestant_id = filters.get('contestant_id')
    parts.append(f"指定选手：{contestant_id}" if contestant_id else '指定选手：无')
    extra = filters.get('include_non_statistics')
    parts.append('记录范围：全部记录（含不计入统计的）' if extra else '记录范围：只含计入统计的')
    mode = filters.get('mode') or 'by_contestant'
    parts.append('聚合方式：按测试次统计' if mode == 'by_test' else '聚合方式：按选手等权')
    bank_version = filters.get('bank_version')
    parts.append(f"题库版本：{bank_version or '当前'}")
    return '；'.join(parts)


def _matrix_sheets(matrix: dict) -> list[tuple[str, list[list[object]]]]:
    question_ids = matrix['question_order']
    modules = matrix['modules']

    header = [
        '测试ID', '选手代号', '类型', '姓名/模型', '思考程度', '接入应用', '模型来源',
        '测试次', '身份验证码', '状态', '已答题数', '题库版本', '统计资格',
        '活跃天数区间（旧问卷单独标识）', '熟悉程度',
    ]
    for question_id in question_ids:
        header.append(f"{question_id} 得分")
    for question_id in question_ids:
        header.append(f"{question_id} 用时ms")
    header.append('总分')
    header.append('满分')
    header.append('得分率')
    header.append('总用时ms')
    header.append('开始时刻')
    header.append('完成时刻')
    header.append('异常标记')

    rows: list[list[object]] = [header]
    for row in matrix['rows']:
        line: list[object] = [
            row['assessment_id'], row['contestant_code'], row['contestant_type'], row['display_name'],
            row['reasoning_effort'], row['agent_host'], row['invocation_route'], row['test_number'],
            row['code'], row['status'], row['answered_count'], row['bank_version'],
            '计入' if row['include_in_statistics'] else '不计入',
            row['survey']['active_days_label'] or '不适用', row['survey']['familiarity'] or '不适用',
        ]
        for question_id in question_ids:
            line.append(row['questions'][question_id]['score'])
        for question_id in question_ids:
            line.append(row['questions'][question_id]['duration_with_delivery_ms'])
        line.extend([
            row['total_score'], row['total_score_max'], row['score_rate'], row['total_duration_ms'],
            row['started_at'], row['completed_at'], '；'.join(row['anomalies']),
        ])
        rows.append(line)

    detail_header = ['测试ID', '选手代号', '类型', '题目', '题序', '得分', '满分', '含交付用时ms',
                     '交付完成后用时ms', '客户端就绪后用时ms', '答案摘要', '接收时刻', '交付不完整', '明确放弃']
    detail_rows: list[list[object]] = [detail_header]
    for row in matrix['rows']:
        for question_id in question_ids:
            item = row['questions'][question_id]
            if item['score'] is None:
                continue
            detail_rows.append([
                row['assessment_id'], row['contestant_code'], row['contestant_type'], question_id,
                question_id, item['score'], item['score_max'], item['duration_with_delivery_ms'],
                item['duration_post_delivery_ms'], item['duration_client_ready_ms'],
                item['answer_summary'], item['answer_received_at'],
                '是' if item['delivery_incomplete'] else '否', '是' if item['abandoned'] else '否',
            ])

    summary_header = ['分组', '样本单位', '选手数', '测试次数', '平均总分']
    summary_header.extend([f'{qid} 均分' for qid in question_ids])
    summary_rows: list[list[object]] = [summary_header]
    for key, label in (('all', '全体汇总'), ('human', '人类'), ('agent', '智能体')):
        item = matrix['summaries'][key]
        line: list[object] = [
            label, matrix['summaries']['sample_unit'],
            item.get('contestants', item.get('tests')), item.get('tests'), item.get('mean_total'),
        ]
        line.extend(item['mean_by_question'].get(qid) for qid in question_ids)
        summary_rows.append(line)
    for item in matrix['summaries']['contestants'].values():
        line = [
            f"{item['contestant_code']} {item['display_name']}",
            matrix['summaries']['sample_unit'], 1, item['tests'], item['mean_total'],
        ]
        line.extend(item['mean_by_question'].get(qid) for qid in question_ids)
        summary_rows.append(line)

    scope_rows: list[list[object]] = [
        ['项目', '值'],
        ['生成时间', matrix['generated_at']],
        ['数据修订号', matrix['revision']],
        ['题库版本', matrix['bank_version']],
        ['规则版本', '、'.join(sorted({str(r['scoring_rules_version']) for r in matrix['rows']})) or '—'],
        ['计时版本', '、'.join(sorted({str(r['timing_policy_version']) for r in matrix['rows']})) or '—'],
        ['筛选条件', _filter_description(matrix.get('filters') or {})],
        ['统计口径', matrix['summaries']['sample_unit']],
        ['聚合方式', matrix['mode']],
        ['样本数', f"纳入 {matrix['summaries']['all'].get('tests', 0)} 次完整测试 / "
                   f"{matrix['summaries']['all'].get('contestants', 0) or matrix['summaries']['all'].get('tests', 0)} 名选手"],
        ['纳入条件', '完整提交、计入统计、非作废、题库与规则版本一致'],
        ['默认均值口径', '先按选手求跨次平均，再对选手求平均（避免重测次数影响权重）'],
        ['缺失值处理', '未开始、未作答或缺失用时显示为 —，不计入均值'],
        ['明确放弃', '记 0 分并在明细中单独标记'],
        ['模块', '；'.join(f"{m['id']} {m['name']} {m['score_max']}分" for m in modules)],
    ]

    return [
        ('总表', rows),
        ('逐题明细', detail_rows),
        ('统计口径与汇总', summary_rows),
        ('导出说明', scope_rows),
    ]


@router.get('/export')
def export(request: Request, format: str = 'xlsx', scope: str = 'all',
           contestant_type: str | None = None, contestant_id: int | None = None,
           include_non_statistics: bool = False, mode: str = 'by_contestant',
           bank_version: str | None = None) -> Response:
    if format not in ('xlsx', 'csv', 'json'):
        raise unprocessable('format 只能是 xlsx、csv 或 json')
    filters = MatrixFilters(
        contestant_type=contestant_type,
        contestant_id=contestant_id,
        include_non_statistics=include_non_statistics,
        mode=mode,
        bank_version=bank_version,
    )
    with request_connection() as conn:
        admin_context(conn, request)
        matrix = build_matrix(conn, filters)
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    if format == 'json':
        # 导出的 JSON 要能自证口径：这份文件是哪一版数据、什么筛选条件、什么统计规则。
        body = json.dumps({
            'meta': {
                'generated_at': matrix['generated_at'],
                'revision': matrix['revision'],
                'bank_version': matrix['bank_version'],
                'scoring_rules_version': sorted({str(r['scoring_rules_version'])
                                                 for r in matrix['rows']}),
                'timing_policy_version': sorted({str(r['timing_policy_version'])
                                                 for r in matrix['rows']}),
                'filters': matrix.get('filters') or {},
                'filter_description': _filter_description(matrix.get('filters') or {}),
                'sample_unit': matrix['summaries']['sample_unit'],
                'mode': matrix['mode'],
                'included_tests': matrix['summaries']['all'].get('tests', 0),
                'included_contestants': matrix['summaries']['all'].get('contestants', 0),
                'scope': '完整记录（含不计入统计的试次，带统计资格标记）'
                         if filters.include_non_statistics else '只含计入统计的试次',
            },
            **matrix,
        }, ensure_ascii=False, indent=2).encode('utf-8')
        return Response(content=body, media_type='application/json; charset=utf-8', headers={
            'Content-Disposition': f'attachment; filename="hsr-mmak-bench-matrix-{stamp}.json"',
            'Cache-Control': 'no-store',
        })
    sheets = _matrix_sheets(matrix)
    if format == 'csv':
        buffer = io.StringIO()
        writer = csv.writer(buffer)
        for _, rows in sheets:
            for row in rows:
                writer.writerow(['' if cell is None else cell for cell in row])
        body = ('\ufeff' + buffer.getvalue()).encode('utf-8')
        return Response(content=body, media_type='text/csv; charset=utf-8', headers={
            'Content-Disposition': f'attachment; filename="hsr-mmak-bench-matrix-{stamp}.csv"',
            'Cache-Control': 'no-store',
        })
    body = build_workbook(sheets)
    return Response(content=body, media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                    headers={
                        'Content-Disposition': f'attachment; filename="hsr-mmak-bench-matrix-{stamp}.xlsx"',
                        'Cache-Control': 'no-store',
                    })
