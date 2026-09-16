"""Normalized operational state; Jira and product repositories remain canonical."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def now() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Project(Base):
    __tablename__ = "projects"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    manifest_revision: Mapped[str] = mapped_column(String(64), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)


class EmbedSession(Base):
    __tablename__ = "embed_sessions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), nullable=False)
    environment: Mapped[str] = mapped_column(String(32), nullable=False)
    opaque_user_id: Mapped[str] = mapped_column(String(128), nullable=False)
    opaque_org_id: Mapped[str] = mapped_column(String(128), nullable=False)
    nonce_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class UnreadMarker(Base):
    __tablename__ = "unread_markers"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), nullable=False)
    environment: Mapped[str] = mapped_column(String(32), nullable=False)
    opaque_user_id: Mapped[str] = mapped_column(String(128), nullable=False)
    conversation_id: Mapped[str] = mapped_column(String(128), nullable=False)
    seen_revision: Mapped[str] = mapped_column(String(128), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)
    __table_args__ = (UniqueConstraint("project_id", "environment", "opaque_user_id", "conversation_id"),)


class SyncCursor(Base):
    __tablename__ = "sync_cursors"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), nullable=False)
    adapter: Mapped[str] = mapped_column(String(64), nullable=False)
    stream: Mapped[str] = mapped_column(String(128), nullable=False)
    cursor: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)
    __table_args__ = (UniqueConstraint("project_id", "adapter", "stream"),)


class IdempotencyRecord(Base):
    __tablename__ = "idempotency_records"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), nullable=False)
    operation: Mapped[str] = mapped_column(String(64), nullable=False)
    key_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    result: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)
    __table_args__ = (UniqueConstraint("project_id", "operation", "key_hash"),)


class AgentRun(Base):
    __tablename__ = "agent_runs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), nullable=False)
    issue_key: Mapped[str] = mapped_column(String(64), nullable=False)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False)
    workspace: Mapped[str] = mapped_column(String(255), nullable=False)
    policy_revision: Mapped[str] = mapped_column(String(64), nullable=False)
    workflow_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    base_sha: Mapped[str] = mapped_column(String(40), nullable=False)
    head_sha: Mapped[str | None] = mapped_column(String(40))
    turns: Mapped[int] = mapped_column(Integer, default=0)
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    checks: Mapped[dict] = mapped_column(JSON, default=dict)
    evidence: Mapped[dict] = mapped_column(JSON, default=dict)
    deployment_result: Mapped[dict | None] = mapped_column(JSON)
    retry_reason: Mapped[str | None] = mapped_column(Text)
    disposition: Mapped[str] = mapped_column(String(32), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (UniqueConstraint("project_id", "issue_key", "attempt"), Index("agent_run_issue", "issue_key"),)


class DeploymentObservation(Base):
    __tablename__ = "deployment_observations"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), nullable=False)
    environment: Mapped[str] = mapped_column(String(32), nullable=False)
    source_sha: Mapped[str] = mapped_column(String(40), nullable=False)
    image_digest: Mapped[str] = mapped_column(String(80), nullable=False)
    workflow_run_id: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Announcement(Base):
    __tablename__ = "announcements"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), nullable=False)
    environment: Mapped[str] = mapped_column(String(32), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
