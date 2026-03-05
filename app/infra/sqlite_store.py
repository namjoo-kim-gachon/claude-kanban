from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
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
        timestamp = received_at or datetime.now(UTC).isoformat()
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
