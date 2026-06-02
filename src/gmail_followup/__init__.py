"""gmail-followup — auto-followups + LLM reply-drafts for labeled Gmail threads."""
from __future__ import annotations

from typing import Iterable

__version__ = "0.1.0"


def mask_email(addr: str) -> str:
    """Maska email-adress för loggar.

    `jonas.hammarstedt@riksbyggen.se` → `jon***@rik***.se`
    Domänens TLD bevaras (mest att hjälpa läsbarheten i loggen utan att
    läcka full identitet). Ej-email-strängar returneras som `***`.
    """
    if not addr or "@" not in addr:
        return "***"
    local, _, domain = addr.partition("@")
    if "." in domain:
        dom_root, _, dom_tld = domain.rpartition(".")
        return f"{local[:3]}***@{dom_root[:3]}***.{dom_tld}"
    return f"{local[:3]}***@{domain[:3]}***"


def mask_emails(addrs: Iterable[str]) -> str:
    return ", ".join(mask_email(a) for a in addrs)
