"""Centraliserad config — läses från environment.

GH Actions cron sätter dessa via repo secrets. Lokalt: .env eller export.
"""
from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Config:
    google_sa_key_path: Path
    delegated_user: str
    anthropic_api_key: str
    anthropic_model: str
    notify_to: tuple[str, ...]
    office_hour_start: int
    office_hour_end: int
    followup_min_hours: int
    label_active: str
    label_drafted: str
    label_paused: str
    label_test: str
    exclude_domains: tuple[str, ...]
    exclude_addresses: tuple[str, ...]
    extra_cc: tuple[str, ...]
    extra_prompt: str
    timezone: str
    dry_run: bool


def _materialize_sa_key() -> Path:
    """Skaffa fram en path till SA-nyckeln.

    Två lägen:
    - GOOGLE_SA_KEY_PATH: pekar på fil (lokal körning).
    - GOOGLE_SA_KEY_JSON: hela JSON:en som env-var (GH Actions). Skrivs då
      till en temp-fil som existerar för processens livslängd.
    """
    path_env = os.environ.get("GOOGLE_SA_KEY_PATH")
    if path_env:
        p = Path(path_env)
        if not p.exists():
            raise FileNotFoundError(f"GOOGLE_SA_KEY_PATH points to missing file: {p}")
        return p

    json_env = os.environ.get("GOOGLE_SA_KEY_JSON")
    if json_env:
        # Verifiera att det är valid JSON innan vi skriver
        try:
            json.loads(json_env)
        except json.JSONDecodeError as e:
            raise ValueError(f"GOOGLE_SA_KEY_JSON is not valid JSON: {e}") from e
        fd, tmp_path = tempfile.mkstemp(suffix=".json", prefix="sa-key-")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(json_env)
        return Path(tmp_path)

    raise RuntimeError(
        "Either GOOGLE_SA_KEY_PATH or GOOGLE_SA_KEY_JSON must be set."
    )


def _require(name: str) -> str:
    val = os.environ.get(name)
    if not val:
        raise RuntimeError(f"Required env var missing: {name}")
    return val


def load_config(*, dry_run: bool = False) -> Config:
    return Config(
        google_sa_key_path=_materialize_sa_key(),
        delegated_user=_require("DELEGATED_USER"),
        anthropic_api_key=_require("ANTHROPIC_API_KEY"),
        anthropic_model=os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-6"),
        notify_to=tuple(
            addr.strip()
            for addr in _require("NOTIFY_TO").split(",")
            if addr.strip()
        ),
        office_hour_start=int(os.environ.get("OFFICE_HOUR_START", "9")),
        office_hour_end=int(os.environ.get("OFFICE_HOUR_END", "17")),
        followup_min_hours=int(os.environ.get("FOLLOWUP_MIN_HOURS", "48")),
        label_active=os.environ.get("LABEL_ACTIVE", "auto-follow-up"),
        label_drafted=os.environ.get("LABEL_DRAFTED", "auto-follow-up-drafted"),
        label_paused=os.environ.get("LABEL_PAUSED", "auto-follow-up-paused"),
        label_test=os.environ.get("LABEL_TEST", "auto-follow-up-test"),
        exclude_domains=tuple(
            d.strip().lower()
            for d in os.environ.get("EXCLUDE_DOMAINS", "").split(",")
            if d.strip()
        ),
        exclude_addresses=tuple(
            a.strip().lower()
            for a in os.environ.get("EXCLUDE_ADDRESSES", "").split(",")
            if a.strip()
        ),
        extra_cc=tuple(
            a.strip()
            for a in os.environ.get("EXTRA_CC", "").split(",")
            if a.strip()
        ),
        extra_prompt=os.environ.get("EXTRA_PROMPT", "").strip(),
        timezone=os.environ.get("TZ", "Europe/Stockholm"),
        dry_run=dry_run,
    )
