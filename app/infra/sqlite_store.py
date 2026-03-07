from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import sqlite3
from typing import Any


@dataclass
class DeliveryRow:
    delivery_id: str
    event: str
    received_at: str
    repo_full_name: str
    comment_id: int | None
    status: str


class SqliteDeliveryStore:
    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS processed_deliveries (
                    delivery_id TEXT PRIMARY KEY,
                    event TEXT NOT NULL,
                    received_at TEXT NOT NULL,
                    repo_full_name TEXT NOT NULL,
                    comment_id INTEGER,
                    status TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_processed_deliveries_received_at
                ON processed_deliveries(received_at)
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS issue_sessions (
                    repo_full_name TEXT NOT NULL,
                    issue_number INTEGER NOT NULL,
                    session_name TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (repo_full_name, issue_number),
                    UNIQUE (session_name)
                )
                """
            )

    def insert_delivery_if_new(
        self,
        *,
        delivery_id: str,
        event: str,
        repo_full_name: str,
        comment_id: int | None,
        status: str,
        received_at: str | None = None,
    ) -> bool:
        timestamp = received_at or datetime.now(timezone.utc).isoformat()
        try:
            with self._connect() as conn:
                conn.execute(
                    """
                    INSERT INTO processed_deliveries (
                        delivery_id, event, received_at, repo_full_name, comment_id, status
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (delivery_id, event, timestamp, repo_full_name, comment_id, status),
                )
            return True
        except sqlite3.IntegrityError:
            return False

    def update_status(self, *, delivery_id: str, status: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE processed_deliveries SET status = ? WHERE delivery_id = ?",
                (status, delivery_id),
            )

    def get_delivery(self, delivery_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT delivery_id, event, received_at, repo_full_name, comment_id, status
                FROM processed_deliveries
                WHERE delivery_id = ?
                """,
                (delivery_id,),
            ).fetchone()

        return dict(row) if row else None

    def upsert_issue_session(self, *, repo_full_name: str, issue_number: int, session_name: str) -> None:
        timestamp = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO issue_sessions (
                    repo_full_name, issue_number, session_name, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(repo_full_name, issue_number)
                DO UPDATE SET
                    session_name = excluded.session_name,
                    updated_at = excluded.updated_at
                """,
                (repo_full_name, issue_number, session_name, timestamp, timestamp),
            )

    def get_issue_session_name(self, *, repo_full_name: str, issue_number: int) -> str | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT session_name
                FROM issue_sessions
                WHERE repo_full_name = ? AND issue_number = ?
                """,
                (repo_full_name, issue_number),
            ).fetchone()

        return str(row["session_name"]) if row else None
