"""Small allowlisted projection of the pinned Symphony state snapshot."""

from __future__ import annotations

from datetime import datetime, timezone
import re

ISSUE = re.compile(r"^BOT-[0-9]{1,8}$")
STAMP = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$")


def _stamp(value: object) -> str | None:
    if not isinstance(value, str) or not STAMP.fullmatch(value):
        return None
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return value


def _nonnegative(value: object) -> int | None:
    return value if type(value) is int and 0 <= value <= 1_000_000_000 else None


def _rows(value: object, kind: str) -> list[dict]:
    if not isinstance(value, list):
        return []
    result = []
    for row in value[:100]:
        if not isinstance(row, dict):
            continue
        identifier = row.get("issue_identifier")
        if not isinstance(identifier, str) or not ISSUE.fullmatch(identifier):
            continue
        item = {"issue": identifier,
                "issue_url": f"https://life2launch.atlassian.net/browse/{identifier}",
                "state": kind}
        if kind == "running":
            item.update(started_at=_stamp(row.get("started_at")),
                        last_event_at=_stamp(row.get("last_event_at")),
                        turns=_nonnegative(row.get("turn_count")))
        elif kind == "retrying":
            item.update(due_at=_stamp(row.get("due_at")), attempt=_nonnegative(row.get("attempt")),
                        reason="Retry scheduled" if row.get("error") else "Continuation scheduled")
        else:
            item.update(blocked_at=_stamp(row.get("blocked_at")),
                        reason="Agent blocked; inspect the delivery issue")
        result.append(item)
    return result


def sanitized_status(raw: object) -> dict:
    if (not isinstance(raw, dict) or "error" in raw
            or any(not isinstance(raw.get(key), list) for key in ("running", "retrying", "blocked"))):
        raise ValueError("Invalid Symphony status response")
    observed_at = datetime.now(timezone.utc).isoformat()
    generated_at = _stamp(raw.get("generated_at"))
    stale = True
    if generated_at:
        generated = datetime.fromisoformat(generated_at.replace("Z", "+00:00"))
        stale = abs((datetime.now(timezone.utc) - generated).total_seconds()) > 120
    running = _rows(raw.get("running"), "running")
    retrying = _rows(raw.get("retrying"), "retrying")
    blocked = _rows(raw.get("blocked"), "blocked")
    return {"availability": "stale" if stale else "live", "generated_at": generated_at,
            "observed_at": observed_at, "running": running, "retrying": retrying,
            "blocked": blocked,
            "counts": {"running": len(running), "retrying": len(retrying), "blocked": len(blocked)}}
