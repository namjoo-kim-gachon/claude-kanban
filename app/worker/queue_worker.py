from __future__ import annotations

from dataclasses import dataclass
import queue
import threading
from typing import Any, Protocol

from app.config import Settings
from app.infra.sqlite_store import SqliteDeliveryStore


@dataclass(frozen=True)
class WorkerJob:
    delivery_id: str
    payload: dict[str, Any]


class GithubClientProtocol(Protocol):
    def list_issue_comments(self, *, repo_full_name: str, issue_number: int) -> list[dict[str, Any]]: ...

    def add_comment_reaction(self, *, repo_full_name: str, comment_id: int, content: str) -> None: ...


class TmuxRunnerProtocol(Protocol):
    def run_payload(self, *, target: str, payload: str) -> None: ...


class QueueWorker:
    def __init__(
        self,
        *,
        settings: Settings,
        event_queue: queue.Queue[WorkerJob],
        store: SqliteDeliveryStore | None,
        github_client: GithubClientProtocol,
        tmux_runner: TmuxRunnerProtocol,
    ) -> None:
        self.settings = settings
        self.event_queue = event_queue
        self.store = store
        self.github_client = github_client
        self.tmux_runner = tmux_runner
        self._running = threading.Event()

    def _build_payload(self, *, issue_title: str, issue_body: str | None, comment_body: str, is_first_mention: bool) -> str:
        if is_first_mention:
            issue_body_text = issue_body or ""
            return f"Issue Title:\n{issue_title}\n\nIssue Body:\n{issue_body_text}\n\nComment:\n{comment_body}"
        return comment_body

    def _is_first_mention(self, *, repo_full_name: str, issue_number: int, current_comment_id: int) -> bool:
        comments = self.github_client.list_issue_comments(repo_full_name=repo_full_name, issue_number=issue_number)
        mention_comments = [
            c for c in comments if isinstance(c.get("body"), str) and self.settings.mention_keyword.lower() in c["body"].lower()
        ]
        mention_comments.sort(key=lambda c: c.get("id", 0))
        if not mention_comments:
            return False
        return mention_comments[0].get("id") == current_comment_id

    def run_forever(self) -> None:
        self._running.set()
        while self._running.is_set():
            try:
                job = self.event_queue.get(timeout=0.2)
            except queue.Empty:
                continue
            self._process_job(job)

    def stop(self) -> None:
        self._running.clear()

    def process_next_once(self) -> bool:
        try:
            job = self.event_queue.get_nowait()
        except queue.Empty:
            return False

        self._process_job(job)
        return True

    def _process_job(self, job: WorkerJob) -> None:
        payload = job.payload
        repo_full_name = payload["repository"]["full_name"]
        issue_number = payload["issue"]["number"]
        issue_title = payload["issue"].get("title", "")
        issue_body = payload["issue"].get("body")
        comment_id = payload["comment"]["id"]
        comment_body = payload["comment"]["body"]

        try:
            is_first = self._is_first_mention(
                repo_full_name=repo_full_name,
                issue_number=issue_number,
                current_comment_id=comment_id,
            )
            tmux_payload = self._build_payload(
                issue_title=issue_title,
                issue_body=issue_body,
                comment_body=comment_body,
                is_first_mention=is_first,
            )
            self.tmux_runner.run_payload(target=self.settings.tmux_target, payload=tmux_payload)

            if self.store is not None:
                self.store.update_status(delivery_id=job.delivery_id, status="processed")

            try:
                self.github_client.add_comment_reaction(
                    repo_full_name=repo_full_name,
                    comment_id=comment_id,
                    content="rocket",
                )
            except Exception:
                pass
        except Exception:
            if self.store is not None:
                self.store.update_status(delivery_id=job.delivery_id, status="failed")
            try:
                self.github_client.add_comment_reaction(
                    repo_full_name=repo_full_name,
                    comment_id=comment_id,
                    content="confused",
                )
            except Exception:
                pass
        finally:
            self.event_queue.task_done()
