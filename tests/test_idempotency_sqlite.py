from __future__ import annotations

import sqlite3
from datetime import UTC, datetime

from app.infra.sqlite_store import SqliteDeliveryStore


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


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
