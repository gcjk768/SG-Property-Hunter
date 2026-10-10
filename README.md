# propbot

A small scheduled bot that watches the Singapore property market, checks what the law lets you buy,
works out what you would pay upfront and every month, projects ten years under bear, base and bull
scenarios, and posts one item per Telegram message with a deterministic verdict.

All money maths is done in code from `rules/sg_property_rules.yaml`. Claude only writes the words.

## How it fits together

![propbot architecture: official data, listings, the rules file and your vault notes flow into a code only finance engine; Claude finds listings and writes the words; the renderer posts one item per message to Telegram and saves every action to SQLite and the Obsidian vault; a website lane turns the tracked listings into one data file served by a web container through a public tunnel](docs/architecture.svg)

Made with draw.io. Open
[docs/architecture.drawio](docs/architecture.drawio) in draw.io desktop or diagrams.net, where every logo is
embedded. To redraw both files after a change, edit the layout in `docs/diagram/build.py` and run
`python docs/diagram/build.py`.

Logos: Telegram, Docker, Claude, Obsidian, SQLite and Python are the official brand marks from
[simple-icons](https://simpleicons.org) (CC0, trademarks belong to their owners). The other icons are from
[Lucide](https://lucide.dev) (ISC). Both licences are in `docs/diagram/icons`.

## Build status

| Step | What | Status |
| --- | --- | --- |
| 1 | Config and safety floors, SQLite, rate limiter, polite fetcher, run lock, Telegram client, Claude wrapper, alerts | Done, tested |
| 2 | Rules file, loader, weekly checker, MRT pipeline | Done, tested |
| 3 | Eligibility, costs, financing, projection, verdict, `propbot analyse` | Done, tested |
| 4 | data.gov.sg HDB sales and rents, MRT exits, OneMap geocoding, `propbot backfill` | Done and tested. Live SORA rates not built |
| 5 | Hourly HDB pulse, condo pulse, listing hunt with data outlook and MRT line | Done, tested. The full daily discover, curate and `propbot run` pipeline is not built |
| 6 | Telegram bot, per user profiles, scheduler, status, Docker, compose, setup guide | Done. Favourites and `propbot purge` are not built |

The four worked example cards are in [docs/worked_examples.md](docs/worked_examples.md).
New here? Start with [docs/SETUP.md](docs/SETUP.md).

## What it posts

| Command | What | Needs |
| --- | --- | --- |
| `/proppulse` | HDB resale sales that are cheap for their town, flat type and lease band, or high yield | nothing |
| `/propcondoreport` | Condo PDF now (resale and new launch, ranked); also posted by itself every Sunday 09:00 | Owner only, two Claude calls |
| `/prophunt` | Real listings found by Claude web search, grouped by estate | Claude token, owner only |
| `/propanalyse` | Full card for one property: upfront cost, loan, stress tests, 10 year outlook, verdict | your profile |
| `/propprofile` | Set your own income, cash and CPF (friends) | nothing |
| `/propask` | Ask anything about SG property | Claude token, owner only |
| `/propstatus`, `/prophelp` | Data, schedule, budgets, command list | nothing |

### Family website: The Block Ledger

A public web page for family and friends. It lists every live listing the bot tracks and explains each one in plain words,
so people who never use Telegram can browse, filter, save and share. Nothing on it is personal: money figures are for a
typical first-time Singapore citizen couple, never from `Profile.md`.

**How the flow works**

1. **Collect.** The hourly Claude hunt and a direct PropNex read (HDB, EC, condo and retail: shophouse, HDB shop, coffee
   shop and F&B, mall shop) record every listing in the tracker. EdgeProp pages give a real photo. PropertyGuru and
   CommercialGuru are opened only through a real Chrome on the NAS (`tools/pg_verify_server.py`, `propbot/pgcheck.py`), and a card
   shows once its page was opened that day and price and name match. Ohmyhome and other sites that answer automated requests with a challenge are never fetched.
2. **Enrich.** Once an hour, `propbot/site.py` takes each live listing and adds facts from open data
   (`propbot/insights.py`, `propbot/fengshui.py`): the block's recent sales by floor band, a verdict, the price
   outlook, what a buyer would pay, rent and yield, schools, hawker centre, polyclinic, MRT, and a feng shui reading.
3. **Publish.** The result is written as one data file in a single atomic step, so a reader never sees half of it.
4. **Serve.** A small static web container serves the page and that file. The page does all sorting and filtering
   in the browser.
5. **Reach.** A separate network node with HTTPS exposes only that container to the internet. The NAS itself and its
   other apps are not exposed, and no router port is opened.

**What each card shows**

- **Verdict and why.** Top pick, Worth a look, Fair price, Short lease, Above market or Not enough data, with reasons:
  price against the same block's sales over 12 months, lease left and CPF use, price cuts, MRT distance.
- **Outlook (appreciation or depreciation).** The likely value change over 5 years and the data behind it: the town's
  resale price trend (counted at no more than the base growth cap), lease ageing from the decay table, planned MRT
  stations, and the price against the block. It is labelled with its basis, and says "Not enough data" when a type has
  no trend (condos and commercial units show lease ageing only).
- **Recent sales in this block.** The last sales with floor and size, and the middle price by floor band, so a high
  floor is not compared with a low one.
- **What you would pay.** Down payment, monthly instalment on an HDB loan and a bank loan, stamp duty and the grants a
  first-time family may get, all from the rules file through the same engine as `/propanalyse`.
- **Rent and nearby.** HDB median rent and gross yield, primary schools within 1 km and 2 km, nearest hawker centre
  and polyclinic.
- **Feng shui.** The good and the bad from open data (water, hills and parks; cemeteries, columbaria, hospitals; the
  MRT; the digits of the block and price), plus a viewing guide for what only a visit shows.
- **Price history.** Days on the market and any price cut.

**Using the page.** Tabs by property type, a Recommended tab and a Saved tab (a shortlist kept in the browser), an
area filter, a price cap, a "found today / 3 days / 7 days" filter, search, and 11 sort orders. Each card has Share.
An Upcoming BTO tab lists open and upcoming projects, refreshed weekly by one Claude web search.

**Limits worth knowing.** The flat type is inferred from the price when a listing does not state it, so a wrong guess
shifts the block comparison. "Rough guide" cards rest on few sales. Condos and commercial units have no sales
benchmark here. The page says so in a "Worth checking yourself" box.

Every card carries three signals beyond the price.

- **🔮 Outlook.** The 5 year value change (10 for BTO). Where there is data it is labelled `data`: the town's or project's own resale price trend over the last years, capped at the base growth cap in `config.yaml`, then reduced by the lease decay table. Where there is no data it is labelled `est` and is Claude's rough guess.
- **🚇 Location.** The nearest MRT exit by straight line, and planned stations from `rules/mrt_pipeline.yaml` matched by name. The distance needs a free OneMap login (`ONEMAP_EMAIL` and `ONEMAP_PASSWORD`, which renew themselves) or a pasted `ONEMAP_ACCESS_TOKEN` (about 3 days).
- **⚠️ Rate stress** (on `/propanalyse`, bank loans only). The monthly instalment if rates rise 1 and 2 points, and how far that is over your own limit. An HDB loan rate is pegged to the CPF rate, so it is not shown for those.

## Try it

```
uv venv --python 3.12 .venv && . .venv/bin/activate
uv pip install -e '.[test]'
pytest
propbot backfill                        # one time: HDB sales since 2017, for trends and the backtest
propbot pulse                           # print the HDB deals, send nothing
propbot analyse --help                  # type in your own figures
propbot backtest                        # check the lease decay table against real repeat sales
```

`propbot analyse` refuses to start while `profile.gross_monthly_income` is 0. Fill in the profile in
`config.yaml`, or pass `--set gross_monthly_income=6500` (and the same for `cash_available` and
`cpf_oa_balance`) for a one off run. Full steps for a new user, including the Telegram bot, are in
[docs/SETUP.md](docs/SETUP.md).

## Obsidian vault on the NAS

propbot reads its inputs from your Obsidian vault and writes every action to it. Mount the vault
folder into the container at `/vault`; propbot only creates and writes `/vault/propbot`. If the
mount is missing, propbot carries on, notes it in its log, and never creates a stand in folder.

```yaml
# docker-compose.yml, under the propbot service (step 6 adds the full file)
volumes:
  - /volume1/Obsidian/MyVault:/vault      # your vault path on the UGREEN NAS
```

What propbot reads:

- **Profile.md.** Frontmatter fields override the profile in `config.yaml`, so you can edit your income, cash or CPF from Obsidian on your phone. Commands you type with `/set` or `--set` still win over it.
- **Watchlist.md.** One property per bullet, in the `/analyse` order. `propbot analyse --watchlist` turns each one into a card note.

What propbot writes:

- **Activity/yyyy-mm.md.** One line per action: cards, Telegram sends and deletes, rules checks, Claude calls, alerts and favourites. Web fetches go to a separate `yyyy-mm web.md`.
- **Cards/yyyy-mm-dd/.** One note per item, with frontmatter (price, verdict, score, IRR, upfront and more) that Dataview can query. Anything you write under `## My notes` survives when the card is rewritten.
- **Daily/yyyy-mm-dd.md.** The day's index, linking every card (filled by the daily run from step 5).
- **Rules/Rules.md and Rules/Changes.md.** Current rule values and every change found by the weekly check.
- **Favourites.md.** Posts you saved from the private chat (step 6).

propbot never deletes a note and never writes outside its folder. The SQLite database stays the
record the bot runs on; the vault is the copy you read and edit. Set `obsidian.enabled: false` to turn it off.

## How to judge appreciation or depreciation

A property's value moves with two forces: what wears it down, and what lifts it. Judge them
separately, then combine them. The projection code in `propbot/engine/projection.py` does exactly this.
It takes a yearly market growth rate for each scenario, subtracts a lease decay set by the remaining
lease (`lease_decay` in `config.yaml`), and works out what you would net on sale after costs.
The scenarios show what your assumptions imply. They do not predict the future.

**What pushes value down**

- **Lease decay.** Leasehold value falls as the remaining lease shortens, and the fall speeds up below about 60 years. Freehold has none.
- **Age and condition.** Older buildings need more maintenance, and the land share of the price shrinks.
- **Oversupply.** Many launches nearby cap rents and resale prices.
- **Financing limits.** Banks and CPF restrict loans on short leases, which shrinks the buyer pool when you sell.

**What pushes value up**

- **Location.** MRT access, including planned lines, plus schools, jobs nearby and URA Master Plan zoning.
- **Land share.** Freehold, low density sites and older estates with collective sale potential hold value better.
- **Scarcity and demand.** Limited supply, strong rental demand and a wide pool of eligible buyers.
- **Market cycle.** Interest rates, cooling measures and the overall price index move every property together.

**How to measure it instead of guessing**

1. Compare recent transactions of similar units in the same project and nearby, on price per square foot (URA, SRX, data.gov.sg).
2. Check the project's own past resale gains, including how units of the same age sold.
3. Estimate yearly growth as market growth minus lease decay. Run a low, middle and high case, then check the net after stamp duties, selling costs and loan paydown.
4. Compare rental yield with the mortgage cost. If yield is weak and the lease is decaying, price gains must carry the whole return.

The value outlook on each Telegram card is a trend, not a forecast. Where it says `data`, it continues the
town's or project's own past price trend, capped, with the lease decay table applied. A boom in the
past five years is not a promise of another one, which is why the cap exists. Check steps 1 and 2 yourself
before you rely on it. This tool is not financial advice.

## Does the lease decay table hold up?

`propbot backtest` takes every HDB flat that sold twice at least 3 years apart (28,505 pairs from the 2017 to
2026 data). It moves the first price by the town and flat type's median price per sqm, applies the
table's decay for each year held, and compares that with the second price.

| Lease left at first sale | Table decay per year | Flats | Years held | Average error | What the data says per year |
| --- | --- | --- | --- | --- | --- |
| Above 80 years | 0.0% | 10,013 | 4.7 | +2.2% | 0.47% |
| 70 to 80 years | 0.3% | 6,992 | 4.9 | minus 1.0% | 0.09% |
| 60 to 70 years | 0.7% | 8,375 | 4.7 | minus 1.1% | 0.47% |
| 50 to 60 years | 1.2% | 2,832 | 4.3 | minus 1.8% | 0.79% |
| Below 50 years | 2.0% | 293 | 4.2 | minus 5.7% | 0.63% |

A positive error means the model predicted more than the flat fetched, so the table decays too little.

- **Short leases.** The table decays older leases faster than the flats did, by about 0.2 to 0.4 points a year at 50 to 70 years. It is cautious, not wrong.
- **Long leases.** Flats with over 80 years left still lagged their town by about 0.5 points a year, even though the table charges nothing. That is probably age and condition, not the lease.
- **Caution.** The market side is the town median across flats of every age, so older flats lag newer ones for reasons other than the lease. This checks the table. It does not replace it.

The table in `config.yaml` is unchanged. Moving it is your call. The raw output is in [docs/backtest_2026-10-06.txt](docs/backtest_2026-10-06.txt).

## Worth checking yourself

- **Flat type is inferred.** Listings rarely state it, so `hdb_report.match` picks the type whose block median is closest to the asking price. A wrong guess makes the deal %, the verdict and the "Why" wrong; the website says so in a "Worth checking yourself" box.
- **"Rough guide" cards** rest on fewer than 3 sales of that type in the block, or an ask more than 10% outside every sale; they never become a Top pick.
- **Vault notes are NAS-only.** `docs/vault/` is never synced to the public GitHub repo (`PERSONAL_DIRS` in the nas-sync script), so the NAS copy is the current one; the repo copy may lag.

## What is still open

- **Live SORA rates.** The bank rate is assumed in `config.yaml`. Loading MAS SORA would make the stress test start from today's rate.
- **Master Plan zoning and distance to planned stations.** LTA has not published coordinates for most planned stations, so they are matched by name only.
- **Private condo data.** There is no free source of private condo transactions for individuals (URA issues its key only to companies), so condo project prices in the weekly report are Claude's web-search estimates and condo outlooks are Claude's `est`.
- **OneMap token.** The distance line works with `ONEMAP_ACCESS_TOKEN` in `.env`, but a pasted token lasts about 3 days, then the line drops out until you paste a new one. `propbot status` shows the expiry date. Setting `ONEMAP_EMAIL` and `ONEMAP_PASSWORD` instead lets the bot renew its own token.
- **The daily pipeline.** `propbot run`, `discover` and `purge`, plus favourites, from the original plan are not built. The hourly hunt covers discovery for now.
- **Friend accounts live in the bot's database.** They are not in the vault, so they do not show up in Obsidian.

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
