"""Scoring rules for the formal service.

The rules are reused from the reviewed V2 prototype (``web/workflow.py``) so a
score recorded by the formal service means exactly the same thing as a score
recorded during the V2 review stage:

* point identification - one point per playable character, the name and the
  head location must both be correct, no normalisation, no double counting;
* single choice - the full score or zero;
* multi choice - kept for bank compatibility, not used by the V2 bank.

Strictness differences from the prototype are deliberate and are listed in
``docs/decisions.md``: a single choice question rejects more than one option
instead of silently keeping the first one, and an empty selection is an
explicit "abandon" only where the question allows it.
"""
from __future__ import annotations

from typing import Iterable, Sequence


def inside_head(x: float, y: float, polygon: Sequence[Sequence[float]]) -> bool:
    """Ray casting with inclusive polygon edges.

    Coordinates are original image pixels. The polygon is closed implicitly.
    """
    inside = False
    count = len(polygon)
    for index in range(count):
        ax, ay = polygon[index]
        bx, by = polygon[(index + 1) % count]
        cross = (x - ax) * (by - ay) - (y - ay) * (bx - ax)
        if abs(cross) < 1e-6 and min(ax, bx) <= x <= max(ax, bx) and min(ay, by) <= y <= max(ay, by):
            return True
        if (ay > y) != (by > y) and x < (bx - ax) * (y - ay) / (by - ay) + ax:
            inside = not inside
    return inside


def evaluate_points(
    targets: Sequence[dict],
    annotations: Sequence[dict],
) -> dict:
    """Score one point identification answer.

    A target is hit when the submitted point falls inside its head polygon.
    The point earns a point only when the submitted character matches the
    character of that target. One target can be claimed once.
    """
    matched: set[str] = set()
    location_matches: dict[str, int] = {}
    details: list[dict] = []

    def assign_location(index: int, hits: list[list[dict]], seen: set[str]) -> bool:
        for target in hits[index]:
            tid = target['id']
            if tid in seen:
                continue
            seen.add(tid)
            if tid not in location_matches or assign_location(location_matches[tid], hits, seen):
                location_matches[tid] = index
                return True
        return False

    all_hits = [[t for t in targets if inside_head(a['x'], a['y'], t['head_region'])] for a in annotations]
    for index in range(len(annotations)):
        assign_location(index, all_hits, set())

    for annotation, hits in zip(annotations, all_hits):
        correct = next((t for t in hits if t['character_id'] == annotation['character_id']), None)
        earned = bool(correct and correct['id'] not in matched)
        if correct:
            matched.add(correct['id'])
        details.append(
            {
                **annotation,
                'score': int(earned),
                'located': bool(hits),
                'duplicate': bool(correct and not earned),
                'matched_target_id': correct['id'] if correct else None,
            }
        )
    return {'score': len(matched), 'localized_count': len(location_matches), 'annotations': details}


def score_choice(
    question: dict,
    correct_options: Iterable[str],
    selected: Sequence[str],
) -> float:
    """Score a selection question."""
    correct = list(correct_options)
    if question.get('type') == 'single_choice':
        return float(question['score']) if len(selected) == 1 and selected[0] == correct[0] else 0.0
    if any(option not in correct for option in selected):
        return 0.0
    return 6 / len(correct) * len(selected)
