"""Protected media delivery with byte accurate transfer events.

Media is never served from a public static directory. A contestant may only
fetch an asset that belongs to the question currently on screen (or one that
was already delivered to their own test). Every request is recorded as
append only events so the admin console can rebuild what actually happened on
the wire, including partial and interrupted transfers.
"""
from __future__ import annotations

import hashlib
import json
import re
import uuid
from pathlib import Path
from typing import Iterator

from fastapi import HTTPException
from fastapi.responses import StreamingResponse

from .config import settings
from .events import record_event, refresh_delivery_complete

RANGE_RE = re.compile(r'^bytes=(?P<start>\d*)-(?P<end>\d*)$')
CHUNK = 256 * 1024


class MediaStore:
    """Read only view over the deployment media directory."""

    def __init__(self, media_dir: Path | None = None) -> None:
        self.root = Path(media_dir) if media_dir else settings().media_dir
        self.manifest_path = self.root / 'media-manifest.json'
        self.assets: dict[str, dict] = {}
        self.version = ''
        self._verified = False

    def load(self) -> None:
        if not self.manifest_path.is_file():
            raise RuntimeError(
                f'media manifest missing at {self.manifest_path}; '
                'run tools/build_service_media.py before starting the service'
            )
        manifest = json.loads(self.manifest_path.read_text(encoding='utf-8'))
        self.version = manifest.get('version', '')
        self.assets = {a['id']: a for a in manifest['assets']}

    def verify(self) -> list[str]:
        """Check every deployed file against the manifest digest."""
        problems: list[str] = []
        for asset_id, meta in self.assets.items():
            path = self.root / meta['file']
            if not path.is_file():
                problems.append(f'{asset_id}: file missing')
                continue
            data = path.read_bytes()
            if len(data) != meta['bytes']:
                problems.append(f'{asset_id}: size mismatch')
                continue
            if hashlib.sha256(data).hexdigest() != meta['sha256']:
                problems.append(f'{asset_id}: sha256 mismatch')
        self._verified = not problems
        return problems

    def get(self, asset_id: str) -> dict | None:
        return self.assets.get(asset_id)

    def path_for(self, asset_id: str) -> Path:
        meta = self.get(asset_id)
        if not meta:
            raise HTTPException(404, '素材不存在')
        path = (self.root / meta['file']).resolve()
        if not str(path).startswith(str(self.root.resolve())):
            raise HTTPException(404, '素材不存在')
        if not path.is_file():
            raise HTTPException(404, '素材不存在')
        return path

    def manifest_entry(self, asset_id: str, base_url: str) -> dict:
        meta = self.get(asset_id)
        if not meta:
            raise HTTPException(404, '素材不存在')
        entry = {
            'asset_id': asset_id,
            'mime': meta['mime'],
            'bytes': meta['bytes'],
            'sha256': meta['sha256'],
            'url': f'{base_url}/api/v1/assets/{asset_id}',
        }
        if 'width' in meta:
            entry['width'] = meta['width']
            entry['height'] = meta['height']
        return entry


def _record_transfer(
    assessment_id: int,
    question_id: str,
    asset_id: str,
    transfer_id: str,
    byte_start: int,
    byte_end: int,
    bytes_sent: int,
    http_status: int,
    range_header: str | None,
    required_assets: list[tuple[str, int]],
) -> None:
    """Persist a successful byte range and refresh the delivery completion moment."""
    from .db import connect, now_iso

    conn = connect()
    try:
        conn.execute(
            '''INSERT INTO asset_transfers(assessment_id,question_id,asset_id,transfer_id,byte_start,
                 byte_end,bytes_sent,http_status,range_header,result,completed_at)
               VALUES (?,?,?,?,?,?,?,?,?,'complete',?)''',
            (assessment_id, question_id, asset_id, transfer_id, byte_start, byte_end,
             bytes_sent, http_status, range_header, now_iso()),
        )
        if required_assets:
            refresh_delivery_complete(conn, assessment_id, question_id, required_assets)
    finally:
        conn.close()


