"""Maintain a short-lived GitHub App credential in gh's private config."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import os
import subprocess
import sys
import time
from urllib.request import Request, urlopen


def refresh() -> int:
    request = Request(
        os.environ["OPERATIONS_BROKER_URL"].rstrip("/") + "/internal/v1/github/installation-token",
        data=b"", method="POST", headers={"X-Runner-Key": os.environ["OPERATIONS_RUNNER_BROKER_KEY"]},
    )
    with urlopen(request, timeout=30) as response:
        capability = json.loads(response.read())
    token = capability["token"]
    if not token or len(token) < 10:
        raise RuntimeError("Credential broker returned an invalid capability")
    subprocess.run(
        ["gh", "auth", "login", "--hostname", "github.com", "--git-protocol", "https", "--with-token"],
        input=token, text=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True,
    )
    expires_at = datetime.fromisoformat(capability["expires_at"].replace("Z", "+00:00"))
    return max(60, min(40 * 60, int((expires_at - datetime.now(timezone.utc)).total_seconds()) - 10 * 60))


def main() -> None:
    once = "--once" in sys.argv[1:]
    delay = 60
    while True:
        try:
            delay = refresh()
            if once:
                return
        except Exception as error:
            print("GitHub capability refresh failed: " + type(error).__name__, file=sys.stderr, flush=True)
            if once:
                raise SystemExit(1)
            delay = min(max(delay, 60) * 2, 10 * 60)
        time.sleep(delay)


if __name__ == "__main__":
    main()
