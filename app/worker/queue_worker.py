from __future__ import annotations

from dataclasses import dataclass
import json
import queue
import re
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

    def prepare_project_transition(self, *, repo_full_name: str, issue_number: int) -> dict[str, Any]: ...


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

    def _normalize_instruction(self, comment_body: str) -> str:
        cleaned_body = comment_body
        mention_keyword = self.settings.mention_keyword.strip()
        if mention_keyword:
            cleaned_body = re.sub(re.escape(mention_keyword), "", cleaned_body, flags=re.IGNORECASE)

        normalized = re.sub(r"\s+", " ", cleaned_body).strip()
        if not normalized:
            normalized = "요청 내용을 확인해 처리해."

        prefix = "/claude-kanban 스킬을 사용해서 처리해."
        return f"{prefix}\n\n{normalized}"

    def _default_project_transition(self) -> dict[str, Any]:
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

    def _build_payload(
        self,
        *,
        delivery_id: str,
        issue_title: str,
        issue_body: str | None,
        issue_author_login: str,
        is_first_mention: bool,
        repo_full_name: str,
        issue_number: int,
        comment_id: int,
        project_transition: dict[str, Any],
    ) -> str:
        payload: dict[str, Any] = {
            "delivery_id": delivery_id,
            "repo_full_name": repo_full_name,
            "issue_number": issue_number,
            "trigger_comment_id": comment_id,
            "is_first_mention": is_first_mention,
            "issue_author_login": issue_author_login,
            "project_transition": project_transition,
        }

        if is_first_mention:
            payload["issue_title"] = issue_title
            payload["issue_body"] = issue_body or ""

        return json.dumps(payload, ensure_ascii=False)

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
        issue_author_login = str((payload["issue"].get("user") or {}).get("login") or "")
        comment_id = payload["comment"]["id"]
        comment_body = payload["comment"]["body"]

        try:
            is_first = self._is_first_mention(
                repo_full_name=repo_full_name,
                issue_number=issue_number,
                current_comment_id=comment_id,
            )

            project_transition = self._default_project_transition()
            try:
                project_transition = self.github_client.prepare_project_transition(
                    repo_full_name=repo_full_name,
                    issue_number=issue_number,
                )
            except Exception as exc:
                project_transition = self._default_project_transition()
                project_transition["attempted"] = True
                project_transition["in_progress"]["reason"] = f"client_error:{type(exc).__name__}"

            instruction = self._normalize_instruction(comment_body)
            tmux_payload_json = self._build_payload(
                delivery_id=job.delivery_id,
                issue_title=issue_title,
                issue_body=issue_body,
                issue_author_login=issue_author_login,
                is_first_mention=is_first,
                repo_full_name=repo_full_name,
                issue_number=issue_number,
                comment_id=comment_id,
                project_transition=project_transition,
            )
            tmux_payload = f"{instruction}\n\n{tmux_payload_json}"
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
