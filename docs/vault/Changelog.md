---
tags: [active]
updated: 2026-10-03
---
# Changelog

## 2026-10-03
- feat: hourly listing hunt (propbot/hunt.py): claude -p haiku web search for real listings for sale, checked for allowed site, budget, lease and repeats, one card per listing; /prophunt; runs at :37 every hour like the rest of the fleet
- chore: Claude daily cap 12 to 40 (floor 48) for the hourly haiku hunt; all claude -p calls on haiku
- feat: Telegram bot @jameskoh_sgproperty_bot with `propbot serve` (listener plus hourly scheduler), topic filter and button whitelist
- feat: hourly HDB resale pulse from data.gov.sg (lease-band medians, HDB median rent yield, posts new deals only)
- feat: /propask (claude -p haiku, WebSearch, vault memory), /propanalyse over Telegram, /propstatus, /prophelp
- feat: Dockerfile, docker-compose.yml, deploy.sh for the NAS; app vault in the fleet layout (Activity/YYYY/MM)
- refactor: analysis card in the fleet Telegram style; plain-text resend on HTML errors
- chore: dropped unused deps (pandas, numpy, bs4, lxml, selectolax, APScheduler); tests run on Windows (symlink fallback, pid check)
