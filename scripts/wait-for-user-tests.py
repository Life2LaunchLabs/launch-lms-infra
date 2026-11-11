"""Hold an unstable redeploy while a user test is active, so a run isn't cut off mid-way.

Asks the running app (GET /api/v1/user_tests/active, from inside its container) every
INTERVAL seconds and returns once no persona of an open run has been active for a while,
or after MAX_WAIT so a run left open can never block unstable. Any trouble asking (an
older image without the endpoint, the app down) means don't wait: the deploy goes ahead.
"""

import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path("/opt/launch-lms")
INTERVAL = 300
MAX_WAIT = 3 * 60 * 60
ASK = (
    "import urllib.request;"
    "print(urllib.request.urlopen('http://127.0.0.1/api/v1/user_tests/active', timeout=10).read().decode())"
)


def active() -> bool | None:
    """True or False from the app; None when it couldn't say."""
    try:
        out = subprocess.run(
            ["docker", "compose", "exec", "-T", "launch-lms", "python3", "-c", ASK],
            cwd=ROOT, capture_output=True, text=True, timeout=60,
        )
        if out.returncode != 0:
            return None
        value = json.loads(out.stdout.strip().splitlines()[-1])
        return bool(value["active"]) if isinstance(value, dict) and "active" in value else None
    except (OSError, ValueError, KeyError, IndexError, subprocess.SubprocessError):
        return None


def wait(ask=active, sleep=time.sleep, clock=time.monotonic, interval: int = INTERVAL, max_wait: int = MAX_WAIT) -> str:
    start = clock()
    while True:
        state = ask()
        if state is not True:
            return "no test active" if state is False else "couldn't ask the app; not waiting"
        waited = clock() - start
        if waited >= max_wait:
            return f"a test is still active after {int(waited // 60)} minutes; deploying anyway"
        print(f"A user test is active on unstable; holding the deploy (waited {int(waited // 60)} min).", flush=True)
        sleep(interval)


if __name__ == "__main__":
    if (ROOT / ".deployment-environment").read_text().strip() != "unstable":
        sys.exit("Only unstable deploys wait for user tests")
    print(wait(), flush=True)
