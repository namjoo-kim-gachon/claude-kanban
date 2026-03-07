from __future__ import annotations

from contextlib import asynccontextmanager
import json
import queue
import threading
from typing import Any

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.responses import JSONResponse

from app.config import Settings
from app.domain.webhook_rules import (
    is_allowed_issue_comment,
    is_allowed_issue_state_event,
    should_handle_event,
    verify_github_signature,
)
from app.infra.github_client import GithubClient
from app.infra.sqlite_store import SqliteDeliveryStore
from app.infra.tmux_runner import TmuxRunner
from app.worker.queue_worker import QueueWorker, WorkerJob


def _extract_payload(raw_body: bytes) -> dict[str, Any]:
    try:
        payload = json.loads(raw_body.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="invalid_json") from exc

    if not isinstance(payload, dict):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="invalid_payload")
    return payload


def create_app(
    settings: Settings | None = None,
    *,
    store: SqliteDeliveryStore | None = None,
    event_queue: queue.Queue[WorkerJob] | None = None,
    github_client: Any | None = None,
    tmux_runner: Any | None = None,
) -> FastAPI:
    resolved_settings = settings or Settings.from_env()
    resolved_store = store or SqliteDeliveryStore(resolved_settings.sqlite_path)
    resolved_queue = event_queue or queue.Queue()
    resolved_github_client = github_client or GithubClient(token=resolved_settings.github_pat)
    resolved_tmux_runner = tmux_runner or TmuxRunner()

    worker = QueueWorker(
        settings=resolved_settings,
        event_queue=resolved_queue,
        store=resolved_store,
        github_client=resolved_github_client,
        tmux_runner=resolved_tmux_runner,
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        thread = threading.Thread(target=worker.run_forever, daemon=True, name="webhook-worker")
        thread.start()
        app.state.worker_thread = thread
        try:
            yield
        finally:
            worker.stop()
            thread.join(timeout=1.0)

    app = FastAPI(lifespan=lifespan)
    app.state.settings = resolved_settings
    app.state.store = resolved_store
    app.state.event_queue = resolved_queue
    app.state.github_client = resolved_github_client
    app.state.tmux_runner = resolved_tmux_runner
    app.state.worker = worker
    app.state.worker_thread = None

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/webhook/github")
    async def github_webhook(request: Request) -> JSONResponse:
        raw_body = await request.body()
        signature_header = request.headers.get("X-Hub-Signature-256")

        if not verify_github_signature(
            raw_body=raw_body,
            secret=resolved_settings.github_webhook_secret,
            signature_header=signature_header,
        ):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid_signature")

        event_name = request.headers.get("X-GitHub-Event")
        if not should_handle_event(event_name=event_name):
            return JSONResponse(status_code=status.HTTP_202_ACCEPTED, content={"result": "ignored_event"})

        payload = _extract_payload(raw_body)
        if event_name == "issue_comment":
            decision = is_allowed_issue_comment(
                payload=payload,
                mention_keywords=resolved_settings.mention_keywords,
            )
            if not decision.allowed:
                return JSONResponse(
                    status_code=status.HTTP_202_ACCEPTED,
                    content={"result": "ignored_filter", "reason": decision.reason},
                )
        else:
            decision = is_allowed_issue_state_event(payload=payload)
            if not decision.allowed:
                return JSONResponse(
                    status_code=status.HTTP_202_ACCEPTED,
                    content={"result": "ignored_filter", "reason": decision.reason},
                )

        delivery_id = request.headers.get("X-GitHub-Delivery")
        if not delivery_id:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="missing_delivery_id")

        repo_full_name = payload["repository"]["full_name"]
        inserted = resolved_store.insert_delivery_if_new(
            delivery_id=delivery_id,
            event=event_name or "issue_comment",
            repo_full_name=repo_full_name,
            comment_id=decision.comment_id,
            status="accepted",
        )
        if not inserted:
            return JSONResponse(status_code=status.HTTP_200_OK, content={"result": "duplicate"})

        if event_name == "issue_comment":
            comment_id = int(payload["comment"]["id"])
            try:
                resolved_github_client.add_comment_reaction(
                    repo_full_name=repo_full_name,
                    comment_id=comment_id,
                    content="eyes",
                )
            except Exception:
                pass

        resolved_queue.put(WorkerJob(delivery_id=delivery_id, event_name=event_name or "issue_comment", payload=payload))
        return JSONResponse(status_code=status.HTTP_202_ACCEPTED, content={"result": "accepted"})

    return app


app = create_app()
