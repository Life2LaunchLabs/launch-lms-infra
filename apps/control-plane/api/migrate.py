"""Apply immutable SQL migrations before starting the API."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

import psycopg


def migrate(connection: psycopg.Connection, directory: Path) -> None:
    with connection.transaction():
        connection.execute("SELECT pg_advisory_xact_lock(7062901)")
        connection.execute("""CREATE TABLE IF NOT EXISTS schema_migrations (
            version varchar(100) PRIMARY KEY, sha256 char(64) NOT NULL,
            applied_at timestamptz NOT NULL DEFAULT now())""")
        for file in sorted(directory.glob("[0-9][0-9][0-9]-*.sql")):
            content = file.read_bytes()
            digest = hashlib.sha256(content).hexdigest()
            row = connection.execute(
                "SELECT sha256 FROM schema_migrations WHERE version = %s", (file.name,)
            ).fetchone()
            if row:
                if row[0] != digest:
                    raise ValueError(f"Migration checksum changed: {file.name}")
                continue
            connection.execute(content.decode("utf-8"))
            connection.execute(
                "INSERT INTO schema_migrations (version, sha256) VALUES (%s, %s)",
                (file.name, digest),
            )


if __name__ == "__main__":
    url = os.environ["OPERATIONS_DATABASE_URL"].replace("postgresql+psycopg://", "postgresql://", 1)
    with psycopg.connect(url) as database:
        migrate(database, Path("/opt/launch-operations/postgres"))
    print("Operations schema migrations are current.")
