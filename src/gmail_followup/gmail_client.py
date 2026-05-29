"""Gmail-klient: DWD-auth, label/thread/draft/send-operations.

All state för boten är label-baserad. Allt skickande/utkastande sker i tråden
(threadId + In-Reply-To/References) så att Gmail håller ihop konversationen.
"""
from __future__ import annotations

import base64
import logging
import re
from dataclasses import dataclass
from email.mime.text import MIMEText
from email.utils import getaddresses, parseaddr
from pathlib import Path
from typing import Iterable

from google.oauth2 import service_account
from googleapiclient.discovery import build

logger = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/gmail.modify"]


@dataclass(frozen=True)
class ParsedMessage:
    id: str
    thread_id: str
    message_id_header: str  # "<...>" Message-ID från MIME-headers
    from_addr: str
    to_addrs: tuple[str, ...]
    cc_addrs: tuple[str, ...]
    subject: str
    body_text: str
    internal_date_ms: int  # epoch ms — Gmails egen timestamp
    label_ids: tuple[str, ...]


@dataclass(frozen=True)
class ParsedThread:
    id: str
    messages: tuple[ParsedMessage, ...]
    label_ids: tuple[str, ...]


class GmailClient:
    def __init__(
        self,
        sa_key_path: Path | str,
        delegated_user: str,
        *,
        gmail_service=None,  # injicerbar för test
    ):
        self.delegated_user = delegated_user
        if gmail_service is not None:
            self.svc = gmail_service
        else:
            creds = service_account.Credentials.from_service_account_file(
                str(sa_key_path), scopes=SCOPES
            ).with_subject(delegated_user)
            self.svc = build("gmail", "v1", credentials=creds, cache_discovery=False)

        # label-name → label-id cache
        self._label_cache: dict[str, str] | None = None

    # ------------------------------------------------------------------
    # Labels
    # ------------------------------------------------------------------

    def _load_labels(self) -> dict[str, str]:
        if self._label_cache is not None:
            return self._label_cache
        resp = self.svc.users().labels().list(userId="me").execute()
        labels = resp.get("labels", [])
        self._label_cache = {l["name"]: l["id"] for l in labels}
        return self._label_cache

    def get_or_create_label(self, name: str) -> str:
        """Returnera label-ID, skapa om den saknas."""
        labels = self._load_labels()
        if name in labels:
            return labels[name]
        body = {
            "name": name,
            "labelListVisibility": "labelShow",
            "messageListVisibility": "show",
        }
        resp = self.svc.users().labels().create(userId="me", body=body).execute()
        label_id = resp["id"]
        labels[name] = label_id
        logger.info(f"Skapade label {name!r} → {label_id}")
        return label_id

    def add_label(self, thread_id: str, label_name: str) -> None:
        label_id = self.get_or_create_label(label_name)
        self.svc.users().threads().modify(
            userId="me",
            id=thread_id,
            body={"addLabelIds": [label_id]},
        ).execute()

    def remove_label(self, thread_id: str, label_name: str) -> None:
        labels = self._load_labels()
        if label_name not in labels:
            return
        self.svc.users().threads().modify(
            userId="me",
            id=thread_id,
            body={"removeLabelIds": [labels[label_name]]},
        ).execute()

    def has_label(self, thread: ParsedThread, label_name: str) -> bool:
        labels = self._load_labels()
        target = labels.get(label_name)
        if not target:
            return False
        return target in thread.label_ids

    def add_label_to_message(self, message_id: str, label_name: str) -> None:
        """Label på en specifik message (för per-message state som 'drafted')."""
        label_id = self.get_or_create_label(label_name)
        self.svc.users().messages().modify(
            userId="me",
            id=message_id,
            body={"addLabelIds": [label_id]},
        ).execute()

    def message_has_label(self, message: ParsedMessage, label_name: str) -> bool:
        labels = self._load_labels()
        target = labels.get(label_name)
        if not target:
            return False
        return target in message.label_ids

    # ------------------------------------------------------------------
    # Threads
    # ------------------------------------------------------------------

    def list_thread_ids_with_label(self, label_name: str) -> list[str]:
        label_id = self.get_or_create_label(label_name)
        thread_ids: list[str] = []
        page_token: str | None = None
        while True:
            resp = (
                self.svc.users()
                .threads()
                .list(userId="me", labelIds=[label_id], pageToken=page_token, maxResults=100)
                .execute()
            )
            for t in resp.get("threads", []):
                thread_ids.append(t["id"])
            page_token = resp.get("nextPageToken")
            if not page_token:
                break
        return thread_ids

    def get_thread(self, thread_id: str) -> ParsedThread:
        raw = (
            self.svc.users()
            .threads()
            .get(userId="me", id=thread_id, format="full")
            .execute()
        )
        messages = tuple(_parse_message(m) for m in raw.get("messages", []))
        # Thread-level label-IDs = union av alla message-labels
        label_ids: set[str] = set()
        for m in messages:
            label_ids.update(m.label_ids)
        return ParsedThread(id=thread_id, messages=messages, label_ids=tuple(label_ids))

    # ------------------------------------------------------------------
    # Skicka / utkasta i tråden
    # ------------------------------------------------------------------

    def create_draft_in_thread(
        self,
        thread: ParsedThread,
        body_text: str,
        to_addrs: Iterable[str],
        cc_addrs: Iterable[str] = (),
    ) -> str:
        """Skapar utkast i tråden. Returnerar draft-ID."""
        raw = _build_reply_raw(
            thread=thread,
            from_addr=self.delegated_user,
            body_text=body_text,
            to_addrs=list(to_addrs),
            cc_addrs=list(cc_addrs),
        )
        resp = (
            self.svc.users()
            .drafts()
            .create(
                userId="me",
                body={"message": {"raw": raw, "threadId": thread.id}},
            )
            .execute()
        )
        return resp["id"]

    def list_pending_draft_thread_ids(self) -> set[str]:
        """ThreadIds som har minst ett pending draft hos delegated_user.

        Används för att inte dubbel-utkasta follow-ups: om användaren redan har
        ett utkast i en tråd lämnar vi den ifred tills han skickar eller
        raderar det.
        """
        thread_ids: set[str] = set()
        page_token: str | None = None
        while True:
            resp = (
                self.svc.users()
                .drafts()
                .list(userId="me", pageToken=page_token, maxResults=500)
                .execute()
            )
            for d in resp.get("drafts", []):
                msg = d.get("message") or {}
                tid = msg.get("threadId")
                if tid:
                    thread_ids.add(tid)
            page_token = resp.get("nextPageToken")
            if not page_token:
                break
        return thread_ids

    def send_standalone(
        self, to: str | Iterable[str], subject: str, body_text: str
    ) -> str:
        """Skicka ett nytt mail (inte i tråd) — används för self-notify.

        `to` kan vara en sträng eller en iterable av adresser; flera adresser
        kommer in i samma To-header som komma-separerad lista.
        """
        to_str = to if isinstance(to, str) else ", ".join(to)
        msg = MIMEText(body_text, "plain", "utf-8")
        msg["From"] = self.delegated_user
        msg["To"] = to_str
        msg["Subject"] = subject
        raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
        resp = (
            self.svc.users()
            .messages()
            .send(userId="me", body={"raw": raw})
            .execute()
        )
        return resp["id"]


