from __future__ import annotations

import hashlib
import hmac
import json
from collections.abc import Callable

import pytest

from app.config import Settings


@pytest.fixture
def test_secret() -> str:
    return "test-webhook-secret"


@pytest.fixture
def settings(tmp_path, test_secret: str) -> Settings:
    return Settings(
        github_webhook_secret=test_secret,
        github_pat="test-pat",
        allowed_repo="namjookim/claude-kanban",
        tmux_target="claude:0.0",
        mention_keyword="@claude",
        sqlite_path=str(tmp_path / "webhook.db"),
        log_level="INFO",
    )


@pytest.fixture
def payload_factory() -> Callable[..., dict]:
    def _factory(
        *,
        action: str = "created",
        repo_full_name: str = "namjookim/claude-kanban",
        author_association: str = "MEMBER",
        comment_body: str = "@claude run this",
        comment_id: int = 100,
        issue_number: int = 1,
        issue_title: str = "Issue title",
        issue_body: str = "Issue body",
    ) -> dict:
        return {
            "action": action,
            "repository": {
                "full_name": repo_full_name,
            },
            "issue": {
                "number": issue_number,
                "title": issue_title,
                "body": issue_body,
                "user": {"login": "namjoo-kim-gachon"},
            },
            "comment": {
                "id": comment_id,
                "body": comment_body,
                "author_association": author_association,
            },
        }

    return _factory


@pytest.fixture
def signature_factory() -> Callable[[str, bytes], str]:
    def _factory(secret: str, raw_body: bytes) -> str:
        digest = hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
        return f"sha256={digest}"

    return _factory


@pytest.fixture
def signed_headers_factory(signature_factory: Callable[[str, bytes], str]) -> Callable[..., dict[str, str]]:
    def _factory(
        *,
        secret: str,
        raw_body: bytes,
        delivery_id: str = "delivery-1",
        event_name: str = "issue_comment",
    ) -> dict[str, str]:
        return {
            "X-GitHub-Delivery": delivery_id,
            "X-GitHub-Event": event_name,
            "X-Hub-Signature-256": signature_factory(secret, raw_body),
            "Content-Type": "application/json",
        }

    return _factory


@pytest.fixture
def encode_payload() -> Callable[[dict], bytes]:
    def _factory(payload: dict) -> bytes:
        return json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")

    return _factory
