import pytest

from jobagent.normalize import classify_any, classify_region, company_key, is_relevant_title, title_key, to_iso


@pytest.mark.parametrize("location, expected", [
    ("Berlin, Berlin, Germany", "DE"),
    ("Munich, DE", "DE"),
    ("München", "DE"),
    ("Amsterdam, North Holland, Netherlands", "NL"),
    ("Dublin, County Dublin, Ireland", "IE"),
    ("Dublin, IE; London, UK", "IE"),
    ("Remote - Ireland", "IE"),
    ("Cork, County Cork, Ireland", None),
    ("Remote - Europe", "REMOTE_EU"),
    ("London, United Kingdom", None),
    ("Paris", None),
    ("", None),
])
def test_classify_region(location, expected):
    assert classify_region(location) == expected


def test_classify_any_prefers_concrete_region_over_remote():
    assert classify_any(["Remote - EMEA", "Berlin"]) == "DE"
    assert classify_any(["London", "Remote - EMEA"]) == "REMOTE_EU"
    assert classify_any(["London", "Paris"]) is None


@pytest.mark.parametrize("title, tier, keep", [
    ("Applied AI Engineer, Codex", "ai_native", True),
    ("Machine Learning Engineer (m/w/d)", "other", True),
    ("Senior Data Scientist", "other", True),
    ("Research Scientist, LLM", "other", True),
    ("Junior Software Engineer", "ai_native", True),
    ("Senior Backend Engineer", "ai_heavy", False),
    ("Senior Backend Engineer", "other", False),
    ("Electrical & Electronics Engineer", "ai_native", False),
    ("Team Assistant - Infrastructure", "ai_native", False),
    ("TEST - Forward Deployed Engineer - Alex", "ai_native", False),
    ("Manager, Senior Support Engineering", "ai_native", False),
    ("Account Executive, AI", "ai_native", False),
    ("Werkstudent AI Engineering", "other", False),
    ("Founding Engineer (early stage AI startup)", "other", True),
])
def test_is_relevant_title(title, tier, keep):
    assert is_relevant_title(title, tier) is keep


def test_to_iso_formats():
    assert to_iso("2026-09-22") == "2026-09-22"
    assert to_iso("2026-09-17T05:48:51-04:00") == "2026-09-17T09:48:51+00:00"
    assert to_iso("2026-03-12T16:38:15.322+00:00") == "2026-03-12T16:38:15+00:00"
    assert to_iso(1782214185805).startswith("2026-")
    assert to_iso("N/A") == "" and to_iso(None) == "" and to_iso("garbage") == ""


def test_keys_strip_noise():
    assert company_key("Helsing GmbH") == company_key("helsing")
    assert company_key("Fin") == company_key("Intercom")
    assert title_key("Machine Learning Engineer (m/w/d) - Berlin") == "machine learning engineer"
