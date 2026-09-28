# test_pusher.py and test_visa.py are standalone scripts (python tests/<name>.py) that
# exit at import time, so pytest must not collect them. mcp_checks.py is also a
# script; pytest runs it in a subprocess through test_mcp.py.
collect_ignore = ["test_pusher.py", "test_visa.py", "mcp_checks.py"]
