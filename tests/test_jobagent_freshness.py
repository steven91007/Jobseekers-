"""Posting-age parsing, the freshness score and window checks."""

from datetime import datetime, timezone

import pytest

from jobagent import freshness
from jobagent.normalize import is_ai_engineer_title

NOW = datetime(2026, 9, 29, 18, 0, tzinfo=timezone.utc)


@pytest.mark.parametrize("posted, expected", [
    ("2026-09-29T18:00:00+00:00", 100),
    ("2026-09-29T06:00:00+00:00", 71),   # 12h
    ("2026-09-28T18:00:00+00:00", 50),   # 24h
    ("2026-09-26T18:00:00+00:00", 12),   # 3 days
    ("2026-09-29", 84),                  # date-only counts as noon UTC: 6h
    ("", None),
    ("garbage", None),
])
def test_score_halves_every_day(posted, expected):
    assert freshness.score(posted, 24, NOW) == expected


def test_within_hours_precise_and_date_only():
    assert freshness.within_hours("2026-09-28T19:00:00+00:00", 24, NOW)
    assert not freshness.within_hours("2026-09-28T17:00:00+00:00", 24, NOW)
    # a date-only posting counts as the whole day, so LinkedIn's own 24h results are not dropped
    assert freshness.within_hours("2026-09-28", 24, NOW)
    assert not freshness.within_hours("2026-09-27", 24, NOW)
    assert freshness.within_hours("2026-09-22", 168, NOW)
    assert not freshness.within_hours("2026-09-21", 168, NOW)
    assert not freshness.within_hours("", 168, NOW)


@pytest.mark.parametrize("text, expected", [
    ("5 hours ago", "2026-09-29T13:00:00+00:00"),
    ("1 hour ago", "2026-09-29T17:00:00+00:00"),
    ("30 minutes ago", "2026-09-29T17:30:00+00:00"),
    ("Just now", "2026-09-29T18:00:00+00:00"),
    ("2 days ago", ""),
    ("1 week ago", ""),
    ("", ""),
])
def test_from_relative(text, expected):
    assert freshness.from_relative(text, NOW) == expected


def test_label():
    assert freshness.label("2026-09-29T15:10:00+00:00", NOW) == "2h ago"
    assert freshness.label("2026-09-29T17:40:00+00:00", NOW) == "<1h ago"
    assert freshness.label("2026-09-26T18:00:00+00:00", NOW) == "3d ago"
    assert freshness.label("2026-09-28", NOW) == "2026-09-28"
    assert freshness.label("", NOW) == "?"


@pytest.mark.parametrize("title, focus", [
    ("AI Engineer", True),
    ("Senior AI Engineer (m/w/d)", True),
    ("AI Software Engineer", True),
    ("AI/ML Engineer", True),
    ("Applied AI Engineer", True),
    ("Generative AI Engineer", True),
    ("GenAI Engineer", True),
    ("LLM Engineer", True),
    ("Artificial Intelligence Engineer", True),
    ("AI Platform Engineer", True),
    ("Senior Software Engineer* Agentic AI", True),
    ("Software Engineer - AI", True),
    ("Backend Engineer, AI Platform", True),
    ("Data Engineer (AI)", False),
    ("Machine Learning Engineer", False),
    ("Data Engineer", False),
    ("AI Sales Engineer", False),
    ("AI Product Manager", False),
])
def test_is_ai_engineer_title(title, focus):
    assert is_ai_engineer_title(title) is focus