# ------------------------------------------------------------------
# MIME parsing
# ------------------------------------------------------------------


def _decode_b64url(data: str) -> bytes:
    # Gmail returnerar base64url-encoded body parts
    padding = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(data + padding)


def _walk_parts(payload: dict):
    """Yield (mimeType, body-bytes-or-None) för alla parts rekursivt."""
    parts = payload.get("parts")
    if parts:
        for p in parts:
            yield from _walk_parts(p)
    else:
        body = payload.get("body", {}) or {}
        data = body.get("data")
        decoded = _decode_b64url(data) if data else None
        yield payload.get("mimeType", ""), decoded


_HTML_TAG_RE = re.compile(r"<[^>]+>")


def _extract_body_text(payload: dict) -> str:
    """Prioritera text/plain, fallback till text/html (strippad)."""
    plain: list[str] = []
    html: list[str] = []
    for mime, data in _walk_parts(payload):
        if not data:
            continue
        try:
            text = data.decode("utf-8", errors="replace")
        except Exception:
            continue
        if mime == "text/plain":
            plain.append(text)
        elif mime == "text/html":
            html.append(text)
    if plain:
        return "\n".join(plain).strip()
    if html:
        stripped = _HTML_TAG_RE.sub("", "\n".join(html))
        return stripped.strip()
    return ""


def _header_dict(headers: list[dict]) -> dict[str, str]:
    return {h["name"].lower(): h["value"] for h in headers}


def _parse_address_list(value: str) -> tuple[str, ...]:
    if not value:
        return ()
    return tuple(addr for _, addr in getaddresses([value]) if addr)


