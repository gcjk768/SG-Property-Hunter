# propbot

A small scheduled bot that watches the Singapore property market, checks what the law lets you buy,
works out what you would pay upfront and every month, projects ten years under bear, base and bull
scenarios, and posts one item per Telegram message with a deterministic verdict.

All money maths is done in code from `rules/sg_property_rules.yaml`. Claude only writes the words.

## Build status

| Step | What | Status |
| --- | --- | --- |
| 1 | Config and safety floors, SQLite, rate limiter, polite fetcher, run lock, Telegram client, Claude wrapper, alerts | Done, tested |
| 2 | Rules file, loader, weekly checker, MRT pipeline | Done, tested; one real check call made |
| 3 | Eligibility, costs, financing, projection, verdict, `propbot analyse` | Done, tested. **Stopped here for the maths check** |
| 4 | data.gov.sg, URA, rates, OneMap, indices, `propbot backfill` | Next |
| 5 | Discovery, evidence, outlooks, curation, rendering, `propbot run --dry-run` | Later, then a second stop |
| 6 | Private chat, favourites, scheduler, status, Docker, compose, full README | Last |

The four worked example cards for the step 3 check are in [docs/worked_examples.md](docs/worked_examples.md).

## Try it

```
uv venv --python 3.12 .venv && . .venv/bin/activate
uv pip install -e '.[test]'
pytest
bash docs/worked_examples.sh            # the four worked examples
propbot analyse --help                  # type in your own figures
```

`propbot analyse` refuses to start while `profile.gross_monthly_income` is 0. Fill in the profile in
`config.yaml`, or pass `--set gross_monthly_income=6500` (and the same for `cash_available` and
`cpf_oa_balance`) for a one off run.

## How the rules file was checked

The build sandbox could not open iras.gov.sg, mas.gov.sg, hdb.gov.sg, cpf.gov.sg or ura.gov.sg directly.
Every figure was confirmed instead through each official site's own search results on 2026-10-02.
Those entries say `check_method: official_search_index`, and the quote is the search index text for
that page. The first weekly rules check on the NAS (which can reach those sites) reads every page again
and replaces each quote with the page's exact sentence. Until then, `propbot status` reports that a
check is needed.

Where the official site differed from the brief, the official site won:

- **ABSD count.** IRAS excludes homes outside Singapore from the ABSD count. The profile now has `properties_owned_outside_sg`.
- **15 month wait.** HDB removed the 15 month wait out in 2026 for private owners buying a resale flat without an HDB loan. It still applies with an HDB loan. The 30 month wait for BTO, EC and Plus or Prime resale is unchanged.
- **HDB loan tenure.** It is the shortest of 25 years, age 65 minus your age, and remaining lease minus 20 years.
- **MSR.** It applies to HDB flats and to ECs still within their MOP.
- **Plus and Prime resale buyers.** They need at least one citizen plus one citizen or PR, not an all citizen household.
- **Proximity Housing Grant.** Singles also get 10,000 to live near their parents.
- **MAS LTV page.** It has moved to `.../new-housing-loans/loan-tenure-and-loan-to-value-limits`.

Not confirmed, so not used in any calculation yet:

- **Raised income ceilings.** The 16,000 and 8,000 ceilings from the 2026 National Day Rally could not be confirmed. The file keeps the confirmed 14,000 and 7,000 and lists the raise as a pending change. Income between the two is shown as conditional.
- **EHG tiers.** The EHG tier table by income band was not found, so EHG shows as "up to" and is not counted.
- **CPF Housing Grant ceiling.** The grant's income ceiling is not in the file, so the grant shows as "up to" and is not counted.
- **Plus and Prime subsidy recovery.** HDB sets it per project. Without a project figure, the card uses the earlier PLH figure of 6% and labels it as an assumption.

Planning figures, labelled on every card: commercial LTV 70% and bank tenure capped at remaining lease
minus 20 years.

## pddbot

No pddbot checkout was available during the build, so `ratelimit.py`, `telegram.py`, `claude.py`,
`alerts.py` and `lock.py` were written fresh in the shape the brief describes. Compare them with
pddbot's versions before sharing a compose file.
