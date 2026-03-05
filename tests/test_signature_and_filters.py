from __future__ import annotations

from app.domain.webhook_rules import (
    ACCEPTED_ASSOCIATIONS,
    is_allowed_issue_comment,
    should_handle_event,
    verify_github_signature,
)


def test_verify_github_signature_rejects_invalid_signature(test_secret, encode_payload, payload_factory) -> None:
    raw_body = encode_payload(payload_factory())

    assert verify_github_signature(raw_body=raw_body, secret=test_secret, signature_header="sha256=invalid") is False


def test_should_handle_event_rejects_non_issue_comment() -> None:
    assert should_handle_event(event_name="issues") is False


def test_filter_rejects_non_created_action(payload_factory) -> None:
    payload = payload_factory(action="edited")

    decision = is_allowed_issue_comment(
        payload=payload,
        allowed_repo="namjookim/claude-kanban",
        mention_keyword="@claude",
    )

    assert decision.allowed is False
    assert decision.reason == "action_not_created"


def test_filter_rejects_unallowed_repo(payload_factory) -> None:
    payload = payload_factory(repo_full_name="other/repo")

    decision = is_allowed_issue_comment(
        payload=payload,
        allowed_repo="namjookim/claude-kanban",
        mention_keyword="@claude",
    )

    assert decision.allowed is False
    assert decision.reason == "repo_not_allowed"


def test_filter_rejects_unallowed_author_association(payload_factory) -> None:
    payload = payload_factory(author_association="CONTRIBUTOR")

    decision = is_allowed_issue_comment(
        payload=payload,
        allowed_repo="namjookim/claude-kanban",
        mention_keyword="@claude",
    )

    assert decision.allowed is False
    assert decision.reason == "author_not_allowed"


def test_filter_rejects_missing_mention(payload_factory) -> None:
    payload = payload_factory(comment_body="please run this")

    decision = is_allowed_issue_comment(
        payload=payload,
        allowed_repo="namjookim/claude-kanban",
        mention_keyword="@claude",
    )

    assert decision.allowed is False
    assert decision.reason == "mention_not_found"


def test_filter_accepts_valid_payload(payload_factory) -> None:
    payload = payload_factory(author_association="OWNER", comment_body="Please check @ClAuDe")

    decision = is_allowed_issue_comment(
        payload=payload,
        allowed_repo="namjookim/claude-kanban",
        mention_keyword="@claude",
    )

    assert decision.allowed is True
    assert decision.reason == "accepted"
    assert decision.comment_id == payload["comment"]["id"]


def test_accepted_associations_are_fixed_set() -> None:
    assert ACCEPTED_ASSOCIATIONS == {"OWNER", "MEMBER", "COLLABORATOR"}
