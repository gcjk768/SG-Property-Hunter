---
tags: [active]
updated: 2026-10-03
---
# App Overview

Telegram bot **@owner_sgproperty_bot** in the owner Channel (`<TELEGRAM_CHAT_ID>`), own forum topic
(`telegram.thread_id` in `config.yaml`). Runs on the NAS as the Docker stack `sg-property-hunter`
(`/volume1/docker/sg-property-hunter`), deployed with `./deploy.sh`. App vault (movement log, reports,
Profile.md): `/volume1/<USER>/Obsidian/SG Property Hunter`, mounted at `/vault`.

## What it does
- **Hourly HDB resale pulse** (`propbot/pulse.py`): data.gov.sg resale transactions (free, no key).
  It compares each sale with the 12-month median price per sqm for the same town, flat type and
  10-year lease band, and with HDB median rent. It posts only **new** deals that are 15% or more under
  that median or give 8% or more gross yield. Each deal is posted once (`pulse_alerted`). One API
  call an hour, plus a full refresh once a day.
- **Hourly listing hunt** (`propbot/hunt.py`, at :37): one claude -p haiku call with WebSearch/WebFetch, reusing `prompts/discover_*`. Code drops anything off `listing_domains_allowed`, over budget, a past sale, a short lease or already posted, then posts a header plus one card per new listing (at most 5). HDB listings get a comparison with recent town sales in the same lease band.
- **Commands** (`propbot/bot.py`), unique names because the group is shared: `/proppulse`, `/prophunt`,
  `/propanalyse`, `/propask` (claude -p haiku with WebSearch and vault memory), `/propstatus`, `/prophelp`.
  It answers only in its own topic or in the owner's private chat. Buttons may trigger only pulse, status and help.
- **Finance engine** (`propbot/engine/`): eligibility, costs, financing, a 10-year projection and a verdict
  from `rules/sg_property_rules.yaml`, behind `/propanalyse` and `propbot analyse`.

## Modules
- `propbot/cli.py`: `serve`, `pulse [--post]`, `analyse`, `status`, `rules-check`, `test-telegram`
- `propbot/telegram.py`: paced Bot API client, topics, inline buttons, plain-text resend when Telegram rejects the HTML
- `propbot/vault.py`: `Activity/YYYY/MM/YYYY-MM-DD.md`, `Reports/YYYY/MM/…`, `recent()` memory (4,000 chars)
- `propbot/render.py`: the analysis card in the fleet Telegram style; `SECTION_TITLES` holds the emoji per message type
- `propbot/claude.py`: bounded `claude -p` wrapper (12 calls a day)

## Ops
- Logs: `docker logs sg-property-hunter`. Health: `data/heartbeat` is touched on every long poll.
- Secrets live only in the NAS `.env` (`TELEGRAM_BOT_TOKEN`; `CLAUDE_CODE_OAUTH_TOKEN` copied from miles-chase).
