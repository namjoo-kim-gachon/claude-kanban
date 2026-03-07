from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

import pytest

from app.infra.sqlite_store import SqliteDeliveryStore


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def test_insert_delivery_accepts_first_event(settings) -> None:
    store = SqliteDeliveryStore(settings.sqlite_path)

    inserted = store.insert_delivery_if_new(
        delivery_id="delivery-1",
        event="issue_comment",
        repo_full_name="namjookim/claude-kanban",
        comment_id=1,
        status="accepted",
    )

    assert inserted is True


def test_insert_delivery_rejects_duplicate(settings) -> None:
    store = SqliteDeliveryStore(settings.sqlite_path)

    first = store.insert_delivery_if_new(
        delivery_id="delivery-dup",
        event="issue_comment",
        repo_full_name="namjookim/claude-kanban",
        comment_id=2,
        status="accepted",
    )
    second = store.insert_delivery_if_new(
        delivery_id="delivery-dup",
        event="issue_comment",
        repo_full_name="namjookim/claude-kanban",
        comment_id=2,
        status="accepted",
    )

    assert first is True
    assert second is False


def test_update_status_persists_transition(settings) -> None:
    store = SqliteDeliveryStore(settings.sqlite_path)
    delivery_id = "delivery-status"
    store.insert_delivery_if_new(
        delivery_id=delivery_id,
        event="issue_comment",
        repo_full_name="namjookim/claude-kanban",
        comment_id=3,
        status="accepted",
    )

    store.update_status(delivery_id=delivery_id, status="processed")

    row = store.get_delivery(delivery_id)
    assert row is not None
    assert row["status"] == "processed"


def test_insert_is_atomic_with_unique_constraint(settings) -> None:
    store = SqliteDeliveryStore(settings.sqlite_path)
    delivery_id = "delivery-atomic"

    assert store.insert_delivery_if_new(
        delivery_id=delivery_id,
        event="issue_comment",
        repo_full_name="namjookim/claude-kanban",
        comment_id=4,
        status="accepted",
    )
    assert store.insert_delivery_if_new(
        delivery_id=delivery_id,
        event="issue_comment",
        repo_full_name="namjookim/claude-kanban",
        comment_id=4,
        status="accepted",
    ) is False

    with sqlite3.connect(settings.sqlite_path) as conn:
        count = conn.execute(
            "SELECT COUNT(*) FROM processed_deliveries WHERE delivery_id = ?",
            (delivery_id,),
        ).fetchone()[0]

    assert count == 1


def test_received_at_is_stored(settings) -> None:
    store = SqliteDeliveryStore(settings.sqlite_path)
    delivery_id = "delivery-time"

    store.insert_delivery_if_new(
        delivery_id=delivery_id,
        event="issue_comment",
        repo_full_name="namjookim/claude-kanban",
        comment_id=5,
        status="accepted",
        received_at=_now_iso(),
    )

    row = store.get_delivery(delivery_id)
    assert row is not None
    assert row["received_at"]


def test_issue_sessions_table_exists(settings) -> None:
    SqliteDeliveryStore(settings.sqlite_path)

    with sqlite3.connect(settings.sqlite_path) as conn:
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='issue_sessions'"
        ).fetchone()

    assert row is not None


def test_issue_session_upsert_and_get(settings) -> None:
    store = SqliteDeliveryStore(settings.sqlite_path)

    store.upsert_issue_session(
        repo_full_name="namjookim/claude-kanban",
        issue_number=7,
        session_name="issue-title-20260307T000001Z",
    )

    session_name = store.get_issue_session_name(
        repo_full_name="namjookim/claude-kanban",
        issue_number=7,
    )

    assert session_name == "issue-title-20260307T000001Z"


def test_issue_session_upsert_updates_latest_name_and_timestamp(settings) -> None:
    store = SqliteDeliveryStore(settings.sqlite_path)

    store.upsert_issue_session(
        repo_full_name="namjookim/claude-kanban",
        issue_number=7,
        session_name="issue-title-20260307T000001Z",
    )
    with sqlite3.connect(settings.sqlite_path) as conn:
        first_created_at, first_updated_at = conn.execute(
            """
            SELECT created_at, updated_at
            FROM issue_sessions
            WHERE repo_full_name = ? AND issue_number = ?
            """,
            ("namjookim/claude-kanban", 7),
        ).fetchone()

    store.upsert_issue_session(
        repo_full_name="namjookim/claude-kanban",
        issue_number=7,
        session_name="issue-title-20260307T000002Z",
    )

    with sqlite3.connect(settings.sqlite_path) as conn:
        second_session_name, second_created_at, second_updated_at = conn.execute(
            """
            SELECT session_name, created_at, updated_at
            FROM issue_sessions
            WHERE repo_full_name = ? AND issue_number = ?
            """,
            ("namjookim/claude-kanban", 7),
        ).fetchone()

    assert second_session_name == "issue-title-20260307T000002Z"
    assert second_created_at == first_created_at
    assert second_updated_at >= first_updated_at


def test_issue_session_name_must_be_unique(settings) -> None:
    store = SqliteDeliveryStore(settings.sqlite_path)

    store.upsert_issue_session(
        repo_full_name="namjookim/claude-kanban",
        issue_number=7,
        session_name="same-session-name-20260307T000003Z",
    )

    with pytest.raises(sqlite3.IntegrityError):
        store.upsert_issue_session(
            repo_full_name="namjookim/another-repo",
            issue_number=8,
            session_name="same-session-name-20260307T000003Z",
        )