_store: MediaStore | None = None


def media_store() -> MediaStore:
    global _store
    if _store is None:
        _store = MediaStore()
        _store.load()
    return _store


def reset_media_store() -> None:
    global _store
    _store = None


def parse_range(header: str | None, size: int) -> tuple[int, int] | None:
    """Parse a single byte range. Returns inclusive (start, end) or None."""
    if not header:
        return None
    match = RANGE_RE.match(header.strip())
    if not match:
        raise HTTPException(416, '不支持的 Range 请求')
    raw_start, raw_end = match.group('start'), match.group('end')
    if raw_start == '' and raw_end == '':
        raise HTTPException(416, '不支持的 Range 请求')
    if raw_start == '':
        length = int(raw_end)
        if length <= 0:
            raise HTTPException(416, '不支持的 Range 请求')
        start = max(0, size - length)
        end = size - 1
    else:
        start = int(raw_start)
        end = int(raw_end) if raw_end else size - 1
        if start >= size:
            raise HTTPException(416, '请求范围超出素材长度')
        end = min(end, size - 1)
    if end < start:
        raise HTTPException(416, '不支持的 Range 请求')
    return start, end


def asset_response(
    *,
    asset_id: str,
    assessment_id: int | None,
    question_id: str | None,
    delivery_id: str | None,
    request_id: str,
    range_header: str | None,
    path: Path,
    meta: dict,
    required_assets: list[tuple[str, int]] | None = None,
) -> StreamingResponse:
    """Stream an asset and record the transfer events around it."""
    size = meta['bytes']
    window = parse_range(range_header, size)
    if window is None:
        start, end = 0, size - 1
        status = 200
    else:
        start, end = window
        status = 206
    length = end - start + 1
    transfer_id = uuid.uuid4().hex

    record_event(
        'asset_request_received_at',
        assessment_id=assessment_id,
        question_id=question_id,
        delivery_id=delivery_id,
        request_id=request_id,
        asset_id=asset_id,
        transfer_id=transfer_id,
        http_status=status,
        range_header=range_header,
        result='received',
    )

    def body() -> Iterator[bytes]:
        sent = 0
        try:
            with path.open('rb') as handle:
                handle.seek(start)
                remaining = length
                while remaining > 0:
                    chunk = handle.read(min(CHUNK, remaining))
                    if not chunk:
                        break
                    remaining -= len(chunk)
                    sent += len(chunk)
                    yield chunk
        except Exception as exc:  # pragma: no cover - depends on client behaviour
            record_event(
                'asset_transfer_failed_at',
                assessment_id=assessment_id,
                question_id=question_id,
                delivery_id=delivery_id,
                request_id=request_id,
                asset_id=asset_id,
                transfer_id=transfer_id,
                http_status=status,
                range_header=range_header,
                bytes_sent=sent,
                byte_start=start,
                byte_end=end,
                result=f'failed: {type(exc).__name__}',
            )
            raise
        record_event(
            'asset_send_finished_at',
            assessment_id=assessment_id,
            question_id=question_id,
            delivery_id=delivery_id,
            request_id=request_id,
            asset_id=asset_id,
            transfer_id=transfer_id,
            http_status=status,
            range_header=range_header,
            bytes_sent=sent,
            byte_start=start,
            byte_end=end,
            result='complete' if sent == length else 'incomplete',
        )
        if sent == length and assessment_id is not None and question_id:
            _record_transfer(assessment_id, question_id, asset_id, transfer_id,
                             start, end, sent, status, range_header, required_assets or [])

    headers = {
        'Content-Length': str(length),
        'Accept-Ranges': 'bytes',
        'Cache-Control': 'no-store',
    }
    if status == 206:
        headers['Content-Range'] = f'bytes {start}-{end}/{size}'
    return StreamingResponse(body(), status_code=status, media_type=meta['mime'], headers=headers)
