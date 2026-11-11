"""Hold an unstable redeploy while the site says it would cut something off.

Asks the running app GET /api/v1/health/deploy-ready every INTERVAL seconds, from inside
its container (no gate or secrets). The site decides what blocks and for how long (today:
a started user test run, until it closes, at most 3 hours). MAX_WAIT is only a backstop in
case the site keeps saying no.

The container is found by its compose labels and asked with `docker exec`, like the
deploy observation does: `docker compose` here has none of the release environment that
deploy.sh loads, so it can't even read the project.

What happens when the answer isn't a plain yes or no:
- the app isn't running, isn't answering, or answers with a server error: there is nothing
  to cut off, so the deploy goes ahead (it is also what brings the site back);
- an older image without the endpoint (404): the deploy goes ahead;
- the host can't look at all (docker fails, a garbled answer): after a few tries the hold
  fails, so the deploy doesn't run and the run shows red instead of deploying blind.
"""

import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path("/opt/launch-lms")
INTERVAL = 300
MAX_WAIT = 4 * 60 * 60
RETRIES = 3
RETRY_PAUSE = 30
URL = "http://localhost/api/v1/health/deploy-ready"


class CantLook(Exception):
    """The host couldn't ask the site at all; not the same as the site being down."""


def run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(args, cwd=ROOT, capture_output=True, text=True, timeout=60)


def app_container(run=run) -> str | None:
    found = run(
        "docker", "ps", "--filter", "label=com.docker.compose.project=launch-lms",
        "--filter", "label=com.docker.compose.service=launch-lms", "--format", "{{.ID}}",
    )
    if found.returncode != 0:
        raise CantLook(f"docker ps failed: {found.stderr.strip() or found.returncode}")
    ids = found.stdout.split()
    if len(ids) > 1:
        raise CantLook(f"expected one app container, found {len(ids)}")
    return ids[0] if ids else None


def ask_site(run=run) -> tuple[str, dict | str]:
    """One look: ("answer", {ready, reasons}), ("go", why), or raises CantLook."""
    container = app_container(run)
    if container is None:
        return "go", "the app isn't running"
    out = run("docker", "exec", container, "curl", "-sS", "--max-time", "10", "-w", r"\n%{http_code}", URL)
    body, _, code = out.stdout.rstrip("\n").rpartition("\n")
    if not (len(code) == 3 and code.isdigit()):
        raise CantLook(f"docker exec failed: {out.stderr.strip() or out.returncode}")
    if code == "000":
        return "down", f"the site isn't answering ({out.stderr.strip() or 'no response'})"
    if code == "404":
        return "go", "this image has no deploy-ready check"
    if code != "200":
        return "down", f"the site answered {code}"
    try:
        value = json.loads(body)
    except ValueError:
        raise CantLook(f"the site's answer isn't JSON: {body[:200]!r}")
    if not isinstance(value, dict) or not isinstance(value.get("ready"), bool):
        raise CantLook(f"the site's answer has no ready flag: {body[:200]!r}")
    return "answer", value


def look(ask=ask_site, sleep=time.sleep, retries: int = RETRIES, pause: int = RETRY_PAUSE) -> tuple[str, dict | str]:
    """Ask, trying again a few times when the site is down or the host can't look."""
    for attempt in range(1, retries + 1):
        try:
            kind, value = ask()
        except CantLook as problem:
            if attempt == retries:
                raise
            print(f"Couldn't ask the site ({problem}); trying again", flush=True)
        else:
            if kind != "down":
                return kind, value
            if attempt == retries:
                return "go", value
            print(f"{value}; trying again", flush=True)
        sleep(pause)
    raise AssertionError("unreachable")


def describe(reasons) -> str:
    parts = []
    for reason in reasons if isinstance(reasons, list) else []:
        if isinstance(reason, dict):
            label = reason.get("run") or reason.get("kind") or "unknown"
            until = reason.get("holds_until")
            parts.append(f"{label} (until {until})" if until else str(label))
    return ", ".join(parts) or "no reason given"


def wait(ask=ask_site, sleep=time.sleep, clock=time.monotonic, interval: int = INTERVAL, max_wait: int = MAX_WAIT,
         retries: int = RETRIES, pause: int = RETRY_PAUSE) -> str:
    start = clock()
    while True:
        kind, state = look(ask, sleep, retries, pause)
        if kind == "go":
            return f"{state}; not waiting"
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
    try:
        print(wait(), flush=True)
    except CantLook as problem:
        sys.exit(f"Couldn't ask unstable whether it's ready to redeploy ({problem}), so it isn't deploying. "
                 "Fix the check and run the deploy again.")
