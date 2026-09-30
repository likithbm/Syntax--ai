import logging

# Failure-path tests trigger server-side error logging on purpose; keep test output readable.
logging.getLogger("syntaxai").setLevel(logging.CRITICAL)
