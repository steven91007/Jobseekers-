"""Runs the jobseekers-mcp submodule's checks against this checkout.

The MCP server imports linkedin_scraper, visa, gitkb and bot.db from here, so a
change to those modules can break it. The checks patch modules and install a
process-wide tracer provider, so they run in their own interpreter.

Needs the submodule (git submodule update --init) and the package installed
(uv pip install -e ./jobseekers-mcp); skipped otherwise.
"""
import importlib.util, os, pathlib, subprocess, sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "jobseekers-mcp" / "tests" / "mcp_checks.py"


@pytest.mark.skipif(not SCRIPT.exists(), reason="submodule not checked out: git submodule update --init")
@pytest.mark.skipif(importlib.util.find_spec("mcp") is None,
                    reason="MCP SDK not installed: uv pip install -e ./jobseekers-mcp")
def test_mcp_server_against_this_checkout():
    env = dict(os.environ, JOBSEEKERS_ROOT=str(ROOT))
    r = subprocess.run([sys.executable, str(SCRIPT)], capture_output=True, text=True, timeout=300, env=env)
    assert r.returncode == 0, r.stdout[-4000:] + r.stderr[-2000:]
