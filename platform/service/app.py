"""FastAPI application for the formal V2 test service.

One process serves the human pages, the contestant API, the public task
document and the admin console. The bank snapshot is frozen on start up, every
official contestant is given exactly one usable code, and the deployment media
directory is verified against its manifest before the first request is served.
"""
from __future__ import annotations

import json
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from . import admin as admin_api
from . import api
from .bank import build_snapshot, persist_snapshot
from .config import settings
from .db import connect, init_db
from .errors import ApiError
from .events import new_request_id
from .lifecycle import ensure_active_assessments
from .media import media_store, reset_media_store
from .roster import sync_roster
from .taskdoc import task_document_html

PAGES = __import__('pathlib').Path(__file__).resolve().parent / 'pages'
ADMIN_PAGES = PAGES / 'admin'


@asynccontextmanager
async def lifespan(app: FastAPI):
    cfg = settings()
    cfg.ensure_dirs()
    init_db()
    reset_media_store()
    store = media_store()
    problems = store.verify()
    if problems:
        raise RuntimeError('deployment media failed verification: ' + '; '.join(problems[:5]))

    with connect() as conn:
        conn.execute('BEGIN IMMEDIATE')
        try:
            sync_roster(conn)
            snapshot = build_snapshot()
            persist_snapshot(conn, snapshot)
            created = ensure_active_assessments(conn, snapshot)
            conn.execute('COMMIT')
        except Exception:
            conn.execute('ROLLBACK')
            raise
    if created['count']:
        print(f"[startup] issued {created['count']} access codes for new contestants")
    print(f"[startup] bank {snapshot.version} ({snapshot.question_count} questions, "
          f"{store.asset_count if hasattr(store, 'asset_count') else len(store.assets)} media assets)")
    yield


app = FastAPI(
    title='HSR-MMAK-Bench 正式测试服务',
    version='2-four-modules-20261025-stage2-music-shuffle',
    lifespan=lifespan,
    docs_url='/api/v1/docs',
    openapi_url='/api/v1/openapi.json',
)


class RequestContextMiddleware:
    """Stamp a request id and capture the moment an answer body fully arrived.

    The timestamp is taken from the raw ASGI receive stream, so it is recorded
    before the body is parsed and before any scoring happens. This is the
    ``answer_received_at`` moment from the design document.
    """

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http':
            return await self.app(scope, receive, send)
        scope.setdefault('state', {})['request_id'] = new_request_id()
        is_answer = scope['method'] == 'POST' and scope.get('path', '').endswith('/answers')
        started = time.perf_counter()

        async def incoming():
            message = await receive()
            if is_answer and message['type'] == 'http.request' and not message.get('more_body', False):
                scope['state']['answer_received_at'] = _now_iso()
            return message

        async def outgoing(message):
            if message['type'] == 'http.response.start':
                message['headers'].append((b'x-request-id', scope['state']['request_id'].encode()))
                message['headers'].append(
                    (b'x-response-time-ms', str(round((time.perf_counter() - started) * 1000, 1)).encode())
                )
            await send(message)

        await self.app(scope, incoming, outgoing)


def _now_iso() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%f')[:-3] + 'Z'


app.add_middleware(RequestContextMiddleware)


@app.exception_handler(ApiError)
async def api_error_handler(request: Request, exc: ApiError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content={'code': exc.code, 'message': exc.message},
        headers={'Cache-Control': 'no-store'},
    )


@app.exception_handler(HTTPException)
async def http_error_handler(request: Request, exc: HTTPException) -> JSONResponse:
    detail = exc.detail
    if isinstance(detail, dict) and 'code' in detail:
        content = {'code': detail['code'], 'message': detail.get('message', '')}
    else:
        content = {'code': 'http_error', 'message': str(detail)}
    return JSONResponse(status_code=exc.status_code, content=content,
                        headers={'Cache-Control': 'no-store'})


@app.exception_handler(RequestValidationError)
async def validation_error_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    return JSONResponse(
        status_code=422,
        content={'code': 'invalid_request', 'message': '请求字段不符合接口约定'},
        headers={'Cache-Control': 'no-store'},
    )


app.include_router(api.router)
app.include_router(admin_api.router)


@app.get('/task', response_class=HTMLResponse)
def task_document(request: Request) -> HTMLResponse:
    """The public task document. It never contains an answer or an admin route."""
    return HTMLResponse(task_document_html(request), headers={'Cache-Control': 'no-store'})


def _page(name: str) -> Response:
    path = PAGES / name
    if not path.is_file():
        raise HTTPException(404)
    return Response(content=path.read_text(encoding='utf-8'), media_type='text/html; charset=utf-8',
                    headers={'Cache-Control': 'no-store'})


@app.get('/', response_class=HTMLResponse)
def human_entry() -> Response:
    return _page('index.html')


@app.get('/survey', response_class=HTMLResponse)
def human_survey() -> Response:
    return _page('survey.html')


@app.get('/play', response_class=HTMLResponse)
def human_play() -> Response:
    return _page('play.html')


@app.get('/done', response_class=HTMLResponse)
def human_done() -> Response:
    return _page('done.html')


@app.get('/admin', response_class=HTMLResponse)
def admin_entry() -> Response:
    return _page('admin/index.html')


class RevalidatingStaticFiles(StaticFiles):
    """Serve static files, but always let the browser revalidate them.

    A stale script after a deployment is worse than a slightly slower load:
    the page would run yesterday's code against today's API.
    """

    def file_response(self, *args, **kwargs):
        response = super().file_response(*args, **kwargs)
        response.headers['Cache-Control'] = 'no-cache, must-revalidate'
        return response


app.mount('/static', RevalidatingStaticFiles(directory=str(PAGES)), name='static')


if __name__ == '__main__':
    import uvicorn

    cfg = settings()
    print(f'admin user: {cfg.admin_username}')
    if cfg.admin_password_is_generated:
        print(f'admin password (generated, set HSR_SERVICE_ADMIN_PASSWORD to pin it): {cfg.admin_password}')
    uvicorn.run(app, host=cfg.host, port=cfg.port)
