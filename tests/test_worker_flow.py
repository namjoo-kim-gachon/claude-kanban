from __future__ import annotations

import json
import queue
from typing import Any

from app.infra.sqlite_store import SqliteDeliveryStore
from app.infra.tmux_runner import TmuxRunner
from app.worker.queue_worker import QueueWorker, WorkerJob


class FakeGithubClient:
    def __init__(
        self,
        *,
        mention_comments: list[dict[str, Any]],
        fail_reaction: bool = False,
        transition_result: dict[str, Any] | None = None,
        transition_error: Exception | None = None,
        move_result: dict[str, Any] | None = None,
        move_error: Exception | None = None,
    ) -> None:
        self.mention_comments = mention_comments
        self.fail_reaction = fail_reaction
        self.reactions: list[tuple[int, str]] = []
        self.transition_result = transition_result or {
            "attempted": True,
            "in_progress": {
                "ok": False,
                "reason": "ready",
                "project_item_id": "ITEM_1",
                "project_id": "PROJ_1",
                "status_field_id": "FIELD_1",
                "in_progress_option_id": "OPT_IN_PROGRESS",
            },
            "next_target_status": "Review",
            "next_target_option_id": "OPT_REVIEW",
        }
        self.transition_error = transition_error
        self.transition_calls: list[tuple[str, int]] = []
        self.move_result = move_result or {"ok": True, "reason": "updated"}
        self.move_error = move_error
        self.move_calls: list[tuple[str, str, str, str]] = []

    def list_issue_comments(self, *, repo_full_name: str, issue_number: int) -> list[dict[str, Any]]:
        _ = (repo_full_name, issue_number)
        return self.mention_comments

    def add_comment_reaction(self, *, repo_full_name: str, comment_id: int, content: str) -> None:
        _ = repo_full_name
        if self.fail_reaction:
            raise RuntimeError("reaction failed")
        self.reactions.append((comment_id, content))

    def prepare_project_transition(self, *, repo_full_name: str, issue_number: int) -> dict[str, Any]:
        self.transition_calls.append((repo_full_name, issue_number))
        if self.transition_error is not None:
            raise self.transition_error
        return self.transition_result

    def try_move_issue_to_in_progress(
        self,
        *,
        project_id: str,
        project_item_id: str,
        status_field_id: str,
        in_progress_option_id: str,
    ) -> dict[str, Any]:
        self.move_calls.append((project_id, project_item_id, status_field_id, in_progress_option_id))
        if self.move_error is not None:
            raise self.move_error
        return self.move_result


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
        "issue": {"number": 7, "title": "Issue title", "body": "Issue body", "user": {"login": "namjoo-kim-gachon"}},
        "comment": {"id": comment_id, "body": comment_body},
    }
    return WorkerJob(delivery_id=delivery_id, payload=payload)


def _decode_tmux_payload(payload_text: str) -> tuple[str, dict[str, Any]]:
    instruction, payload_json = payload_text.rsplit("\n\n", 1)
    return instruction, json.loads(payload_json)


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
    _, first_payload = _decode_tmux_payload(tmux.payloads[0])
    _, second_payload = _decode_tmux_payload(tmux.payloads[1])
    assert first_payload["delivery_id"] == "d1"
    assert second_payload["delivery_id"] == "d2"


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

    instruction, payload = _decode_tmux_payload(tmux.payloads[0])
    assert payload["repo_full_name"] == "namjookim/claude-kanban"
    assert payload["issue_number"] == 7
    assert payload["trigger_comment_id"] == 101
    assert payload["is_first_mention"] is True
    assert payload["issue_author_login"] == "namjoo-kim-gachon"
    assert payload["issue_title"] == "Issue title"
    assert payload["issue_body"] == "Issue body"
    assert payload["project_transition"]["next_target_status"] == "Review"
    assert instruction.startswith("/claude-kanban")


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

    _, payload = _decode_tmux_payload(tmux.payloads[0])
    assert payload["is_first_mention"] is False
    assert "issue_title" not in payload
    assert "issue_body" not in payload
    assert payload["project_transition"]["next_target_status"] == "Review"


def test_worker_normalizes_instruction_and_removes_mentions(settings) -> None:
    event_queue: queue.Queue[WorkerJob] = queue.Queue()
    github = FakeGithubClient(mention_comments=[{"id": 711, "body": "@ClAuDe   run this"}])
    tmux = FakeTmuxRunner()

    worker = QueueWorker(
        settings=settings,
        event_queue=event_queue,
        store=None,
        github_client=github,
        tmux_runner=tmux,
    )

    event_queue.put(_job("d-normalize", 711, "  @ClAuDe   run\nthis  "))
    worker.process_next_once()

    instruction, _ = _decode_tmux_payload(tmux.payloads[0])
    assert instruction.startswith("/claude-kanban")
    assert "@claude" not in instruction.lower()


def test_worker_does_not_add_success_reaction(settings) -> None:
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

    assert github.reactions == []


def test_worker_does_not_add_failure_reaction_on_tmux_error(settings) -> None:
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

    assert github.reactions == []


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


