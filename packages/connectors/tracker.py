"""Tracker boundary. Services receive capabilities, never raw credential values."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Iterable


@dataclass(frozen=True)
class TrackerIssue:
    key: str
    summary: str
    status: str
    revision: str
    properties: dict


class TrackerAdapter(ABC):
    @abstractmethod
    async def create_issue(self, project: str, summary: str, description: dict, properties: dict, idempotency_key: str) -> TrackerIssue: ...

    @abstractmethod
    async def get_issue(self, key: str) -> TrackerIssue: ...

    @abstractmethod
    async def comments(self, key: str) -> list[dict]: ...

    @abstractmethod
    async def add_comment(self, key: str, body: dict, public: bool) -> dict: ...

    @abstractmethod
    async def attach(self, key: str, filename: str, content_type: str, stream: BinaryIO) -> dict: ...

    @abstractmethod
    async def transition(self, key: str, status: str) -> None: ...

    @abstractmethod
    async def set_properties(self, key: str, properties: dict) -> None: ...
