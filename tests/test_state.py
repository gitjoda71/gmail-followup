"""Smoke-tests för classify_thread — kärnan i botens beteende."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from gmail_followup.config import Config
from gmail_followup.gmail_client import ParsedMessage, ParsedThread
from gmail_followup.state import Action, classify_thread


OWNER = "owner@example.com"
OTHER = "kund@example.com"


def _cfg(**overrides) -> Config:
    base = dict(
        google_sa_key_path=Path("/dev/null"),
        delegated_user=OWNER,
        anthropic_api_key="sk-ant-test",
        anthropic_model="claude-sonnet-4-6",
        notify_to=("notify@example.com",),
        office_hour_start=9,
        office_hour_end=17,
        followup_min_hours=48,
        label_active="auto-follow-up",
        label_drafted="auto-follow-up-drafted",
        label_paused="auto-follow-up-paused",
        label_test="auto-follow-up-test",
        exclude_domains=(),
        exclude_addresses=(),
        extra_cc=(),
        extra_prompt="",
        timezone="Europe/Stockholm",
        dry_run=False,
    )
    base.update(overrides)
    return Config(**base)


def _msg(from_addr: str, hours_ago: float, *, label_ids=()) -> ParsedMessage:
    ts = datetime.now(timezone.utc) - timedelta(hours=hours_ago)
    return ParsedMessage(
        id=f"m-{from_addr}-{hours_ago}",
        thread_id="t1",
        message_id_header=f"<{from_addr}-{hours_ago}@example.com>",
        from_addr=from_addr,
        to_addrs=(OTHER if from_addr == OWNER else OWNER,),
        cc_addrs=(),
        subject="Test",
        body_text="hej",
        internal_date_ms=int(ts.timestamp() * 1000),
        label_ids=label_ids,
    )


def _thread(*messages: ParsedMessage) -> ParsedThread:
    label_ids = tuple({l for m in messages for l in m.label_ids})
    return ParsedThread(id="t1", messages=messages, label_ids=label_ids)


class _FakeClient:
    """Minimal duck-typed client för classify-testning."""

    def __init__(self, *, thread_has_paused: bool = False, message_drafted_ids: set[str] | None = None):
        self._paused = thread_has_paused
        self._drafted = message_drafted_ids or set()

    def has_label(self, thread, label_name):
        return self._paused and label_name == "auto-follow-up-paused"

    def message_has_label(self, message, label_name):
        return label_name == "auto-follow-up-drafted" and message.id in self._drafted


def test_empty_thread_is_skipped():
    res = classify_thread(_thread(), _FakeClient(), _cfg(), pending_draft_thread_ids=set())
    assert res.action == Action.EMPTY


def test_paused_thread_is_skipped():
    t = _thread(_msg(OWNER, 100))
    res = classify_thread(t, _FakeClient(thread_has_paused=True), _cfg(), pending_draft_thread_ids=set())
    assert res.action == Action.PAUSED


def test_owner_last_within_48h_waits():
    t = _thread(_msg(OWNER, 24))
    res = classify_thread(t, _FakeClient(), _cfg(), pending_draft_thread_ids=set())
    assert res.action == Action.WAITING


def test_owner_last_over_48h_triggers_followup():
    t = _thread(_msg(OWNER, 72))
    res = classify_thread(t, _FakeClient(), _cfg(), pending_draft_thread_ids=set())
    assert res.action == Action.NEEDS_FOLLOWUP_DRAFT


def test_pending_draft_prevents_followup_spam():
    t = _thread(_msg(OWNER, 72))
    res = classify_thread(
        t, _FakeClient(), _cfg(), pending_draft_thread_ids={"t1"}
    )
    assert res.action == Action.HAS_PENDING_DRAFT


def test_external_reply_triggers_draft():
    t = _thread(_msg(OWNER, 100), _msg(OTHER, 10))
    res = classify_thread(t, _FakeClient(), _cfg(), pending_draft_thread_ids=set())
    assert res.action == Action.NEEDS_REPLY_DRAFT


def test_external_reply_already_drafted_is_skipped():
    last = _msg(OTHER, 10)
    t = _thread(_msg(OWNER, 100), last)
    res = classify_thread(
        t,
        _FakeClient(message_drafted_ids={last.id}),
        _cfg(),
        pending_draft_thread_ids=set(),
    )
    assert res.action == Action.ALREADY_DRAFTED


def test_classify_is_case_insensitive_on_owner_addr():
    t = _thread(_msg(OWNER.upper(), 72))
    res = classify_thread(t, _FakeClient(), _cfg(), pending_draft_thread_ids=set())
    assert res.action == Action.NEEDS_FOLLOWUP_DRAFT


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
