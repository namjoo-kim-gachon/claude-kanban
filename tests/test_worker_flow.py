from __future__ import annotations

import queue
from typing import Any

from app.infra.sqlite_store import SqliteDeliveryStore
from app.infra.tmux_runner import TmuxRunner
from app.worker.queue_worker import QueueWorker, WorkerJob


class FakeGithubClient:
    def __init__(self, *, mention_comments: list[dict[str, Any]], fail_reaction: bool = False) -> None:
        self.mention_comments = mention_comments
        self.fail_reaction = fail_reaction
        self.reactions: list[tuple[int, str]] = []

    def list_issue_comments(self, *, repo_full_name: str, issue_number: int) -> list[dict[str, Any]]:
        _ = (repo_full_name, issue_number)
        return self.mention_comments

    def add_comment_reaction(self, *, repo_full_name: str, comment_id: int, content: str) -> None:
        _ = repo_full_name
        if self.fail_reaction:
            raise RuntimeError("reaction failed")
        self.reactions.append((comment_id, content))


class FakeTmuxRunner:
    def __init__(self, *, should_fail: bool = False) -> None:
        self.should_fail = should_fail
        self.payloads: list[str] = []

    def run_payload(self, *, target: str, payload: str) -> None:
        _ = target
        if self.should_fail:
            raise RuntimeError("tmux failed")
        self.payloads.append(payload)


def _job(delivery_id: str, comment_id: int, comment_body: str) -> WorkerJob:
    payload = {
        "repository": {"full_name": "namjookim/claude-kanban"},
        "issue": {"number": 7, "title": "Issue title", "body": "Issue body"},
        "comment": {"id": comment_id, "body": comment_body},
    }
    return WorkerJob(delivery_id=delivery_id, payload=payload)


def test_worker_processes_jobs_in_fifo_order(settings) -> None:
    event_queue: queue.Queue[WorkerJob] = queue.Queue()
    github = FakeGithubClient(mention_comments=[{"id": 100, "body": "@claude first"}])
    tmux = FakeTmuxRunner()

    worker = QueueWorker(
        settings=settings,
        event_queue=event_queue,
        store=None,
        github_client=github,
        tmux_runner=tmux,
    )

    event_queue.put(_job("d1", 100, "@claude first"))
    event_queue.put(_job("d2", 200, "@claude second"))

    worker.process_next_once()
    worker.process_next_once()

    assert len(tmux.payloads) == 2
    assert "@claude first" in tmux.payloads[0]
    assert "@claude second" in tmux.payloads[1]


def test_worker_sends_first_comment_payload_with_issue_context(settings) -> None:
    event_queue: queue.Queue[WorkerJob] = queue.Queue()
    github = FakeGithubClient(mention_comments=[{"id": 101, "body": "@claude first"}])
    tmux = FakeTmuxRunner()

    worker = QueueWorker(
        settings=settings,
        event_queue=event_queue,
        store=None,
        github_client=github,
        tmux_runner=tmux,
    )

    event_queue.put(_job("d-first", 101, "@claude first"))
    worker.process_next_once()

    payload = tmux.payloads[0]
    assert "Issue title" in payload
    assert "Issue body" in payload
    assert "@claude first" in payload


def test_worker_sends_followup_comment_payload_without_issue_context(settings) -> None:
    event_queue: queue.Queue[WorkerJob] = queue.Queue()
    github = FakeGithubClient(
        mention_comments=[
            {"id": 101, "body": "@claude first"},
            {"id": 202, "body": "@claude followup"},
        ]
    )
    tmux = FakeTmuxRunner()

    worker = QueueWorker(
        settings=settings,
        event_queue=event_queue,
        store=None,
        github_client=github,
        tmux_runner=tmux,
    )

    event_queue.put(_job("d-follow", 202, "@claude followup"))
    worker.process_next_once()

    payload = tmux.payloads[0]
    assert "Issue title" not in payload
    assert "Issue body" not in payload
    assert "@claude followup" in payload


def test_worker_marks_success_reaction(settings) -> None:
    event_queue: queue.Queue[WorkerJob] = queue.Queue()
    github = FakeGithubClient(mention_comments=[{"id": 300, "body": "@claude go"}])
    tmux = FakeTmuxRunner()

    worker = QueueWorker(
        settings=settings,
        event_queue=event_queue,
        store=None,
        github_client=github,
        tmux_runner=tmux,
    )

    event_queue.put(_job("d-success", 300, "@claude go"))
    worker.process_next_once()

    assert (300, "rocket") in github.reactions


def test_worker_marks_failure_reaction_on_tmux_error(settings) -> None:
    event_queue: queue.Queue[WorkerJob] = queue.Queue()
    github = FakeGithubClient(mention_comments=[{"id": 400, "body": "@claude go"}])
    tmux = FakeTmuxRunner(should_fail=True)

    worker = QueueWorker(
        settings=settings,
        event_queue=event_queue,
        store=None,
        github_client=github,
        tmux_runner=tmux,
    )

    event_queue.put(_job("d-fail", 400, "@claude go"))
    worker.process_next_once()

    assert (400, "confused") in github.reactions


def test_worker_updates_store_processed_status(settings) -> None:
    event_queue: queue.Queue[WorkerJob] = queue.Queue()
    store = SqliteDeliveryStore(settings.sqlite_path)
    github = FakeGithubClient(mention_comments=[{"id": 500, "body": "@claude go"}])
    tmux = FakeTmuxRunner()

    store.insert_delivery_if_new(
        delivery_id="d-processed",
        event="issue_comment",
        repo_full_name="namjookim/claude-kanban",
        comment_id=500,
        status="accepted",
    )

    worker = QueueWorker(
        settings=settings,
        event_queue=event_queue,
        store=store,
        github_client=github,
        tmux_runner=tmux,
    )

    event_queue.put(_job("d-processed", 500, "@claude go"))
    worker.process_next_once()

    row = store.get_delivery("d-processed")
    assert row is not None
    assert row["status"] == "processed"


def test_worker_updates_store_failed_status(settings) -> None:
    event_queue: queue.Queue[WorkerJob] = queue.Queue()
    store = SqliteDeliveryStore(settings.sqlite_path)
    github = FakeGithubClient(mention_comments=[{"id": 600, "body": "@claude go"}])
    tmux = FakeTmuxRunner(should_fail=True)

    store.insert_delivery_if_new(
        delivery_id="d-failed",
        event="issue_comment",
        repo_full_name="namjookim/claude-kanban",
        comment_id=600,
        status="accepted",
    )

    worker = QueueWorker(
        settings=settings,
        event_queue=event_queue,
        store=store,
        github_client=github,
        tmux_runner=tmux,
    )

    event_queue.put(_job("d-failed", 600, "@claude go"))
    worker.process_next_once()

    row = store.get_delivery("d-failed")
    assert row is not None
    assert row["status"] == "failed"


def test_tmux_runner_uses_shell_false_and_send_keys_literal(monkeypatch) -> None:
    calls: list[dict[str, Any]] = []

    def _fake_run(args, *, check, shell):
        calls.append({"args": args, "check": check, "shell": shell})

    monkeypatch.setattr("subprocess.run", _fake_run)

    runner = TmuxRunner()
    runner.run_payload(target="claude:0.0", payload="hello @claude")

    assert len(calls) == 2
    assert calls[0]["args"] == ["tmux", "send-keys", "-t", "claude:0.0", "-l", "hello @claude"]
    assert calls[1]["args"] == ["tmux", "send-keys", "-t", "claude:0.0", "Enter"]
    assert all(call["shell"] is False for call in calls)
