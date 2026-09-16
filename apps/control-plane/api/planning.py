"""Repository planning and correlated operator-dashboard routes."""

from __future__ import annotations

from collections import Counter
import asyncio

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from auth import require_operator
from config import Settings
from models import AgentRun, Announcement, DeploymentObservation, IdempotencyRecord
from packages.connectors.github import GitHubAppCredentials
from packages.connectors.jira import JiraAdapter, JiraCredentials
from services.orchestrator.manifest import load_project
from services.planning.repository import RepositoryPlanning


router = APIRouter(prefix="/api/v1/projects/{project_id}", tags=["planning"])


class PlanningProposal(BaseModel):
    path: str
    content: str
    expected_blob_sha: str
    base_sha: str
    title: str = Field(min_length=3, max_length=120)
    reason: str = Field(min_length=3, max_length=5_000)


async def planning_service(request: Request, project_id: str) -> RepositoryPlanning:
    settings: Settings = request.app.state.settings
    if not settings.github_app_id or not settings.github_installation_id or not settings.github_app_private_key:
        raise HTTPException(503, "Repository planning is not configured")
    credentials = GitHubAppCredentials(
        settings.github_app_id, settings.github_installation_id, settings.github_app_private_key,
    )
    return RepositoryPlanning(await credentials.connector(), load_project(project_id))


def translated(error: Exception) -> HTTPException:
    if isinstance(error, KeyError):
        return HTTPException(404, "Repository planning document was not found")
    if isinstance(error, (ValueError, TypeError)):
        return HTTPException(422, str(error))
    if isinstance(error, RuntimeError):
        return HTTPException(409, str(error))
    if isinstance(error, httpx.HTTPStatusError) and error.response.status_code == 404:
        return HTTPException(404, "Repository planning document was not found")
    return HTTPException(502, "Repository planning provider is unavailable")


@router.get("/planning/product-map")
async def product_map(request: Request, project_id: str, _: dict = Depends(require_operator)) -> dict:
    try:
        return await (await planning_service(request, project_id)).catalog()
    except Exception as error:
        raise translated(error) from error


@router.get("/planning/product-map/{group_id}")
async def product_group(request: Request, project_id: str, group_id: str, _: dict = Depends(require_operator)) -> dict:
    try:
        return await (await planning_service(request, project_id)).group(group_id)
    except Exception as error:
        raise translated(error) from error


@router.get("/planning/design")
async def design_index(request: Request, project_id: str, _: dict = Depends(require_operator)) -> dict:
    try:
        return await (await planning_service(request, project_id)).design_index()
    except Exception as error:
        raise translated(error) from error


@router.post("/planning/proposals", status_code=201)
async def propose_planning_edit(
    request: Request, project_id: str, payload: PlanningProposal, operator: dict = Depends(require_operator),
) -> dict:
    try:
        service = await planning_service(request, project_id)
        reason = f"{payload.reason.strip()}\n\nRequested by GitHub operator `{operator['login']}`."
        return await service.propose_edit(
            path=payload.path, content=payload.content, expected_blob_sha=payload.expected_blob_sha,
            base_sha=payload.base_sha, title=payload.title, reason=reason,
        )
    except Exception as error:
        raise translated(error) from error


async def tracker_summary(settings: Settings, project: str, delivery: bool) -> dict:
    email = settings.jira_delivery_email if delivery else settings.jira_feedback_email
    token = settings.jira_delivery_token if delivery else settings.jira_feedback_token
    if not settings.jira_base_url or not email or not token:
        raise RuntimeError("tracker credentials are not configured")
    issues = await JiraAdapter(JiraCredentials(settings.jira_base_url, email, token)).search(project, "status is not EMPTY")
    statuses = Counter(issue.status for issue in issues)
    return {"state": "available", "total": len(issues), "by_status": dict(sorted(statuses.items())),
            "recent": [{"key": issue.key, "summary": issue.summary, "status": issue.status,
                        "revision": issue.revision} for issue in issues[:10]]}


async def unavailable(error: Exception):
    raise error


def operational_summary(request: Request, project_id: str) -> dict:
    engine = request.app.state.engine
    if engine is None:
        return {"state": "unavailable", "reason": "Operational storage is unavailable"}
    with Session(engine) as session:
        runs = list(session.scalars(select(AgentRun).where(
            AgentRun.project_id == project_id,
        ).order_by(AgentRun.started_at.desc()).limit(10)))
        deployments = list(session.scalars(select(DeploymentObservation).where(
            DeploymentObservation.project_id == project_id,
        ).order_by(DeploymentObservation.observed_at.desc()).limit(10)))
        pending_feedback = session.scalar(select(func.count()).select_from(IdempotencyRecord).where(
            IdempotencyRecord.project_id == project_id, IdempotencyRecord.operation == "feedback.create",
            IdempotencyRecord.status == "pending",
        )) or 0
        announcements = session.scalar(select(func.count()).select_from(Announcement).where(
            Announcement.project_id == project_id,
        )) or 0
    return {
        "state": "available", "pending_feedback": pending_feedback, "announcement_count": announcements,
        "runs": [{"issue_key": value.issue_key, "attempt": value.attempt, "head_sha": value.head_sha,
                  "turns": value.turns, "duration_ms": value.duration_ms, "disposition": value.disposition,
                  "started_at": value.started_at.isoformat()} for value in runs],
        "deployments": [{"environment": value.environment, "source_sha": value.source_sha,
                         "image_digest": value.image_digest, "status": value.status,
                         "observed_at": value.observed_at.isoformat()} for value in deployments],
    }


@router.get("/dashboard")
async def dashboard(request: Request, project_id: str, _: dict = Depends(require_operator)) -> dict:
    manifest = load_project(project_id)
    settings: Settings = request.app.state.settings
    try:
        service = await planning_service(request, project_id)
        product_task = service.summary()
        ci_task = service.connector.workflow_runs(manifest.repository, manifest.data["source"]["delivery_branch"])
    except Exception as error:
        product_task = unavailable(error)
        ci_task = unavailable(error)
    delivery_task = tracker_summary(settings, manifest.data["tracker"]["delivery_project"], True)
    feedback_task = tracker_summary(settings, manifest.data["tracker"]["feedback_project"], False)
    values = await asyncio.gather(product_task, ci_task, delivery_task, feedback_task, return_exceptions=True)

    def module(value, transform=lambda item: item):
        if isinstance(value, Exception):
            return {"state": "unavailable", "reason": type(value).__name__}
        return transform(value)

    product = module(values[0], lambda value: {
        "state": "available", "source_revision": value["source_revision"], "fetched_at": value["fetched_at"],
        "counts": value["migration"]["expected_counts"],
    })
    ci = module(values[1], lambda runs: {
        "state": "available", "recent": [{"id": str(value["id"]), "name": value.get("name"),
                                             "status": value.get("conclusion") or value.get("status"),
                                             "head_sha": value.get("head_sha"), "url": value.get("html_url")}
                                            for value in runs[:10]],
    })
    return {"project_id": project_id, "repository": manifest.repository, "product": product,
            "delivery": module(values[2]), "feedback": module(values[3]), "ci": ci,
            "operations": operational_summary(request, project_id)}
