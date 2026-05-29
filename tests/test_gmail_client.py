"""Smoke-tests för pure helpers i gmail_client."""
from __future__ import annotations

from gmail_followup.gmail_client import (
    ParsedMessage,
    ParsedThread,
    _build_references_header,
    _ensure_re_prefix,
    _parse_address_list,
    collect_reply_all_recipients,
)


OWNER = "owner@example.com"


def _m(from_addr, to_addrs=(), cc_addrs=(), msg_id="<x@y>") -> ParsedMessage:
    return ParsedMessage(
        id="m",
        thread_id="t",
        message_id_header=msg_id,
        from_addr=from_addr,
        to_addrs=to_addrs,
        cc_addrs=cc_addrs,
        subject="Test",
        body_text="hej",
        internal_date_ms=0,
        label_ids=(),
    )


def test_ensure_re_prefix_adds_when_missing():
    assert _ensure_re_prefix("Hello") == "Re: Hello"


def test_ensure_re_prefix_preserves_existing():
    assert _ensure_re_prefix("Re: Hello") == "Re: Hello"
    assert _ensure_re_prefix("RE: Hello") == "RE: Hello"  # bevarar case


def test_ensure_re_prefix_handles_empty():
    assert _ensure_re_prefix("") == "Re:"


def test_parse_address_list_extracts_emails():
    addrs = _parse_address_list("John <john@x.com>, jane@y.com")
    assert addrs == ("john@x.com", "jane@y.com")


def test_parse_address_list_empty():
    assert _parse_address_list("") == ()


def test_build_references_includes_all_message_ids():
    thread = ParsedThread(
        id="t",
        messages=(
            _m(OWNER, msg_id="<a@x>"),
            _m("other@x", msg_id="<b@x>"),
        ),
        label_ids=(),
    )
    refs = _build_references_header(thread)
    assert "<a@x>" in refs
    assert "<b@x>" in refs


def test_reply_all_excludes_owner():
    thread = ParsedThread(
        id="t",
        messages=(
            _m(OWNER, to_addrs=("kund@x.com", "kollega@x.com"), cc_addrs=()),
        ),
        label_ids=(),
    )
    to, cc = collect_reply_all_recipients(thread, exclude=OWNER)
    assert OWNER not in to and OWNER not in cc
    assert "kund@x.com" in to
    assert "kollega@x.com" in to


def test_reply_all_picks_latest_external_message_recipients():
    """Vid svar ska vi reply-alla mot motpartens to/cc, inte ägarens."""
    thread = ParsedThread(
        id="t",
        messages=(
            _m(OWNER, to_addrs=("kund@x.com",)),
            _m("kund@x.com", to_addrs=(OWNER, "extra@x.com"), cc_addrs=("cc1@x.com",)),
        ),
        label_ids=(),
    )
    to, cc = collect_reply_all_recipients(thread, exclude=OWNER)
    # to ska innehålla motparten själv (from-addr läggs till) + extra
    assert "kund@x.com" in to
    assert "extra@x.com" in to
    assert "cc1@x.com" in cc
    assert OWNER not in to and OWNER not in cc


def test_reply_all_dedupes_addresses():
    thread = ParsedThread(
        id="t",
        messages=(
            _m(OWNER, to_addrs=("kund@x.com",)),
            _m("kund@x.com", to_addrs=(OWNER,)),
        ),
        label_ids=(),
    )
    to, cc = collect_reply_all_recipients(thread, exclude=OWNER)
    assert to.count("kund@x.com") == 1


def test_reply_all_falls_back_to_first_when_all_owner():
    thread = ParsedThread(
        id="t",
        messages=(
            _m(OWNER, to_addrs=("kund@x.com",)),
            _m(OWNER, to_addrs=("kund@x.com",)),
        ),
        label_ids=(),
    )
    to, cc = collect_reply_all_recipients(thread, exclude=OWNER)
    assert "kund@x.com" in to


def test_reply_all_filters_excluded_domains():
    """Pivot = senaste icke-egna; exkluderade-domän-adresser filtreras bort."""
    thread = ParsedThread(
        id="t",
        messages=(
            _m(OWNER, to_addrs=("kund@x.com", "lamnat@excluded.example.com")),
            _m(
                "kund@x.com",
                to_addrs=(OWNER, "annan@y.com"),
                cc_addrs=("cc-lamnat@excluded.example.com", "kvar@z.com"),
            ),
        ),
        label_ids=(),
    )
    to, cc = collect_reply_all_recipients(
        thread, exclude=OWNER, exclude_domains=("excluded.example.com",)
    )
    flat = to + cc
    assert all("@excluded.example.com" not in addr.lower() for addr in flat)
    assert "kund@x.com" in to
    assert "annan@y.com" in to
    assert "kvar@z.com" in cc


def test_reply_all_filters_excluded_address():
    thread = ParsedThread(
        id="t",
        messages=(
            _m(OWNER, to_addrs=("kund@x.com", "lamnat@gmail.com", "kvar@gmail.com")),
        ),
        label_ids=(),
    )
    to, _ = collect_reply_all_recipients(
        thread, exclude=OWNER, exclude_addresses=("LAMNAT@gmail.com",)
    )
    assert "lamnat@gmail.com" not in [a.lower() for a in to]
    assert "kvar@gmail.com" in to


def test_reply_all_extra_cc_added():
    thread = ParsedThread(
        id="t",
        messages=(_m(OWNER, to_addrs=("kund@x.com",)),),
        label_ids=(),
    )
    to, cc = collect_reply_all_recipients(
        thread, exclude=OWNER, extra_cc=("colleague@example.com",)
    )
    assert "kund@x.com" in to
    assert "colleague@example.com" in cc


def test_reply_all_extra_cc_dedupes_with_to():
    """Om extra_cc redan finns som mottagare hamnar den inte i bägge."""
    thread = ParsedThread(
        id="t",
        messages=(_m(OWNER, to_addrs=("kollega@x.com",)),),
        label_ids=(),
    )
    to, cc = collect_reply_all_recipients(
        thread, exclude=OWNER, extra_cc=("kollega@x.com",)
    )
    assert "kollega@x.com" in to
    assert "kollega@x.com" not in cc


def test_reply_all_exclude_domains_is_case_insensitive():
    thread = ParsedThread(
        id="t",
        messages=(_m(OWNER, to_addrs=("Foo@EXCLUDED.example.com", "bar@x.com")),),
        label_ids=(),
    )
    to, _ = collect_reply_all_recipients(
        thread, exclude=OWNER, exclude_domains=("excluded.example.com",)
    )
    assert to == ["bar@x.com"]
