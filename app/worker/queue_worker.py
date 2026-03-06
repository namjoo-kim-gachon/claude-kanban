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

    def try_move_issue_to_in_progress(
        self,
        *,
        project_id: str,
        project_item_id: str,
        status_field_id: str,
        in_progress_option_id: str,
    ) -> dict[str, Any]: ...


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
        mention_keywords = sorted(self.settings.mention_keywords, key=len, reverse=True)
        for mention_keyword in mention_keywords:
            if mention_keyword:
                cleaned_body = re.sub(re.escape(mention_keyword), "", cleaned_body, flags=re.IGNORECASE)

        normalized = re.sub(r"\s+", " ", cleaned_body).strip()
        if not normalized:
            normalized = "요청 내용을 확인해 처리해."

        prefix = "/claude-kanban"
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
        mention_keywords = [k.lower() for k in self.settings.mention_keywords if k.strip()]
        mention_comments = [
            c
            for c in comments
            if isinstance(c.get("body"), str)
            and any(keyword in c["body"].lower() for keyword in mention_keywords)
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
                project_transition["attempted"] = True
            except Exception as exc:
                project_transition = self._default_project_transition()
                project_transition["attempted"] = True
                project_transition["in_progress"]["reason"] = f"client_error:{type(exc).__name__}"

            resolved_tmux_target = self.settings.resolve_tmux_target(comment_body)
            if not resolved_tmux_target:
                raise RuntimeError("MENTION_TO_TMUX mapping is required")

            in_progress = project_transition.get("in_progress")
            if isinstance(in_progress, dict):
                project_id = in_progress.get("project_id")
                project_item_id = in_progress.get("project_item_id")
                status_field_id = in_progress.get("status_field_id")
                in_progress_option_id = in_progress.get("in_progress_option_id")

                if project_id and project_item_id and status_field_id and in_progress_option_id:
                    try:
                        self.github_client.try_move_issue_to_in_progress(
                            project_id=str(project_id),
                            project_item_id=str(project_item_id),
                            status_field_id=str(status_field_id),
                            in_progress_option_id=str(in_progress_option_id),
                        )
                    except Exception:
                        pass

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
            self.tmux_runner.run_payload(target=resolved_tmux_target, payload=tmux_payload)

            if self.store is not None:
                self.store.update_status(delivery_id=job.delivery_id, status="processed")
        except Exception:
            if self.store is not None:
                self.store.update_status(delivery_id=job.delivery_id, status="failed")
        finally:
            self.event_queue.task_done()
