# test_pusher.py is a standalone script (python tests/test_pusher.py) that exits at
# import time, so pytest must not collect it.
collect_ignore = ["test_pusher.py"]