def test_worker_includes_project_transition_metadata(settings) -> None:
    event_queue: queue.Queue[WorkerJob] = queue.Queue()
    github = FakeGithubClient(mention_comments=[{"id": 801, "body": "@claude go"}])
    tmux = FakeTmuxRunner()

    worker = QueueWorker(
        settings=settings,
        event_queue=event_queue,
        store=None,
        github_client=github,
        tmux_runner=tmux,
    )

    event_queue.put(_job("d-transition-ok", 801, "@claude go"))
    worker.process_next_once()

    _, payload = _decode_tmux_payload(tmux.payloads[0])
    transition = payload["project_transition"]
    assert transition["attempted"] is True
    assert transition["in_progress"]["project_item_id"] == "ITEM_1"
    assert transition["next_target_status"] == "Review"
    assert transition["next_target_option_id"] == "OPT_REVIEW"
    assert github.transition_calls == [("namjookim/claude-kanban", 7)]


def test_worker_soft_fails_project_transition_and_continues_tmux(settings) -> None:
    event_queue: queue.Queue[WorkerJob] = queue.Queue()
    github = FakeGithubClient(
        mention_comments=[{"id": 901, "body": "@claude go"}],
        transition_error=RuntimeError("forbidden"),
    )
    tmux = FakeTmuxRunner()

    worker = QueueWorker(
        settings=settings,
        event_queue=event_queue,
        store=None,
        github_client=github,
        tmux_runner=tmux,
    )

    event_queue.put(_job("d-transition-soft-fail", 901, "@claude go"))
    worker.process_next_once()

    assert len(tmux.payloads) == 1
    _, payload = _decode_tmux_payload(tmux.payloads[0])
    transition = payload["project_transition"]
    assert transition["attempted"] is True
    assert transition["in_progress"]["ok"] is False
    assert transition["in_progress"]["reason"] == "client_error:RuntimeError"
    assert github.reactions == []


def test_worker_moves_board_after_tmux_send(settings) -> None:
    event_queue: queue.Queue[WorkerJob] = queue.Queue()
    github = FakeGithubClient(mention_comments=[{"id": 910, "body": "@claude go"}])
    tmux = FakeTmuxRunner()

    worker = QueueWorker(
        settings=settings,
        event_queue=event_queue,
        store=None,
        github_client=github,
        tmux_runner=tmux,
    )

    event_queue.put(_job("d-move-order", 910, "@claude go"))
    worker.process_next_once()

    assert len(tmux.payloads) == 1
    assert github.move_calls == [("PROJ_1", "ITEM_1", "FIELD_1", "OPT_IN_PROGRESS")]


def test_worker_moves_board_even_when_tmux_fails(settings) -> None:
    event_queue: queue.Queue[WorkerJob] = queue.Queue()
    github = FakeGithubClient(mention_comments=[{"id": 920, "body": "@claude go"}])
    tmux = FakeTmuxRunner(should_fail=True)

    worker = QueueWorker(
        settings=settings,
        event_queue=event_queue,
        store=None,
        github_client=github,
        tmux_runner=tmux,
    )

    event_queue.put(_job("d-move-skip", 920, "@claude go"))
    worker.process_next_once()

    assert github.move_calls == [("PROJ_1", "ITEM_1", "FIELD_1", "OPT_IN_PROGRESS")]


def test_worker_fails_when_mention_mapping_not_found(settings) -> None:
    event_queue: queue.Queue[WorkerJob] = queue.Queue()
    github = FakeGithubClient(mention_comments=[{"id": 930, "body": "@claude go"}])
    tmux = FakeTmuxRunner()

    no_mapping_settings = type(settings)(
        github_webhook_secret=settings.github_webhook_secret,
        github_pat=settings.github_pat,
        mention_to_tmux={"@other": "claude:0.1"},
        sqlite_path=settings.sqlite_path,
        log_level=settings.log_level,
    )

    worker = QueueWorker(
        settings=no_mapping_settings,
        event_queue=event_queue,
        store=None,
        github_client=github,
        tmux_runner=tmux,
    )

    event_queue.put(_job("d-no-target", 930, "@claude go"))
    worker.process_next_once()

    assert len(tmux.payloads) == 0


def test_worker_routes_by_mention_mapping(settings) -> None:
    event_queue: queue.Queue[WorkerJob] = queue.Queue()
    github = FakeGithubClient(mention_comments=[{"id": 940, "body": "@ㅋㅋ run"}])

    class CaptureTmuxRunner:
        def __init__(self) -> None:
            self.calls: list[tuple[str, str]] = []

        def run_payload(self, *, target: str, payload: str) -> None:
            self.calls.append((target, payload))

    tmux = CaptureTmuxRunner()

    mapped_settings = type(settings)(
        github_webhook_secret=settings.github_webhook_secret,
        github_pat=settings.github_pat,
        mention_to_tmux={"@ㅋㅋ": "0:0.0", "@ㅋㅋ1": "0:1.0"},
        sqlite_path=settings.sqlite_path,
        log_level=settings.log_level,
    )

    worker = QueueWorker(
        settings=mapped_settings,
        event_queue=event_queue,
        store=None,
        github_client=github,
        tmux_runner=tmux,
    )

    event_queue.put(_job("d-map-mention", 940, "@ㅋㅋ 이 기능 추가해줘"))
    worker.process_next_once()

    assert len(tmux.calls) == 1
    assert tmux.calls[0][0] == "0:0.0"
    instruction, _ = _decode_tmux_payload(tmux.calls[0][1])
    assert "@ㅋㅋ" not in instruction


