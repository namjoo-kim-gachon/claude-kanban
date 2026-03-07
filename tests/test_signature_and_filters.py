from __future__ import annotations

import pytest

from app.config import _parse_mention_target_map
from app.domain.webhook_rules import (
    ACCEPTED_ASSOCIATIONS,
    is_allowed_issue_comment,
    is_allowed_issue_state_event,
    should_handle_event,
    verify_github_signature,
)


def test_verify_github_signature_rejects_invalid_signature(test_secret, encode_payload, payload_factory) -> None:
    raw_body = encode_payload(payload_factory())

    assert verify_github_signature(raw_body=raw_body, secret=test_secret, signature_header="sha256=invalid") is False


def test_should_handle_event_accepts_issues() -> None:
    assert should_handle_event(event_name="issues") is True


def test_should_handle_event_rejects_unrelated_event() -> None:
    assert should_handle_event(event_name="pull_request") is False


def test_filter_rejects_non_created_action(payload_factory) -> None:
    payload = payload_factory(action="edited")

    decision = is_allowed_issue_comment(
        payload=payload,
        mention_keywords=["@claude"],
    )

    assert decision.allowed is False
    assert decision.reason == "action_not_created"


def test_filter_rejects_unallowed_author_association(payload_factory) -> None:
    payload = payload_factory(author_association="CONTRIBUTOR")

    decision = is_allowed_issue_comment(
        payload=payload,
        mention_keywords=["@claude"],
    )

    assert decision.allowed is False
    assert decision.reason == "author_not_allowed"


def test_filter_rejects_missing_mention(payload_factory) -> None:
    payload = payload_factory(comment_body="please run this")

    decision = is_allowed_issue_comment(
        payload=payload,
        mention_keywords=["@claude"],
    )

    assert decision.allowed is False
    assert decision.reason == "mention_not_found"


def test_filter_rejects_when_mention_keywords_empty(payload_factory) -> None:
    payload = payload_factory(comment_body="@claude run this")

    decision = is_allowed_issue_comment(
        payload=payload,
        mention_keywords=[],
    )

    assert decision.allowed is False
    assert decision.reason == "mention_not_found"


def test_filter_accepts_valid_payload(payload_factory) -> None:
    payload = payload_factory(author_association="OWNER", comment_body="Please check @ClAuDe")

    decision = is_allowed_issue_comment(
        payload=payload,
        mention_keywords=["@claude"],
    )

    assert decision.allowed is True
    assert decision.reason == "accepted"
    assert decision.comment_id == payload["comment"]["id"]


def test_filter_accepts_when_any_configured_mention_found(payload_factory) -> None:
    payload = payload_factory(author_association="OWNER", comment_body="Please check @ㅋㅋ1")

    decision = is_allowed_issue_comment(
        payload=payload,
        mention_keywords=["@claude", "@ㅋㅋ", "@ㅋㅋ1"],
    )

    assert decision.allowed is True
    assert decision.reason == "accepted"


def test_parse_mention_target_map_warns_invalid_entries() -> None:
    with pytest.warns(RuntimeWarning, match="Invalid MENTION_TO_TMUX entries ignored"):
        parsed = _parse_mention_target_map("@claude=0:0.0,invalid,@ops=0:0.1,=bad,@x=")

    assert parsed == {"@claude": "0:0.0", "@ops": "0:0.1"}


def test_issue_state_filter_accepts_closed_action(payload_factory) -> None:
    payload = payload_factory(action="closed")

    decision = is_allowed_issue_state_event(payload=payload)

    assert decision.allowed is True
    assert decision.reason == "accepted"


def test_issue_state_filter_accepts_reopened_action(payload_factory) -> None:
    payload = payload_factory(action="reopened")

    decision = is_allowed_issue_state_event(payload=payload)

    assert decision.allowed is True
    assert decision.reason == "accepted"


def test_issue_state_filter_rejects_unsupported_action(payload_factory) -> None:
    payload = payload_factory(action="edited")

    decision = is_allowed_issue_state_event(payload=payload)

    assert decision.allowed is False
    assert decision.reason == "action_not_supported"


def test_accepted_associations_are_fixed_set() -> None:
    assert ACCEPTED_ASSOCIATIONS == {"OWNER", "MEMBER", "COLLABORATOR"}
