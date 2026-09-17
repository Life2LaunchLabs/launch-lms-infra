#!/usr/bin/env python3
"""Read-only FEED ownership inventory; emit issue keys and categories, never identities."""

from __future__ import annotations

from collections import defaultdict
import json
import os
import re
import sys

ISSUE_KEY = re.compile(r"^FEED-[1-9][0-9]*$")


def all_feed_issues(client) -> list[dict]:
    if client.project != "FEED":
        raise ValueError("Expected the FEED project")
    issues: list[dict] = []
    token = None
    seen_tokens: set[str] = set()
    while True:
        payload = {"jql": 'project = "FEED" ORDER BY key ASC', "fields": ["labels"], "maxResults": 100}
        if token:
            payload["nextPageToken"] = token
        page = client._request("POST", "/rest/api/3/search/jql", payload)
        if not isinstance(page, dict) or not isinstance(page.get("issues"), list):
            raise ValueError("Jira returned an invalid search page")
        issues.extend(page["issues"])
        if page.get("isLast") is True:
            break
        token = page.get("nextPageToken")
        if not isinstance(token, str) or not token or token in seen_tokens:
            raise ValueError("Jira search pagination was incomplete")
        seen_tokens.add(token)
    keys = [issue.get("key") for issue in issues]
    if len(keys) != len(set(keys)) or any(not isinstance(key, str) or not ISSUE_KEY.fullmatch(key) for key in keys):
        raise ValueError("Jira returned duplicate or invalid FEED issue keys")
    return issues


def classify(issue: dict, legacy: object, user: object, org: object) -> list[str]:
    labels = set(issue.get("fields", {}).get("labels") or [])
    if not isinstance(legacy, dict):
        return ["labeled_without_legacy_property"] if "launchlms-feedback" in labels else []
    reasons = []
    if "launchlms-feedback" not in labels:
        reasons.append("legacy_property_without_feedback_label")
    if legacy.get("source") != "unstable":
        reasons.append("source_not_unstable")
    user_id, org_id = legacy.get("user_id"), legacy.get("org_id")
    if type(user_id) is not int or type(org_id) is not int:
        reasons.append("invalid_owner_ids")
        return reasons
    if f"launchlms-org-{org_id}" not in labels:
        reasons.append("organization_label_mismatch")
    if not user:
        reasons.append("user_row_missing")
    elif not user.user_uuid:
        reasons.append("user_uuid_missing")
    elif legacy.get("user_uuid") != user.user_uuid:
        reasons.append("legacy_user_uuid_mismatch")
    if not org:
        reasons.append("organization_row_missing")
    elif not org.org_uuid:
        reasons.append("organization_uuid_missing")
    return reasons


def inventory(client, session) -> dict:
    from sqlmodel import select
    from src.db.organizations import Organization
    from src.db.users import User
    from src.services.candidate_jira import FEEDBACK_PROPERTY

    issues = all_feed_issues(client)
    categories: dict[str, list[str]] = defaultdict(list)
    labeled = legacy_count = 0
    for issue in issues:
        key = issue["key"]
        labels = set(issue.get("fields", {}).get("labels") or [])
        labeled += "launchlms-feedback" in labels
        legacy = client.property(key, FEEDBACK_PROPERTY)
        legacy_count += isinstance(legacy, dict)
        user = org = None
        if isinstance(legacy, dict) and type(legacy.get("user_id")) is int and type(legacy.get("org_id")) is int:
            user = session.exec(select(User).where(User.id == legacy["user_id"])).first()
            org = session.exec(select(Organization).where(Organization.id == legacy["org_id"])).first()
        for reason in classify(issue, legacy, user, org):
            categories[reason].append(key)
    return {
        "feed_issues": len(issues),
        "feedback_labeled": labeled,
        "legacy_properties": legacy_count,
        "diagnostics": {category: {"count": len(keys), "issue_keys": keys} for category, keys in sorted(categories.items())},
    }


def main() -> int:
    try:
        from sqlalchemy import create_engine
        from sqlmodel import Session

        sys.path.insert(0, "/app/api")
        from src.services.candidate_jira import CandidateJira

        database_url = os.getenv("LAUNCHLMS_SQL_CONNECTION_STRING") or os.getenv("DATABASE_URL")
        if not database_url:
            raise ValueError("Application database connection is unavailable")
        client = CandidateJira()
        if not client.configured or client.project != "FEED":
            raise ValueError("FEED Jira connection is unavailable")
        with Session(create_engine(database_url, pool_pre_ping=True)) as session:
            print(json.dumps(inventory(client, session), sort_keys=True))
        return 0
    except Exception as error:
        detail = str(error) if isinstance(error, ValueError) else "connection or tracker operation failed"
        print(f"Read-only FEED diagnosis stopped: {type(error).__name__}: {detail}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
