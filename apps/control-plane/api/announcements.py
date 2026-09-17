"""Operator-published, project/environment-scoped announcements."""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from auth import require_operator
from embed import database_session, require_embed_session
from models import Announcement, EmbedSession
from services.orchestrator.manifest import load_project


router = APIRouter()


class AnnouncementDraft(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    body: str = Field(min_length=1, max_length=10_000)


def _scope(project_id: str, environment: str) -> None:
    try:
        manifest = load_project(project_id)
    except (ValueError, FileNotFoundError) as error:
        raise HTTPException(404, "Unknown project") from error
    if not manifest.data["modules"].get("announcements") or environment not in manifest.data["environments"]:
        raise HTTPException(404, "Announcements unavailable for this environment")


def _serialize(item: Announcement) -> dict:
    return {
        "id": item.id, "title": item.title, "body": item.body,
        "published_at": item.published_at.isoformat() if item.published_at else None,
    }


def published(session: Session, project_id: str, environment: str) -> list[dict]:
    now = datetime.now(timezone.utc)
    items = session.scalars(select(Announcement).where(
        Announcement.project_id == project_id, Announcement.environment == environment,
        Announcement.published_at.is_not(None), Announcement.published_at <= now,
    ).order_by(Announcement.published_at.desc(), Announcement.id.desc())).all()
    return [_serialize(item) for item in items]


@router.get("/api/v1/embed/announcements")
def embed_announcements(
    identity: EmbedSession = Depends(require_embed_session),
    session: Session = Depends(database_session),
) -> list[dict]:
    _scope(identity.project_id, identity.environment)
    return published(session, identity.project_id, identity.environment)


@router.get("/api/v1/projects/{project_id}/environments/{environment}/announcements")
def operator_announcements(
    project_id: str, environment: str,
    _: dict = Depends(require_operator), session: Session = Depends(database_session),
) -> list[dict]:
    _scope(project_id, environment)
    items = session.scalars(select(Announcement).where(
        Announcement.project_id == project_id, Announcement.environment == environment,
    ).order_by(Announcement.created_at.desc(), Announcement.id.desc())).all()
    return [_serialize(item) for item in items]


@router.post("/api/v1/projects/{project_id}/environments/{environment}/announcements", status_code=201)
def draft_announcement(
    project_id: str, environment: str, payload: AnnouncementDraft,
    _: dict = Depends(require_operator), session: Session = Depends(database_session),
) -> dict:
    _scope(project_id, environment)
    title, body = payload.title.strip(), payload.body.strip()
    if not title or not body:
        raise HTTPException(422, "Announcement title and body are required")
    item = Announcement(project_id=project_id, environment=environment, title=title, body=body)
    session.add(item); session.commit(); session.refresh(item)
    return _serialize(item)


@router.post("/api/v1/projects/{project_id}/environments/{environment}/announcements/{announcement_id}/publish")
def publish_announcement(
    project_id: str, environment: str, announcement_id: str,
    _: dict = Depends(require_operator), session: Session = Depends(database_session),
) -> dict:
    _scope(project_id, environment)
    item = session.scalar(select(Announcement).where(
        Announcement.id == announcement_id, Announcement.project_id == project_id,
        Announcement.environment == environment,
    ))
    if item is None:
        raise HTTPException(404, "Announcement not found")
    if item.published_at is None:
        item.published_at = datetime.now(timezone.utc)
        session.add(item); session.commit(); session.refresh(item)
    return _serialize(item)
