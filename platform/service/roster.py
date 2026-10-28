"""Import of the private contestant roster.

The roster file is the deployment configuration: it declares who takes part,
how they are displayed, and whether they count towards the official
statistics. Importing is idempotent and never deletes historical records.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from .config import settings
from .db import now_iso

TYPE_MAP = {'human_player': 'human', 'ai_agent': 'agent'}


def roster_path() -> Path:
    return settings().roster_path


def load_roster(path: Path | None = None) -> dict:
    target = Path(path) if path else roster_path()
    roster = json.loads(target.read_text(encoding='utf-8'))
    if roster.get('version') != 1:
        raise ValueError(f'unsupported roster version {roster.get("version")!r}')
    return roster


def sync_roster(conn: sqlite3.Connection, path: Path | None = None) -> dict:
    roster = load_roster(path)
    profiles = roster['contestants']
    if len({p['roster_key'] for p in profiles}) != len(profiles):
        raise ValueError('roster contains duplicate roster_key values')
    inserted = updated = 0
    for profile in profiles:
        subject_type = TYPE_MAP[profile['subject_type']]
        display_name = profile.get('model_name') or profile.get('admin_note')
        values = (
            subject_type,
            profile.get('roster_status', 'confirmed'),
            display_name,
            profile.get('reasoning_effort'),
            profile.get('agent_host'),
            profile.get('invocation_route'),
            profile.get('model_id'),
            json.dumps(profile.get('runtime_config', {}), ensure_ascii=False),
            json.dumps(profile.get('sources', []), ensure_ascii=False),
            profile.get('profile_note'),
            profile.get('admin_note'),
            int(profile.get('include_in_statistics', subject_type == 'agent')),
        )
        existing = conn.execute(
            'SELECT id FROM contestants WHERE roster_key=?', (profile['roster_key'],)
        ).fetchone()
        if existing is None:
            conn.execute(
                '''INSERT INTO contestants(roster_key,subject_type,roster_status,display_name,reasoning_effort,
                   agent_host,invocation_route,model_id,runtime_config_json,source_refs_json,profile_note,
                   admin_note,include_in_statistics,created_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                (profile['roster_key'], *values, now_iso()),
            )
            inserted += 1
        else:
            conn.execute(
                '''UPDATE contestants SET subject_type=?,roster_status=?,display_name=?,reasoning_effort=?,
                   agent_host=?,invocation_route=?,model_id=?,runtime_config_json=?,source_refs_json=?,
                   profile_note=?,admin_note=?,include_in_statistics=? WHERE id=?''',
                (*values, existing['id']),
            )
            updated += 1
    return {'profiles': len(profiles), 'inserted': inserted, 'updated': updated}


def formal_contestants(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Contestants that take part in the official statistics.

    The administrator reserve entry and placeholder identities are not part of
    this list, and they never receive a code.
    """
    return conn.execute(
        'SELECT * FROM contestants WHERE include_in_statistics=1 ORDER BY id'
    ).fetchall()
