"""Shared labels and version for the human pre-test questionnaire."""
from typing import Literal

SURVEY_VERSION = 'survey-v2-active-days-range'
ActiveDaysRange = Literal['0-49', '50-199', '200-500', 'over-500']
ACTIVE_DAY_RANGES = {
    '0-49': '0–49 天',
    '50-199': '50–199 天',
    '200-500': '200–500 天',
    'over-500': '500 天以上',
}


def activity_label(values: dict) -> str | None:
    if values.get('active_days_range') in ACTIVE_DAY_RANGES:
        return ACTIVE_DAY_RANGES[values['active_days_range']]
    if values.get('active_days') is not None:
        return f"旧问卷：过去30天活跃{values['active_days']}天"
    return None
