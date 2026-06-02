"""Classifier: tråd → vad ska boten göra?

State-modell:
- Inga externa databaser. All state ligger i Gmail (thread- och message-labels +
  thread-message-historik).
- Per-message `drafted`-label markerar "vi har redan utkastat ett svar på just
  detta motpart-meddelande" så att vi inte dubbel-utkastar varje cron-run.
- "användaren har ett utkast liggande" detekteras via separat drafts.list — då
  väntar vi i stället för att skapa ett till.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum

from . import mask_email
from .config import Config
from .gmail_client import GmailClient, ParsedThread


class Action(str, Enum):
    EMPTY = "empty"
    PAUSED = "paused"
    WAITING = "waiting"
    HAS_PENDING_DRAFT = "has_pending_draft"
    NEEDS_FOLLOWUP_DRAFT = "needs_followup_draft"
    NEEDS_REPLY_DRAFT = "needs_reply_draft"
    ALREADY_DRAFTED = "already_drafted"


@dataclass(frozen=True)
class Classification:
    action: Action
    reason: str  # mänskligt läsbar — loggas


def classify_thread(
    thread: ParsedThread,
    client: GmailClient,
    cfg: Config,
    *,
    pending_draft_thread_ids: set[str],
    now: datetime | None = None,
    force_followup: bool = False,
) -> Classification:
    """Avgör vad som ska göras med tråden.

    `force_followup=True` skippar `FOLLOWUP_MIN_HOURS`-tröskeln så att en
    follow-up skapas direkt, även om tråden bara är några minuter gammal.
    Påverkar inte reply-draft-flödet eller pending-draft-check.
    """
    now = now or datetime.now(timezone.utc)

    if not thread.messages:
        return Classification(Action.EMPTY, "ingen meddelandehistorik")

    if client.has_label(thread, cfg.label_paused):
        return Classification(Action.PAUSED, f"label {cfg.label_paused!r} satt")

    last = thread.messages[-1]
    last_is_from_owner = last.from_addr.lower() == cfg.delegated_user.lower()

    if last_is_from_owner:
        # Senaste är från användaren — kandidat för follow-up
        last_dt = datetime.fromtimestamp(last.internal_date_ms / 1000, tz=timezone.utc)
        hours_since = (now - last_dt).total_seconds() / 3600
        if not force_followup and hours_since < cfg.followup_min_hours:
            return Classification(
                Action.WAITING,
                f"endast {hours_since:.1f}h sedan senaste meddelande (kräver "
                f"≥{cfg.followup_min_hours}h)",
            )
        if thread.id in pending_draft_thread_ids:
            return Classification(
                Action.HAS_PENDING_DRAFT,
                "befintligt utkast väntar på användaren att skicka",
            )
        return Classification(
            Action.NEEDS_FOLLOWUP_DRAFT,
            f"≥{cfg.followup_min_hours}h sedan senaste meddelande, inget pending utkast",
        )

    # Senaste är från motpart — kandidat för reply-draft
    if client.message_has_label(last, cfg.label_drafted):
        return Classification(
            Action.ALREADY_DRAFTED,
            f"label {cfg.label_drafted!r} redan satt på senaste meddelande",
        )
    return Classification(
        Action.NEEDS_REPLY_DRAFT,
        f"motpart har svarat (från {mask_email(last.from_addr)}), inget utkast skapat ännu",
    )
