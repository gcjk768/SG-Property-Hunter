# propbot

A small scheduled bot that watches the Singapore property market, checks what the law lets you buy,
works out what you would pay upfront and every month, projects ten years under bear, base and bull
scenarios, and posts one item per Telegram message with a deterministic verdict.

All money maths is done in code from `rules/sg_property_rules.yaml`. Claude only writes the words.

## How it fits together

![propbot architecture: official data, listings, the rules file and your vault notes flow into a code only finance engine; Claude finds listings and writes the words; the renderer posts one item per message to Telegram and saves every action to SQLite and the Obsidian vault](docs/architecture.svg)

Made with draw.io. [Open it in the draw.io editor](https://app.diagrams.net/?pv=0&grid=0#create=%7B%22type%22%3A%22xml%22%2C%22compressed%22%3Atrue%2C%22data%22%3A%227V1rd6I6F%2F41%2FThd3MWPiNoyr9CLtp32y1kRUkQRPIBV%2BuvfHW5VQMe2kZ5eZk1bCCEJm72fnX1JOOHV%2BfosQIuJ7lvYPeEYa33Cd084jpUYBv6QkjgrYfISO3CstGyjYOg847xiVrp0LBxmZWlR5Ptu5Cy2C03f87AZbZWhIPBX29UefdfaKlggG1cKhiZyq6V3jhVNyg9GLpxjx55E5QccI3NmB%2F7Syzo84fi%2B3Ff6anp5jvLGsvrhBFn%2BaqOI753wauD7UXo0X6vYJdTdplt%2Fx9Vi4AH2okNuMLn0jifkLnE%2BZMmFeztjMrwozmgi%2FbskY%2Bo8%2Bl70K0zemMIzi%2FXLFTiyyd9F4C%2FGUJI1A32P80t5WbhA3mGt8x3Td%2F0AToGSQksUpXa1R2hqAkSEd0%2BYDsXwO1h64cYI0g63BzEO3jQsViwPSxJagtypDqsf%2BHNozn98dEwHJSKCIpTwMin2MPweYRfbASL15jgMCV%2FC68MBeUURnp%2FCXyV5YXOoH2ccFJKrYcL%2FFk6fX3XR0sJJsy6p9uh4FqnhOmHkeDY5RMCTHLMKoF1yGk1I9ZUfWOHpTkpxW%2BTgIrwm5ZNo7kIBC4fIdWwPjl38SK484SByQI6UrDjyF1C6mkCfwwUySTMrwIyibVIfr3fyLltIBGAN9uc4CsjDZTcImQzlKCOnp6sNieVyuZxsSGsrK0MZSthFyy%2BCAgeZrOyQG36f3KTkuzm77vUMqGQoQ%2Fh9onInnRYcdH1zlrxhgK8IOR45rpOVEvETVMFWTvjAzHCT334jj47rqilzdgn6JP%2Bg3Af6OxGhlcQQXo8Cf4Y3KrYFhe%2FIcMVC4aTohpxcoijCAXmdZPTSoW89JB169iCp0xWll6IRud5lmUyysudgxex8Y1A8L7CiSIdd%2BBK75KyxxS9iDb%2FIAg2GEWoYpvSGQRksyKEzT%2FRTB4WLVLs9OmvyRjrJBSUvZfISOJ5EEdGNChkP10eLxanlIAIs4amHAYv7ztyG364zJgMNTQTC308Z8TR8sulQWOS2KcwKFQpzUpW%2Bedm7yCv%2BXR6HJvD10t0lbhKaE7b1xuGi9pwhrK8QrmEWASZU2RRqRk5UFrnoh9HmFd33Up3EiPn9AYwiwe8JNmcHC7rI7JV0ps%2B2OKUi2h5RNBXByiYl2wIoVEV7W4iFRIjzKQtLSS7FkmDWALnYrpFLQaDAN9JRxTJR%2BLySnoJyBUnjOmt4g5x6eW5wDyvf1s4n0fhMfL4c%2Fvat8%2BvVhSM%2FWbzFDzzzeTBvxw%2BxHOtdZTXgoX6s%2FU9TOzy6u2ZQl3H06ZWjOoqNzm4XD9yEuRxqsaEmdTh0d8tfzdsClK20rmJDXTuv%2FzB3wzHcP563lw%2FDpP6zdf77CXE3bc0xQG%2FdcBe9XlLX5K%2FjMRe5gz%2FW4uH82id9aDYZt8GY83bwMGRDdCe691x%2FBc8Qj%2Fnb5YO61SY8n7t8uFs8oTuhrXm%2Fn6w7cQZtAC1ID5dnxsKcG%2BHDsDPF5KlGmqOdGSL0tNYdzTanpExxNFXO7zjvTKwz236A2qNRL9a6D5wxXzFaV8trTVdPJv%2FgXdrkHZL%2FVFhVkrZZlWtXEY6rQTiOAqe2KpyqGZc3o%2BEBs7RNIefy8%2BwGtgoOxVSgBAYUKCiVVERLqpu01VCQhgqWKxS8O1dGUHJ%2FcQO%2Fh73ep6Ily3Pb1OTEqsIFo%2FU41MSe7RAT5s3WI9suG1I8y%2FVktsaQcjzkmUTd5p3uNi1fYdVtWpY8L6tKtWfVL4wqYooZxHBTNGLJ%2BRtGVGKTHWxE7VTve7V7r9fnknk8TOosB2%2FxWI%2FptZJrlUm9BIjVZ4sruTODe9scvlWdw3MlieCPMEGQ2NIEoV2dufNCDZuLVCy9doMTdwdMwrA8dUdhiCPgtn4iPWiFQ3gKOAWq9S%2FjaOJ7%2F4wDsO7pTeQlXt4GFrYK05xQgys0ZmT5234bqFS8M2yP7Qg13pkeMLozdlxiE9PHkx2eqrsJigrYcBHxWbnJu2Vifwm%2Fx8v4vTDC7bcSCn9ABSvUVpfr9%2F5mAohyjcRTYbmSLpO5Ksu1a6zHFo25Fcvu47n0DbAHeWmgYWcR4r3vQOgLUk881FLLX1jZUqNBdKG1RXSe4U9rsLUGWnkayMq%2Bx%2Fl8uKSrYJC%2FOECtZeTgsDmR7wyhvkrmDcXRMD94xGQk5ChCa7zbYf29RJ9vNSr6B3hyuW8g%2Bu1mRb%2FOHUpf9FPLAVi4OXkfjG5TkR51h9fpkZ4fqJf99MDxwgi5c%2FKufmQ%2B8eVJjcr8Ad5i%2FuvLvCA3K%2FN1vlb6Mk%2FsBybGKCC0Cfwp2FyO7zWo8JOuiZSPEXBGerQkpCBH2nWGBeMAoxlxaTxh7wcEUntdbBQEqg7VCggIXx8ExFazIFB1wh4DBG5xYDnm3iQUulI%2FNP0kIEjAJ8nvELMpfuIBAnFPs1Lcn1l%2BnPNho8Je58UrCbv49YVdkhoV9iLLbYN8nqWQDD04G7u%2BOYPnhKK%2B4%2Bak3KBqhYNlVlb78kmdOxuvnegPHDOnbHZ2n7UCpArijUvklFxjChJjq5IOWCIwPIK%2FDEycc1P2XBEKbBzlhVz9qwiwiyLnabuH95GV%2FaJk5erIKjRGVu6LklWoI6vUGFn5L0pWqY6sckNkfckqpTibylKIKpObi3IK62tmVXTnWaT%2FU9t%2FOg3tdIJ1c61kXhaSZalWez3vdja8L5kX%2BMLDOlq8dyqWqOm3TMV6XE%2FuM9Wp2CFRWFZ%2BKcqisELNlI16FHY7CJtMcMvTt7rsSZalMpM4IH1yb95c6dV02qzKqrtmaEcgV97CZlJbzVxXpkGrOu9WiVbfLqdNvRj1u%2FqN3lRO23aGGoy2J1gOw2uO8nRJeuquV%2Fd%2Frn3t7KqtzZi1PhTW%2Bmhm644AlLmd6I4SJ0%2BoKmu4d2k838N5JzRiQTBU5XkwtRljBuVDgX2pp60G0xvb4OxIH1kTOIbrSkqpIbPUp1o0mM6Yiz%2F%2B7lw65m8jvYCeLs7vIwMoNngWgCaKbJ71GaR2ZkArQ4%2BVWJ%2FbUK%2F%2BuuFs33%2Bptp%2BtuQmjoSN48rbFUxgyDaQuvCy2aEAnD9LOSIvZio5Us3l49croJl0Ffem7MB7i%2BcCROcFpbSbwx34UnkbrqFZHh26yfsdGiyweaiEnWccyXsK0LHq3z%2BQbK2qebzWqqA9IqH6Nomb6LUnqNaeoeZFvTlHXeaC%2FuaLWV9dTkzNmN58o%2Bfz3RB9dEVW7NtRCHWfPDT%2F11yK4trNVDe767W%2FUOEo6e0lV8nUx2COpyswT3oCevM7W2gCuvDJxmK5e7MGrIrR3sZ0Y0o%2BOvUwiBisHiE2We4bVjjfWjma%2Bh0Q7JuuGkrDCCuOZ%2B%2B6cwW%2BsHgWpZgnHEdVjXfjrPXasIPJMuzn1KMhVs%2F9o6vG4mdefUj0aqtG9eda7s4%2BxY0HZGZw%2BsncorvGoJ2pd2wYbULgfgSX6Yn1yYL1Kt1Nzp8rTid26vh%2F1eLBA%2BRdFCbYsWIxGnPyssnIBlK6vD2stzAh6heu6fXHWA4V7k7XTi8DyXGH3fpdlC31qa9QFWz29Lidv9Vk4lroV2jXLY4%2BkbheBn%2Bq%2FBhTuPSiqpJelS3LdPT96bbotZYM0ffbTuVXYyHcITFNirSeF5U4DjJKqL5n6YYQCcuY%2FnqSbTATLd%2BfsfGOdKzFVZ%2BgRdW7uAaKlc1sqr%2FS6zelcia1a8MfSuXzdQgh6Ovedq50uxqED1amvdyohs8Q1t9zJckLTT02DBrC52CZmo9uPQ%2BY7nDwbBnGbnNTtWVPuNEVveAYPLKDMU0ioBqjnhicb%2B%2F%2F8QPNbd1%2Fg6%2FzldSuyWSqL%2FfKkMFrY3G23WmLriNhcoZdQxYoacG5zNIhVt1TlPwPOKbbQhma%2BVXJSiTVx52MtRV0GKM0dbxKa63bo%2BlDH1XrhIsd7GdFTmlt7Wu1OixIw9jw%2FOZggL9nPjMCyt5yPcfDuRfLfCZrbua8lD2XWrHvnxBqwoQTNlFMujg7NZXrxTIPQfNyci%2F8kNLNMnrCZE5xtDpthkBbZyasBZL5Outq5bVgjIHyR7BZJtoQ8yfeHLPaLrImnL1PiMEBolSFJ9QSMA2RGOPiJpb8DUoTWoRBMx3NBO5jOS22lmlR7PAgW5IMgmI7r4ieaXhNN15%2FvxatRY1u5bUexY%2BLKvyfRbKcIBJB4uWY4gqBPJ5tpbqvBqLc0uj1mML0RjPkqSlPhtCwl7ndnMDXX5Gcj3Q2o0FvqU5u7nZZT5rSIJKEZcxKy0PIQABlHjJ0diWmxstbv7LW%2BK3GNhCDubG53%2BMDy86Q32kltFVUrtJtTteG%2FaUZXA6p2eDVIk8eItKXLZz8%2BYg%2BqyVy6mSWYh%2BsTU6jcK14jsuYwWeeXE4vYTcuQBOt%2F1O7hy9a2nSxyzV69UFijdhkqlg%2FlID3D8J2jWj4VetXs11WndqlYPv%2Bd%2FdFy0AgzswfQ5BcvUdwWTdr2RclccyCchVMbwOA8yHJSxHA%2FDoJVlDi%2FNjOCHRC79UkeyUVetWPAYOcp2daNcX2SIJ2EbXGG5qi8HcQPGr9yUl%2BLxrVGEBU0Fj5b%2BLZCrwbRWPiW8dvK9LhJZI6KT3g0AM4b3wsh7nWPfHnn4%2BBZGftLMt9NvqWQeKeSKCwpKvbDr%2FFOxTiMcJAGatNN9qGPNZfMkE7SDfTJXwueNfqZOL9yS%2BjSusPamMHRwrn5nh60sJqTFLEnHBGrKwRrMmggfHg8lxT%2BMpE5wQSeOwSVw38ukTn7xfEk66ifg9svlpMpzqRZvrz9YIOhA2TNnWZiukraE4HqD51GKy68rJQVGNP3XcAcL5tWPyLXJR8tC2uROnNkpJHcn8jBe4ClPnRwtKRHgXb0Vk3S4hpE4iZjB8LPkvnqUoPe1bPG6dP7D1oyP5hq3IWqxPrdpg8fniAWGIM8xQ4%2FPVyPjSlZQXez1OEJrkY9Wx8WSwfIAncedXvc%2FXb50ni%2BivShwBnPRlcfzUgsAtrQlxejG44sioefeDC9Ei%2BGClkEL1yN7m3DKe6HOpMpaT%2Btw6zh7xraZwfTGWs4DNxrPsN9%2FGCqx4ZzpDhBWa82GSdYBM4TaihQkC0yKLr8cBVLLF8PuXG%2B4ycRcxxlOtYPVihI3FXVzrPPhSV7Bs4wXqS1QC8%2FwSMmwZAfpftmHVI4xhtSupTj9dlnaRpUupLYoNL9CdhXA%2FaM%2FqwLV8x%2F4NtrguZ0YvjL7gh4x3qqnIVkxV%2B%2BqB2Uon6sIHhZuUlSg7nAQjUouLE6O13NHVb49zU73W0jQwWPS1tWFfvenebIWlf3b18rc9EYu53io9V1iiF9pr9vj1d9bwdvepfdeuk7ZEP8IvW79N2nYl1U3kS6JD676%2BVVVhoqf0Cq0lC6hV%2BloYQniuc5kE0OCIdSZIrS9ih7maKo%2B5WYgi974g5lispCjnJDFJlCPCCERZEpSpnWe5miqPuZmaL40Nt7maKMFEdlimqobntvkONqk%2B2NI%2FZrk7zuZ%2BaRMnCI5VnBW3mk0hBNHuEqPBLnlu%2FGiv7jsEgpUL6XRYq6X4lFCmPovSxSaYgmixwQRKLIFKWt7fcyRVH3MzNFuzxPyGXytUxRWZ5UbogmU1S9%2F9k6wp9ZBzXOYNlyUjb7RpVSban85VqavHFArIMiW5RC%2BHvZoqj7qdmCK2XjimXf4sFsUUlOOBAy4N2heKPaglQI9w1Zrh%2FyzpGVb8hH9sKX6RjezqUHuFJpgtd2eHM%2FeOV1vxaXCrS4tNISTfCq%2BoxNfz5H3nF1W8kRv3%2FWk9f91OxRCQzkgcTXsodcQorWgc6312NYu37EuzGsdEOrJb3uSbbrU8C8A9ad0PQdbi9L2e87zOt%2BZqZuMX9hxUN5utxQkRF%2FDMSrOpSzLfWyJRDJGocfLwBFzVh6u6L8Vs1YbumYfJLzckPgIQktQe4cxhZFXQpsYaFwUozqw3hEzKOB2Ytl862zX8si5Yb48nf33swhcBr4JEXlpXqAFhPdtzCp8X8%3D%22%2C%22effect%22%3A%22pop%22%7D), or open
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