def _parse_message(raw_msg: dict) -> ParsedMessage:
    payload = raw_msg.get("payload", {}) or {}
    headers = _header_dict(payload.get("headers", []) or [])
    _, from_addr = parseaddr(headers.get("from", ""))
    return ParsedMessage(
        id=raw_msg["id"],
        thread_id=raw_msg["threadId"],
        message_id_header=headers.get("message-id", ""),
        from_addr=from_addr or "",
        to_addrs=_parse_address_list(headers.get("to", "")),
        cc_addrs=_parse_address_list(headers.get("cc", "")),
        subject=headers.get("subject", ""),
        body_text=_extract_body_text(payload),
        internal_date_ms=int(raw_msg.get("internalDate", "0")),
        label_ids=tuple(raw_msg.get("labelIds", []) or []),
    )


# ------------------------------------------------------------------
# Reply-build
# ------------------------------------------------------------------


def _build_references_header(thread: ParsedThread) -> str:
    """Bygg References-header från alla tidigare Message-IDs i tråden."""
    ids = [m.message_id_header for m in thread.messages if m.message_id_header]
    return " ".join(ids)


def _ensure_re_prefix(subject: str) -> str:
    if not subject:
        return "Re:"
    if subject.lower().startswith("re:"):
        return subject
    return f"Re: {subject}"


def _build_reply_raw(
    *,
    thread: ParsedThread,
    from_addr: str,
    body_text: str,
    to_addrs: list[str],
    cc_addrs: list[str],
) -> str:
    """Bygger en MIME-reply som Gmail kan tråda. Returnerar base64url-raw."""
    if not thread.messages:
        raise ValueError("Kan inte svara på tom tråd")
    last = thread.messages[-1]
    subject = _ensure_re_prefix(last.subject)
    in_reply_to = last.message_id_header

    msg = MIMEText(body_text, "plain", "utf-8")
    msg["From"] = from_addr
    msg["To"] = ", ".join(to_addrs)
    if cc_addrs:
        msg["Cc"] = ", ".join(cc_addrs)
    msg["Subject"] = subject
    if in_reply_to:
        msg["In-Reply-To"] = in_reply_to
    refs = _build_references_header(thread)
    if refs:
        msg["References"] = refs

    return base64.urlsafe_b64encode(msg.as_bytes()).decode()


def collect_reply_all_recipients(
    thread: ParsedThread,
    exclude: str,
    exclude_domains: Iterable[str] = (),
    exclude_addresses: Iterable[str] = (),
    extra_cc: Iterable[str] = (),
) -> tuple[list[str], list[str]]:
    """Returnera (to, cc) för reply-all baserat på trådens deltagare.

    Strategi: kolla senaste icke-egna meddelande för dess To/Cc. Om alla
    meddelanden är från dig, fallback till första meddelandets To/Cc.

    Tar bort delegated_user (du själv) ur listorna. Lägger till alla från-
    adresser i tråden som inte redan finns med (mottagar-listor kan missa
    senare påkopplade personer). Adresser i `exclude_domains` filtreras bort
    på domännivå; `exclude_addresses` filtreras bort på exakt-adress-nivå.
    `extra_cc` läggs alltid till i Cc (dedupas mot To/Cc).
    """
    exclude_lower = exclude.lower()
    excluded_domains_lower = {d.lower().lstrip("@") for d in exclude_domains if d}
    excluded_addrs_lower = {a.lower() for a in exclude_addresses if a}

    def _norm(addr: str) -> str:
        return addr.strip().lower()

    def _domain(addr: str) -> str:
        _, _, dom = _norm(addr).partition("@")
        return dom

    def _is_excluded(addr: str) -> bool:
        n = _norm(addr)
        if not n or n == exclude_lower:
            return True
        if n in excluded_addrs_lower:
            return True
        return _domain(addr) in excluded_domains_lower

    # Senaste icke-egna message — eller första om alla är dina
    pivot = None
    for m in reversed(thread.messages):
        if _norm(m.from_addr) != exclude_lower:
            pivot = m
            break
    if pivot is None:
        pivot = thread.messages[0]

    to_set: list[str] = []
    cc_set: list[str] = []
    seen: set[str] = set()

    def _add(bucket: list[str], addr: str) -> None:
        if _is_excluded(addr):
            return
        n = _norm(addr)
        if n in seen:
            return
        seen.add(n)
        bucket.append(addr)

    for addr in pivot.to_addrs:
        _add(to_set, addr)
    for addr in pivot.cc_addrs:
        _add(cc_set, addr)
    # Lägg till alla from-adresser från tråden (utom oss) som inte redan finns
    for m in thread.messages:
        if _norm(m.from_addr) != exclude_lower:
            _add(to_set, m.from_addr)

    # Extra CC — alltid med (dedupas mot redan tillagda)
    for addr in extra_cc:
        _add(cc_set, addr)

    return to_set, cc_set
