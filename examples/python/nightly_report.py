"""Light a lamp around any Python code with ``agent_status()``.

Run it with ``python nightly_report.py``; then ``lamptown status list`` shows it.
"""

import time

from lamptown.status import agent_status

STEPS = ["fetch tide tables", "draw charts", "write summary"]

with agent_status("nightly-report", "Nightly report", agent="cron", kind="job") as status:
    for number, step in enumerate(STEPS, start=1):
        status.message(step.capitalize())
        status.progress(number - 1, len(STEPS))
        time.sleep(0.5)  # the real work goes here
    status.progress(len(STEPS), len(STEPS))
# a clean exit writes "ok"; an exception writes "error" with its message and re-raises
