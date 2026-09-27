# These are standalone scripts (python tests/<name>.py) that exit at import time,
# so pytest must not collect them. Run them directly.
collect_ignore = ["test_pusher.py", "test_visa.py"]
