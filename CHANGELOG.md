# Changelog

## v0.1.0 — Initial release

- DWD-auth via service account
- Label-baserad thread-discovery (`auto-follow-up`)
- Klassificering med all state i Gmail-labels (per-message `drafted`)
- LLM-genererat **utkast** för både follow-up och svar — boten skickar aldrig
  själv, användaren klickar Send efter granskning
- Self-notification mail per utkast
- GitHub Actions cron under kontorstid mån–fre Europe/Stockholm
- Konfigurerbara overrides via repo-variables: `FOLLOWUP_MIN_HOURS`,
  `EXCLUDE_DOMAINS`, `EXCLUDE_ADDRESSES`, `EXTRA_CC`, `EXTRA_PROMPT`,
  `ANTHROPIC_MODEL`
- `--force-followup` flagga och `workflow_dispatch` input för att skippa
  tids-tröskeln för en enskild körning
