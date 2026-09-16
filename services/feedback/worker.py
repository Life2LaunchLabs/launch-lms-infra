"""Least-privilege feedback outbox worker."""

from __future__ import annotations

import asyncio
import os

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from packages.connectors.jira import JiraAdapter, JiraCredentials
from services.feedback.sync import synchronize_pending
from services.orchestrator.manifest import load_project


async def run() -> None:
    from models import Project

    engine = create_engine(os.environ["OPERATIONS_DATABASE_URL"], pool_pre_ping=True)
    manifest = load_project(os.getenv("OPERATIONS_PROJECT", "launch-lms"))
    adapter = JiraAdapter(JiraCredentials(
        os.environ["JIRA_BASE_URL"], os.environ["JIRA_FEEDBACK_EMAIL"], os.environ["JIRA_FEEDBACK_TOKEN"],
    ))
    with Session(engine) as session:
        if not session.get(Project, manifest.project_id):
            raise RuntimeError("project must be registered by the control-plane API before feedback sync starts")
    interval = max(5, int(os.getenv("FEEDBACK_SYNC_INTERVAL_SECONDS", "15")))
    while True:
        await synchronize_pending(engine, adapter, manifest.data)
        await asyncio.sleep(interval)


if __name__ == "__main__":
    asyncio.run(run())
