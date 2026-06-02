"""Entry point: en cron-körning.

Plockar alla trådar med `auto-follow-up`-label, klassificerar varje, och
agerar:
- NEEDS_FOLLOWUP_DRAFT → LLM-genererad uppföljning, utkasta i tråden, notifiera användaren
- NEEDS_REPLY_DRAFT    → LLM-genererat svar på motpart, utkasta i tråden,
                         labela motpart-meddelandet som drafted, notifiera användaren
- Övrigt → bara logga
"""
from __future__ import annotations

import argparse
import logging
import sys
import traceback

from . import mask_email, mask_emails
from .compose import compose_followup, compose_reply_draft
from .config import Config, load_config
from .gmail_client import GmailClient, ParsedThread, collect_reply_all_recipients
from .notify import notify_draft_created
from .state import Action, classify_thread

logger = logging.getLogger("gmail_followup")


def _setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s — %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def _process_thread(
    thread: ParsedThread,
    client: GmailClient,
    cfg: Config,
    *,
    pending_draft_thread_ids: set[str],
    force_followup: bool = False,
) -> None:
    classification = classify_thread(
        thread,
        client,
        cfg,
        pending_draft_thread_ids=pending_draft_thread_ids,
        force_followup=force_followup,
    )
    short_id = thread.id[-8:]
    logger.info(
        f"[{short_id}] → {classification.action.value} ({classification.reason})"
    )

    if classification.action == Action.NEEDS_FOLLOWUP_DRAFT:
        text = compose_followup(thread, cfg)
        to, cc = collect_reply_all_recipients(
            thread,
            exclude=cfg.delegated_user,
            exclude_domains=cfg.exclude_domains,
            exclude_addresses=cfg.exclude_addresses,
            extra_cc=cfg.extra_cc,
        )
        if cfg.dry_run:
            logger.info(
                f"[{short_id}] DRY-RUN: skulle skapa follow-up-draft till "
                f"{mask_emails(to)} cc={mask_emails(cc)}"
            )
            logger.info(f"[{short_id}] DRY-RUN: utkast-preview:\n{text[:400]}")
            return
        draft_id = client.create_draft_in_thread(thread, text, to, cc)
        logger.info(f"[{short_id}] Follow-up-draft skapad: {draft_id}")
        notify_draft_created(client, cfg, thread, draft_kind="follow-up", draft_text=text)
        return

    if classification.action == Action.NEEDS_REPLY_DRAFT:
        text = compose_reply_draft(thread, cfg)
        to, cc = collect_reply_all_recipients(
            thread,
            exclude=cfg.delegated_user,
            exclude_domains=cfg.exclude_domains,
            exclude_addresses=cfg.exclude_addresses,
            extra_cc=cfg.extra_cc,
        )
        last_msg = thread.messages[-1]
        if cfg.dry_run:
            logger.info(
                f"[{short_id}] DRY-RUN: skulle skapa reply-draft till "
                f"{mask_emails(to)} cc={mask_emails(cc)}"
            )
            logger.info(f"[{short_id}] DRY-RUN: utkast-preview:\n{text[:400]}")
            return
        draft_id = client.create_draft_in_thread(thread, text, to, cc)
        client.add_label_to_message(last_msg.id, cfg.label_drafted)
        logger.info(f"[{short_id}] Reply-draft skapad: {draft_id}, drafted-label satt")
        notify_draft_created(client, cfg, thread, draft_kind="svar", draft_text=text)
        return

    # ingen åtgärd — loggades redan ovan


def run(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="gmail-followup cron runner")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Klassificera och generera utkast-text men skapa/skicka inget",
    )
    parser.add_argument(
        "--force-followup",
        action="store_true",
        help="Skippa FOLLOWUP_MIN_HOURS-tröskeln — skapa follow-up direkt",
    )
    args = parser.parse_args(argv)

    _setup_logging()
    cfg = load_config(dry_run=args.dry_run)
    if args.dry_run:
        logger.info("DRY-RUN: inga drafts/labels/mails kommer skapas")
    if args.force_followup:
        logger.info("FORCE-FOLLOWUP: skippar FOLLOWUP_MIN_HOURS-tröskeln")
    if cfg.extra_prompt:
        logger.info(
            f"EXTRA_PROMPT aktiv ({len(cfg.extra_prompt)} tecken) — vävs in i "
            f"alla utkast denna körning. Rensa via `gh variable set EXTRA_PROMPT "
            f"-R <repo> --body ''` när det inte längre behövs."
        )

    client = GmailClient(cfg.google_sa_key_path, cfg.delegated_user)
    thread_ids = client.list_thread_ids_with_label(cfg.label_active)
    logger.info(f"Hittade {len(thread_ids)} tråd(ar) med label {cfg.label_active!r}")

    if not thread_ids:
        return 0

    # Hämta lista av thread-IDs med pending drafts en gång (cron-snabbare)
    pending = client.list_pending_draft_thread_ids()
    logger.info(f"{len(pending)} tråd(ar) har pending drafts hos {mask_email(cfg.delegated_user)}")

    errors = 0
    for tid in thread_ids:
        try:
            thread = client.get_thread(tid)
            _process_thread(
                thread,
                client,
                cfg,
                pending_draft_thread_ids=pending,
                force_followup=args.force_followup,
            )
        except Exception as e:
            errors += 1
            logger.error(f"Fel vid tråd {tid}: {e}\n{traceback.format_exc()}")

    if errors:
        logger.warning(f"Klar med {errors} fel av {len(thread_ids)} trådar")
        return 1
    logger.info(f"Klar — {len(thread_ids)} trådar processade utan fel")
    return 0


def main() -> None:
    sys.exit(run())


if __name__ == "__main__":
    main()
