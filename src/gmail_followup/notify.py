"""Self-notification: maila användaren när ett utkast skapats."""
from __future__ import annotations

import logging

from . import mask_email, mask_emails
from .compose import truncate_preview
from .config import Config
from .gmail_client import GmailClient, ParsedThread

logger = logging.getLogger(__name__)


def _thread_link(thread_id: str) -> str:
    # Mail.google.com tål både u/0 och raw — u/0 är vanligast i användarens setup
    return f"https://mail.google.com/mail/u/0/#all/{thread_id}"


def notify_draft_created(
    client: GmailClient,
    cfg: Config,
    thread: ParsedThread,
    draft_kind: str,  # "follow-up" | "svar"
    draft_text: str,
) -> str:
    """Skicka kort notis till användaren om att ett utkast väntar."""
    original_subject = thread.messages[0].subject if thread.messages else "(utan rubrik)"
    latest = thread.messages[-1]
    preview = truncate_preview(draft_text)

    # OBS: subject + latest.from_addr går till notis-mailet (inte loggen), så
    # användaren själv ser dem. mask_email används bara i strukturerade loggar.
    subject = f"[gmail-followup] {draft_kind}-utkast klart: \"{original_subject}\""
    body = (
        f"Ett {draft_kind}-utkast har skapats i tråden \"{original_subject}\".\n"
        f"\n"
        f"Senaste meddelande från: {latest.from_addr}\n"
        f"Antal meddelanden i tråd: {len(thread.messages)}\n"
        f"\n"
        f"Öppna tråden: {_thread_link(thread.id)}\n"
        f"\n"
        f"--- UTKAST ---\n"
        f"{preview}\n"
        f"--- /UTKAST ---\n"
        f"\n"
        f"Granska, tweeka, och klicka Send i Gmail när det ser bra ut.\n"
    )
    msg_id = client.send_standalone(cfg.notify_to, subject, body)
    logger.info(f"Notis skickad till {mask_emails(cfg.notify_to)} (message {msg_id})")
    return msg_id
