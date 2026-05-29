# gmail-followup

Automatiserade uppföljningar och LLM-genererade svarsutkast för taggade Gmail-trådar.

## Vad gör den?

Varje timme under kontorstid (mån–fre 09–17 Europe/Stockholm) körs en cron-job som:

1. **Plockar trådar** taggade med Gmail-label `auto-follow-up` i din Workspace-inbox.
2. **För varje tråd:**
   - Om senaste meddelandet är från **dig** och **≥`FOLLOWUP_MIN_HOURS`** har gått → genererar en LLM-skriven uppföljning som **utkast** i tråden.
   - Om senaste meddelandet är från **motparten** och inget utkast finns ännu → skapar Gmail-utkast med LLM-skrivet svar + mailar en notis.
   - Annars: lämnar tråden ifred (väntar, redan utkastat, eller pausad).

All state ligger i Gmail-labels — ingen extern databas. Boten skickar aldrig själv — du klickar Send efter granskning.

## Labels

| Label | Effekt |
|---|---|
| `auto-follow-up` | Aktiverar tråden för bot-hantering |
| `auto-follow-up-drafted` | Sätts av boten när utkast skapats — re-draftas ej |
| `auto-follow-up-paused` | (v0.3) Fryser tråden tillfälligt |
| `auto-follow-up-test` | (v0.2) Dry-run, loggar utan att skicka/utkasta |

## Setup

### En gång per repo

```bash
pip install -e .
```

### GitHub Actions secrets (måste sättas före första körning)

| Secret | Värde |
|---|---|
| `GOOGLE_SA_KEY_JSON` | Hela service account JSON-nyckeln som sträng |
| `DELEGATED_USER` | Workspace-adress att impersonera |
| `ANTHROPIC_API_KEY` | sk-ant-... |
| `NOTIFY_TO` | Komma-separerad lista av notis-mottagare |

### Repo variables (valfria overrides)

| Variable | Effekt |
|---|---|
| `FOLLOWUP_MIN_HOURS` | Min timmar mellan follow-ups (default 48) |
| `EXCLUDE_DOMAINS` | Komma-sep domäner att filtrera bort från reply-all |
| `EXCLUDE_ADDRESSES` | Komma-sep adresser att filtrera bort från reply-all |
| `EXTRA_CC` | Komma-sep adresser som alltid läggs till i Cc |
| `EXTRA_PROMPT` | Fri text som vävs in i alla utkast denna körning |
| `ANTHROPIC_MODEL` | Modellnamn (default `claude-sonnet-4-6`) |

### Aktivera en tråd

1. Öppna tråden i Gmail.
2. Sätt label `auto-follow-up`.
3. Klart — nästa cron-körning plockar upp den.

## Lokal körning

```bash
export GOOGLE_SA_KEY_PATH=/path/to/service-account.json
export DELEGATED_USER=user@example.com
export ANTHROPIC_API_KEY=sk-ant-...
export NOTIFY_TO=notify@example.com
python -m gmail_followup
```

Lägg till `--dry-run` för att se vad som skulle hänt utan att skicka/utkasta.
Lägg till `--force-followup` för att skippa `FOLLOWUP_MIN_HOURS`-tröskeln.

## Säkerhet

Säkerhetsrapporter: öppna ett issue.
