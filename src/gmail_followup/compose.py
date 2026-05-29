"""LLM-genererat utkastinnehåll — både follow-ups och svar.

Båda funktionerna får hela trådens meddelanden som kontext och användarens egna
tidigare meddelanden som stilreferens. LLM:en instrueras att matcha ton
och språk, och att hålla det kort.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
import anthropic

from .config import Config
from .gmail_client import ParsedMessage, ParsedThread

logger = logging.getLogger(__name__)


_MAX_BODY_CHARS = 4000  # cap per meddelande för att hålla prompten rimlig
_MAX_TOKENS = 1024


def _truncate(text: str, limit: int = _MAX_BODY_CHARS) -> str:
    text = text.strip()
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n[...truncated, ursprunglig {len(text)} tecken]"


def _format_message(m: ParsedMessage, owner_addr: str) -> str:
    role = "DU (användaren)" if m.from_addr.lower() == owner_addr.lower() else "MOTPART"
    dt = datetime.fromtimestamp(m.internal_date_ms / 1000, tz=timezone.utc)
    return (
        f"=== {role} — {m.from_addr} — {dt.strftime('%Y-%m-%d %H:%M UTC')} ===\n"
        f"Subject: {m.subject}\n\n{_truncate(m.body_text)}"
    )


def _format_thread(thread: ParsedThread, owner_addr: str) -> str:
    return "\n\n".join(_format_message(m, owner_addr) for m in thread.messages)


def _owner_messages(thread: ParsedThread, owner_addr: str) -> list[ParsedMessage]:
    return [m for m in thread.messages if m.from_addr.lower() == owner_addr.lower()]


def _client(cfg: Config) -> anthropic.Anthropic:
    return anthropic.Anthropic(api_key=cfg.anthropic_api_key)


def _extra_prompt_block(cfg: Config) -> str:
    """Om EXTRA_PROMPT är satt, returnera ett block att flika in i prompten."""
    if not cfg.extra_prompt:
        return ""
    return (
        "\n\nVIKTIGT ATT VÄVA IN I DETTA UTKAST (användaren har lagt till en specifik "
        f"instruktion för pågående utkast):\n{cfg.extra_prompt}"
    )


_SYSTEM_TONE_GUIDANCE = (
    "Du skriver mail-utkast åt användaren som han kommer granska innan sändning. "
    "Hård krav:\n"
    "- Matcha tonen och det språk (svenska/engelska) användaren använt i tidigare meddelanden i tråden.\n"
    "- Skriv kort — det här är ett mail, inte en essä.\n"
    "- Inga inledande 'Hej!' eller markdown-formatering om användaren inte gör det själv.\n"
    "- Skriv ENDAST mail-bodyn. Ingen subject-line, ingen signatur om användaren inte använder en.\n"
    "- Skriv ingen extra meta-text, inga förklaringar — bara texten användaren ska skicka."
)


def compose_followup(thread: ParsedThread, cfg: Config) -> str:
    """Generera uppföljningstext för en tråd där motparten inte svarat."""
    owner_msgs = _owner_messages(thread, cfg.delegated_user)
    if not owner_msgs:
        # Borde inte hända — vi följer bara upp efter användaren-meddelanden.
        raise ValueError("Inga användaren-meddelanden i tråden att följa upp efter")

    prior_followups = "\n\n---\n\n".join(
        _truncate(m.body_text, 1500) for m in owner_msgs[1:]  # alla utom första
    )
    prior_section = (
        f"TIDIGARE UPPFÖLJNINGAR (du har redan skickat dessa — undvik att upprepa exakt ordval):\n{prior_followups}"
        if prior_followups
        else "Inga tidigare uppföljningar — detta är den första."
    )

    prompt = f"""Här är en e-posttråd där motparten inte svarat trots användarens meddelande(n). Skriv en kort, vänlig uppföljning som ber om svar på de frågor som ännu inte besvarats.

Identifiera först vilka frågor/handlingsanmodningar i användarens tidigare meddelanden som motparten inte adresserat, och rikta uppföljningen mot dem.

FULL TRÅD:
{_format_thread(thread, cfg.delegated_user)}

{prior_section}

Skriv en NY uppföljning. Variera ordvalet jämfört med tidigare uppföljningar men håll samma kärnfrågor. Maximalt ~5 meningar.{_extra_prompt_block(cfg)}"""

    msg = _client(cfg).messages.create(
        model=cfg.anthropic_model,
        max_tokens=_MAX_TOKENS,
        system=_SYSTEM_TONE_GUIDANCE,
        messages=[{"role": "user", "content": prompt}],
    )
    text = _extract_text(msg)
    logger.info(f"Follow-up genererad ({len(text)} tecken) för tråd {thread.id}")
    return text


def compose_reply_draft(thread: ParsedThread, cfg: Config) -> str:
    """Generera svar på det senaste motparts-meddelandet i tråden."""
    last = thread.messages[-1]
    if last.from_addr.lower() == cfg.delegated_user.lower():
        raise ValueError("Senaste meddelandet är från användaren — inget att svara på")

    prompt = f"""Här är en e-posttråd. Motparten har precis svarat (senaste meddelandet). Skriv ett svar från användaren som adresserar vad motparten faktiskt skrev.

FULL TRÅD:
{_format_thread(thread, cfg.delegated_user)}

Skriv ett svar på motpartens senaste meddelande. Matcha tonen från användarens tidigare meddelanden i tråden. Om motparten ställde frågor, försök svara så långt det går — men markera tydligt med [?] där användaren måste fylla i en uppgift som du inte vet (t.ex. priser, datum, tekniska beslut). Maximalt ~10 meningar.{_extra_prompt_block(cfg)}"""

    msg = _client(cfg).messages.create(
        model=cfg.anthropic_model,
        max_tokens=_MAX_TOKENS,
        system=_SYSTEM_TONE_GUIDANCE,
        messages=[{"role": "user", "content": prompt}],
    )
    text = _extract_text(msg)
    logger.info(f"Reply-utkast genererat ({len(text)} tecken) för tråd {thread.id}")
    return text


def _extract_text(msg: anthropic.types.Message) -> str:
    parts: list[str] = []
    for block in msg.content:
        if getattr(block, "type", None) == "text":
            parts.append(block.text)
    return "\n".join(parts).strip()


def truncate_preview(text: str, limit: int = 600) -> str:
    """Kort preview av drafttext för notis-mail."""
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "\n[...]"
