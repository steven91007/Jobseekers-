"""Live checks of the keyless sources. Opt in with JOBAGENT_LIVE=1 (they hit the real sites).

    JOBAGENT_LIVE=1 .venv/bin/python -m pytest tests/test_jobagent_live_sources.py -v

Same checks as `python -m jobagent sources check`.
"""

import os

import pytest

from jobagent import harness

pytestmark = pytest.mark.skipif(os.getenv("JOBAGENT_LIVE") != "1", reason="set JOBAGENT_LIVE=1 to hit live sites")


@pytest.mark.parametrize("name", list(harness.CHECKS))
def test_source_finds_jobs(name):
    check = harness.run([name], progress=lambda m: None)[0]
    assert check.ok, check.summary
