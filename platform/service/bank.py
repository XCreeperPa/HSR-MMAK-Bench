"""Loading, validating and snapshotting the stage1 V2 question bank.

The bank itself is not modified by the formal service. The service reads the
reviewed stage1 payload, validates it, and freezes an immutable snapshot:

* ``public`` - what a contestant may see: question text, options, media
  references, the character roster. No answer, no head region, no provenance.
* ``private`` - the answer key. It is stored in a separate table and is only
  ever read inside admin code paths.

The snapshot fixes the bank version, the question order and the option display
order. Both stay constant for the whole life of a test, so a refresh never
reorders a question and the scoring rules stay reproducible.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import settings
from .db import now_iso

PUBLIC_QUESTION_FIELDS = (
    'id', 'module', 'dimension', 'type', 'prompt', 'instructions', 'score',
    'selection_target', 'max_selections', 'allow_empty',
    'image_width', 'image_height', 'coordinate_space', 'character_roster_version',
)


class BankError(RuntimeError):
    """Raised when the bank on disk does not satisfy the expected contract."""


@dataclass(frozen=True)
class BankSnapshot:
    version: str
    content_digest: str
    public: dict
    private: dict

    def question(self, question_id: str) -> dict:
        for item in self.public['questions']:
            if item['id'] == question_id:
                return item
        raise KeyError(question_id)

    def question_at(self, order: int) -> dict:
        return self.public['questions'][order]

    @property
    def question_count(self) -> int:
        return len(self.public['questions'])

    @property
    def score_max(self) -> float:
        return float(sum(q['score'] for q in self.public['questions']))

    def asset_ids(self, question: dict) -> list[str]:
        used = list(question.get('stimulus', []))
        for option in question.get('options', []):
            used.extend(option.get('assets', []))
        return list(dict.fromkeys(used))


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding='utf-8'))


def _public_question(question: dict, order: int) -> dict:
    public = {field: question[field] for field in PUBLIC_QUESTION_FIELDS if field in question}
    public['order'] = order
    public['stimulus'] = list(question.get('stimulus', []))
    public['options'] = [
        {
            'id': option['id'],
            'label': option.get('label', ''),
            'content_id': option.get('content_id'),
            'assets': list(option.get('assets', [])),
        }
        for option in question.get('options', [])
    ]
    return public


def load_source_bank(bank_dir: Path | None = None) -> tuple[dict, dict, dict]:
    """Read and validate the reviewed stage1 payload from disk."""
    root = Path(bank_dir) if bank_dir else settings().bank_dir
    manifest = _read_json(root / 'manifest.json')
    questions = _read_json(root / 'questions.json')
    assets = _read_json(root / 'assets.json')
    key = _read_json(root / 'answer-key.demo.json')
    characters = _read_json(root / 'characters.json')

    if manifest['version'] not in {
        '2-four-modules-20261025-stage1',
        '2-four-modules-20261025-stage2-music-shuffle',
    }:
        raise BankError(f"unexpected bank version {manifest['version']!r}")
    if len(questions) != manifest['question_count']:
        raise BankError('question count does not match the manifest')
    if sum(q['score'] for q in questions) != manifest['score_max']:
        raise BankError('score total does not match the manifest')
    if set(key) != {q['id'] for q in questions}:
        raise BankError('answer key does not cover exactly the published questions')

    character_ids = {c['id'] for c in characters['characters']}
    if len(character_ids) != len(characters['characters']):
        raise BankError('duplicate character id in the roster')

    asset_ids = {a['id'] for a in assets}
    for question in questions:
        if question['type'] not in ('single_choice', 'multi_choice', 'point_identification'):
            raise BankError(f"unsupported question type {question['type']!r}")
        used = list(question.get('stimulus', []))
        for option in question.get('options', []):
            used.extend(option.get('assets', []))
        if not set(used) <= asset_ids:
            raise BankError(f"question {question['id']} references an unknown asset")

        if question['type'] == 'point_identification':
            targets = key[question['id']]['targets']
            if len(question.get('stimulus', [])) != 1 or question['score'] != len(targets):
                raise BankError(f"question {question['id']} target count does not match the score")
            if not all(t['character_id'] in character_ids for t in targets):
                raise BankError(f"question {question['id']} targets a character outside the roster")
            continue

        if [o['id'] for o in question['options']] != ['A', 'B', 'C', 'D']:
            raise BankError(f"question {question['id']} does not use the A-D option set")
        correct = key[question['id']]['correct_options']
        if len(set(correct)) != len(correct) or not set(correct) <= {'A', 'B', 'C', 'D'}:
            raise BankError(f"question {question['id']} has an invalid correct option set")
        if question['type'] == 'single_choice' and len(correct) != 1:
            raise BankError(f"question {question['id']} is single choice but has {len(correct)} answers")
        if question['type'] == 'multi_choice' and question['score'] != 6:
            raise BankError(f"question {question['id']} multi choice score is not 6")
        for option in 'ABCD':
            decision = key[question['id']]['options'][option]['decision']
            if decision != (option in correct):
                raise BankError(f"question {question['id']} option {option} decision is inconsistent")

    return manifest, {'questions': questions, 'characters': characters, 'assets': assets}, key


def build_snapshot(bank_dir: Path | None = None) -> BankSnapshot:
    """Build the immutable snapshot of the bank."""
    manifest, source, key = load_source_bank(bank_dir)
    questions = source['questions']
    public_questions = [_public_question(q, index + 1) for index, q in enumerate(questions)]
    public = {
        'version': manifest['version'],
        'title': manifest.get('title', ''),
        'question_count': len(public_questions),
        'score_max': sum(q['score'] for q in public_questions),
        'questions': public_questions,
        'characters': source['characters'],
        'question_order': [q['id'] for q in public_questions],
        'option_order': {q['id']: [o['id'] for o in q['options']] for q in public_questions},
        'modules': manifest.get('planned_structure', {}).get('modules', []),
    }
    digest = hashlib.sha256(
        json.dumps(public, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')
    ).hexdigest()
    return BankSnapshot(
        version=manifest['version'],
        content_digest=digest,
        public=public,
        private={'key': key},
    )


def persist_snapshot(conn, snapshot: BankSnapshot) -> None:
    """Store the snapshot; an existing version is never rewritten."""
    row = conn.execute(
        'SELECT content_digest FROM bank_versions WHERE version=?', (snapshot.version,)
    ).fetchone()
    if row and row['content_digest'] != snapshot.content_digest:
        raise BankError(
            f"bank version {snapshot.version} already exists with a different digest; "
            'publish a new version instead of rewriting history'
        )
    conn.execute(
        'INSERT OR IGNORE INTO bank_versions(version,payload_json,content_digest,created_at) VALUES (?,?,?,?)',
        (
            snapshot.version,
            json.dumps(snapshot.public, ensure_ascii=False, sort_keys=True),
            snapshot.content_digest,
            now_iso(),
        ),
    )
    conn.execute(
        'INSERT OR IGNORE INTO bank_keys(version,key_json,created_at) VALUES (?,?,?)',
        (
            snapshot.version,
            json.dumps(snapshot.private['key'], ensure_ascii=False, sort_keys=True),
            now_iso(),
        ),
    )


def load_snapshot(conn, version: str) -> BankSnapshot:
    """Read a stored snapshot back, including its private key."""
    row = conn.execute('SELECT payload_json, content_digest FROM bank_versions WHERE version=?', (version,)).fetchone()
    if not row:
        raise BankError(f"bank version {version!r} is not stored in this database")
    key_row = conn.execute('SELECT key_json FROM bank_keys WHERE version=?', (version,)).fetchone()
    if not key_row:
        raise BankError(f"private answer key for {version!r} is missing")
    return BankSnapshot(
        version=version,
        content_digest=row['content_digest'],
        public=json.loads(row['payload_json']),
        private={'key': json.loads(key_row['key_json'])},
    )