def test_tmux_runner_uses_preflight_load_paste_enter_sequence(monkeypatch) -> None:
    calls: list[dict[str, Any]] = []

    class _Result:
        def __init__(self, stdout: str = "") -> None:
            self.stdout = stdout

    def _fake_run(args, **kwargs):
        calls.append({"args": args, **kwargs})
        if args[:3] == ["tmux", "display-message", "-p"]:
            return _Result(stdout="0:0\n")
        return _Result()

    monkeypatch.setattr("subprocess.run", _fake_run)

    runner = TmuxRunner()
    runner.run_payload(target="claude:0.0", payload="hello @claude")

    assert len(calls) == 4
    assert calls[0]["args"] == ["tmux", "display-message", "-p", "-t", "claude:0.0", "#{pane_dead}:#{pane_in_mode}"]
    assert calls[0]["capture_output"] is True
    assert calls[0]["text"] is True

    assert calls[1]["args"][:4] == ["tmux", "load-buffer", "-b", calls[1]["args"][3]]
    assert calls[1]["args"][4:] == ["-"]
    assert calls[1]["input"] == "hello @claude"
    assert calls[1]["text"] is True

    assert calls[2]["args"] == ["tmux", "paste-buffer", "-d", "-b", calls[1]["args"][3], "-t", "claude:0.0"]
    assert calls[3]["args"] == ["tmux", "send-keys", "-t", "claude:0.0", "C-m"]
    assert all(call["shell"] is False for call in calls)


def test_tmux_runner_keeps_payload_unchanged_before_load_buffer(monkeypatch) -> None:
    calls: list[dict[str, Any]] = []

    class _Result:
        def __init__(self, stdout: str = "") -> None:
            self.stdout = stdout

    def _fake_run(args, **kwargs):
        calls.append({"args": args, **kwargs})
        if args[:3] == ["tmux", "display-message", "-p"]:
            return _Result(stdout="0:0\n")
        return _Result()

    monkeypatch.setattr("subprocess.run", _fake_run)

    payload = "line1\n\n{\"k\":1}"
    runner = TmuxRunner()
    runner.run_payload(target="claude:0.0", payload=payload)

    assert len(calls) == 4
    assert calls[1]["input"] == payload
    assert calls[2]["args"][0] == "tmux"
    assert calls[3]["args"][-1] == "C-m"
    assert all(call["shell"] is False for call in calls)


def test_tmux_runner_cancels_copy_mode_before_paste(monkeypatch) -> None:
    calls: list[dict[str, Any]] = []

    class _Result:
        def __init__(self, stdout: str = "") -> None:
            self.stdout = stdout

    def _fake_run(args, **kwargs):
        calls.append({"args": args, **kwargs})
        if args[:3] == ["tmux", "display-message", "-p"]:
            return _Result(stdout="0:1\n")
        return _Result()

    monkeypatch.setattr("subprocess.run", _fake_run)

    runner = TmuxRunner()
    runner.run_payload(target="claude:0.0", payload="hello")

    assert len(calls) == 5
    assert calls[1]["args"] == ["tmux", "send-keys", "-t", "claude:0.0", "-X", "cancel"]
    assert calls[-1]["args"] == ["tmux", "send-keys", "-t", "claude:0.0", "C-m"]


def test_tmux_runner_raises_meaningful_error_on_preflight_failure(monkeypatch) -> None:
    def _fake_run(args, **kwargs):
        raise __import__("subprocess").CalledProcessError(returncode=1, cmd=args)

    monkeypatch.setattr("subprocess.run", _fake_run)

    runner = TmuxRunner()
    try:
        runner.run_payload(target="claude:0.0", payload="hello")
        assert False, "expected RuntimeError"
    except RuntimeError as exc:
        assert "tmux preflight failed" in str(exc)


def test_tmux_runner_raises_meaningful_error_on_paste_failure(monkeypatch) -> None:
    calls = {"count": 0}

    class _Result:
        def __init__(self, stdout: str = "") -> None:
            self.stdout = stdout

    def _fake_run(args, **kwargs):
        calls["count"] += 1
        if args[:3] == ["tmux", "display-message", "-p"]:
            return _Result(stdout="0:0\n")
        if args[:2] == ["tmux", "paste-buffer"]:
            raise __import__("subprocess").CalledProcessError(returncode=1, cmd=args)
        return _Result()

    monkeypatch.setattr("subprocess.run", _fake_run)

    runner = TmuxRunner()
    try:
        runner.run_payload(target="claude:0.0", payload="hello")
        assert False, "expected RuntimeError"
    except RuntimeError as exc:
        assert "tmux paste-buffer failed" in str(exc)
