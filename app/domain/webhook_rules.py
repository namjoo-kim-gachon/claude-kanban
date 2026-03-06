from __future__ import annotations

from dataclasses import dataclass
import hashlib
import hmac
from typing import Any


ACCEPTED_ASSOCIATIONS = {"OWNER", "MEMBER", "COLLABORATOR"}


@dataclass(frozen=True)
class FilterDecision:
    allowed: bool
    reason: str
    comment_id: int | None = None


def verify_github_signature(*, raw_body: bytes, secret: str, signature_header: str | None) -> bool:
    if not secret or not signature_header:
        return False
    if not signature_header.startswith("sha256="):
        return False

    expected_digest = hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    expected_signature = f"sha256={expected_digest}"
    return hmac.compare_digest(expected_signature, signature_header)


def should_handle_event(*, event_name: str | None) -> bool:
    return event_name == "issue_comment"


def is_allowed_issue_comment(*, payload: dict[str, Any], mention_keywords: list[str]) -> FilterDecision:
    action = payload.get("action")
    if action != "created":
        return FilterDecision(allowed=False, reason="action_not_created")

    comment = payload.get("comment") or {}
    association = comment.get("author_association")
    if association not in ACCEPTED_ASSOCIATIONS:
        return FilterDecision(allowed=False, reason="author_not_allowed")

    comment_body = comment.get("body") or ""
    normalized_body = comment_body.lower()
    if not any(keyword.lower() in normalized_body for keyword in mention_keywords if keyword.strip()):
        return FilterDecision(allowed=False, reason="mention_not_found")

    comment_id = comment.get("id")
    if not isinstance(comment_id, int):
        return FilterDecision(allowed=False, reason="comment_id_invalid")

    return FilterDecision(allowed=True, reason="accepted", comment_id=comment_id)
