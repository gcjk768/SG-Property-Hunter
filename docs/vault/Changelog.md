---
tags: [active]
updated: 2026-10-03
---
# Changelog

## 2026-10-03
- feat: hunt post groups listings by estate (A to Z, 📍 YISHUN · n for sale), then by property type (BTO, HDB resale, EC, condo…); each estate is its own Telegram message (never mixed), after a header and summary message; `hunt.estate_blocks` splits an over-long estate by type, repeating its heading
- feat: 🔮 value outlook on every card: hunt listings get Claude's estimated % change over 5 years (BTO 10) in the same hunt call; pulse deals get one batched no-tools claude -p haiku call (`prompts/outlook_*`), skipped quietly if Claude is unavailable
- fix: shophouses, HDB shops, coffee shops and strata commercial never showed because the S$2.5M budget cut them; categories now take an optional `budget_max_sgd` (8M, 5M, 15M, 4M) and the hunt asks for at least one per category
- feat: pulse and hunt use the SG car tracker bot layout (header · date, summary counts, numbered cards with the linked name, collapsed notes last)
- feat: scheduled checks pause during the US regular session (trading desk hours, New York time so DST is right)
- feat: hunt requires a unit listing page for resale (contact the agent); new launch, BTO and EC may link the project page
- feat: pulse lists every notable deal (no top 8 cap) and every deal links to listings in its block
- feat: hourly listing hunt (propbot/hunt.py): claude -p haiku web search for real listings for sale, checked for allowed site, budget, lease and repeats, one card per listing; /prophunt; runs at :37 every hour like the rest of the fleet
- chore: Claude daily cap 12 to 40 (floor 48) for the hourly haiku hunt; all claude -p calls on haiku
- feat: Telegram bot @owner_sgproperty_bot with `propbot serve` (listener plus hourly scheduler), topic filter and button whitelist
- feat: hourly HDB resale pulse from data.gov.sg (lease-band medians, HDB median rent yield, posts new deals only)
- feat: /propask (claude -p haiku, WebSearch, vault memory), /propanalyse over Telegram, /propstatus, /prophelp
- feat: Dockerfile, docker-compose.yml, deploy.sh for the NAS; app vault in the fleet layout (Activity/YYYY/MM)
- refactor: analysis card in the fleet Telegram style; plain-text resend on HTML errors
- chore: dropped unused deps (pandas, numpy, bs4, lxml, selectolax, APScheduler); tests run on Windows (symlink fallback, pid check)
