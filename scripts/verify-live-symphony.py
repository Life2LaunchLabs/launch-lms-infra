#!/usr/bin/env python3
"""Print only the sanitized status projection from a private Symphony snapshot."""

from __future__ import annotations

import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps/control-plane/api"))
from orchestration import sanitized_status  # noqa: E402


def main() -> None:
    safe = sanitized_status(json.load(sys.stdin))
    if safe["availability"] != "live":
        raise SystemExit("Symphony snapshot is stale")
    print(f"Symphony snapshot: {safe['generated_at']}")
    for state in ("running", "retrying", "blocked"):
        print(f"{state}: {safe['counts'][state]}")
        for row in safe[state]:
            print(f"  {row['issue']} {row['issue_url']}")


if __name__ == "__main__":
    main()
