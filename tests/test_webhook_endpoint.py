from __future__ import annotations

import queue

from fastapi.testclient import TestClient

from app.infra.sqlite_store import SqliteDeliveryStore
from app.main import create_app


class FakeGithubClient:
    def __init__(self) -> None:
        self.reactions: list[tuple[str, int, str]] = []

    def add_comment_reaction(self, *, repo_full_name: str, comment_id: int, content: str) -> None:
        self.reactions.append((repo_full_name, comment_id, content))

    def list_issue_comments(self, *, repo_full_name: str, issue_number: int):
        _ = (repo_full_name, issue_number)
        return []

    def prepare_project_transition(self, *, repo_full_name: str, issue_number: int):
        _ = (repo_full_name, issue_number)
        return {
            "attempted": False,
            "in_progress": {
                "ok": False,
                "reason": "not_attempted",
                "project_item_id": None,
                "project_id": None,
                "status_field_id": None,
                "in_progress_option_id": None,
            },
            "next_target_status": "Review",
            "next_target_option_id": None,
        }

    def try_move_issue_to_in_progress(
        self,
        *,
        project_id: str,
        project_item_id: str,
        status_field_id: str,
        in_progress_option_id: str,
    ):
        _ = (project_id, project_item_id, status_field_id, in_progress_option_id)
        return {"ok": True, "reason": "updated"}


class DummyTmuxRunner:
    def run_payload(self, *, target: str, payload: str) -> None:
        _ = (target, payload)


def test_webhook_returns_401_on_invalid_signature(settings, payload_factory, encode_payload) -> None:
    event_queue: queue.Queue = queue.Queue()
    github = FakeGithubClient()
    store = SqliteDeliveryStore(settings.sqlite_path)

    app = create_app(
        settings=settings,
        store=store,
        event_queue=event_queue,
        github_client=github,
        tmux_runner=DummyTmuxRunner(),
    )
    client = TestClient(app)

    raw_body = encode_payload(payload_factory())
    response = client.post(
        "/webhook/github",
        content=raw_body,
        headers={
            "X-GitHub-Delivery": "delivery-invalid-signature",
            "X-GitHub-Event": "issue_comment",
            "X-Hub-Signature-256": "sha256=invalid",
            "Content-Type": "application/json",
        },
    )

    assert response.status_code == 401


def test_webhook_ignores_non_issue_comment_event(
    settings,
    payload_factory,
    encode_payload,
    signed_headers_factory,
    test_secret,
) -> None:
    event_queue: queue.Queue = queue.Queue()
    github = FakeGithubClient()
    store = SqliteDeliveryStore(settings.sqlite_path)

    app = create_app(
        settings=settings,
        store=store,
        event_queue=event_queue,
        github_client=github,
        tmux_runner=DummyTmuxRunner(),
    )
    client = TestClient(app)

    raw_body = encode_payload(payload_factory())
    headers = signed_headers_factory(
        secret=test_secret,
        raw_body=raw_body,
        delivery_id="delivery-non-event",
        event_name="issues",
    )
    response = client.post("/webhook/github", content=raw_body, headers=headers)

    assert response.status_code == 202
    assert event_queue.qsize() == 0


def test_webhook_ignores_filtered_payload(
    settings,
    payload_factory,
    encode_payload,
    signed_headers_factory,
    test_secret,
) -> None:
    event_queue: queue.Queue = queue.Queue()
    github = FakeGithubClient()
    store = SqliteDeliveryStore(settings.sqlite_path)

    app = create_app(
        settings=settings,
        store=store,
        event_queue=event_queue,
        github_client=github,
        tmux_runner=DummyTmuxRunner(),
    )
    client = TestClient(app)

    payload = payload_factory(action="edited")
    raw_body = encode_payload(payload)
    headers = signed_headers_factory(
        secret=test_secret,
        raw_body=raw_body,
        delivery_id="delivery-filtered",
    )

    response = client.post("/webhook/github", content=raw_body, headers=headers)

    assert response.status_code == 202
    assert event_queue.qsize() == 0
    assert github.reactions == []


def test_webhook_accepts_and_enqueues_job(
    settings,
    payload_factory,
    encode_payload,
    signed_headers_factory,
    test_secret,
) -> None:
    event_queue: queue.Queue = queue.Queue()
    github = FakeGithubClient()
    store = SqliteDeliveryStore(settings.sqlite_path)

    app = create_app(
        settings=settings,
        store=store,
        event_queue=event_queue,
        github_client=github,
        tmux_runner=DummyTmuxRunner(),
    )
    client = TestClient(app)

    payload = payload_factory(comment_id=777)
    raw_body = encode_payload(payload)
    headers = signed_headers_factory(
        secret=test_secret,
        raw_body=raw_body,
        delivery_id="delivery-accepted",
    )

    response = client.post("/webhook/github", content=raw_body, headers=headers)

    assert response.status_code == 202
    assert event_queue.qsize() == 1
    assert github.reactions == [("namjookim/claude-kanban", 777, "eyes")]

    row = store.get_delivery("delivery-accepted")
    assert row is not None
    assert row["status"] == "accepted"


def test_webhook_returns_200_for_duplicate_delivery(
    settings,
    payload_factory,
    encode_payload,
    signed_headers_factory,
    test_secret,
) -> None:
    event_queue: queue.Queue = queue.Queue()
    github = FakeGithubClient()
    store = SqliteDeliveryStore(settings.sqlite_path)

    app = create_app(
        settings=settings,
        store=store,
        event_queue=event_queue,
        github_client=github,
        tmux_runner=DummyTmuxRunner(),
    )
    client = TestClient(app)

    payload = payload_factory(comment_id=888)
    raw_body = encode_payload(payload)
    headers = signed_headers_factory(
        secret=test_secret,
        raw_body=raw_body,
        delivery_id="delivery-dup",
    )

    first = client.post("/webhook/github", content=raw_body, headers=headers)
    second = client.post("/webhook/github", content=raw_body, headers=headers)

    assert first.status_code == 202
    assert second.status_code == 200
    assert event_queue.qsize() == 1
