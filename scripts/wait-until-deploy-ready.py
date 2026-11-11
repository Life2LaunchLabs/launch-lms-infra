"""Hold an unstable redeploy while the site says it would cut something off.

Asks the running app GET /api/v1/health/deploy-ready (from inside its container, so no
gate or secrets) every INTERVAL seconds. The site decides what blocks and for how long
(today: a started user test run, until it closes, at most 3 hours). MAX_WAIT is only a
backstop in case the site keeps saying no. Any trouble asking (an older image without the
endpoint, the app down) means don't wait: the deploy goes ahead.
"""

import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path("/opt/launch-lms")
INTERVAL = 300
MAX_WAIT = 4 * 60 * 60
ASK = (
    "import urllib.request;"
    "print(urllib.request.urlopen('http://127.0.0.1/api/v1/health/deploy-ready', timeout=10).read().decode())"
)


def ask_site() -> dict | None:
    """The site's {ready, reasons}, or None when it couldn't say."""
    try:
        out = subprocess.run(
            ["docker", "compose", "exec", "-T", "launch-lms", "python3", "-c", ASK],
            cwd=ROOT, capture_output=True, text=True, timeout=60,
        )
        if out.returncode != 0:
            return None
        value = json.loads(out.stdout.strip().splitlines()[-1])
        return value if isinstance(value, dict) and isinstance(value.get("ready"), bool) else None
    except (OSError, ValueError, IndexError, subprocess.SubprocessError):
        return None


def describe(reasons) -> str:
    parts = []
    for reason in reasons if isinstance(reasons, list) else []:
        if isinstance(reason, dict):
            label = reason.get("run") or reason.get("kind") or "unknown"
            until = reason.get("holds_until")
            parts.append(f"{label} (until {until})" if until else str(label))
    return ", ".join(parts) or "no reason given"


def wait(ask=ask_site, sleep=time.sleep, clock=time.monotonic, interval: int = INTERVAL, max_wait: int = MAX_WAIT) -> str:
    start = clock()
    while True:
        state = ask()
        if state is None:
            return "couldn't ask the site; not waiting"
        if state["ready"]:
            return "site is ready"
        waited = clock() - start
        if waited >= max_wait:
            return f"site still not ready after {int(waited // 60)} minutes ({describe(state.get('reasons'))}); deploying anyway"
        print(f"Holding the deploy, waited {int(waited // 60)} min: {describe(state.get('reasons'))}", flush=True)
        sleep(interval)


if __name__ == "__main__":
    if (ROOT / ".deployment-environment").read_text().strip() != "unstable":
        sys.exit("Only unstable deploys wait for the site")
    print(wait(), flush=True)
