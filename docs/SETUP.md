# Setup guide

This gets propbot running on your own computer or server, posting to your own Telegram chat. Allow
about 30 minutes. You need Python 3.12 or newer, a Telegram account and a Claude subscription or API key.

propbot reads public data (data.gov.sg, URA, LTA, OneMap) and asks Claude to search for listings. It
never logs in anywhere for you and never buys anything. It is research, not financial advice.

## 1. Get the code and install it

```
git clone https://github.com/gcjk768/SG-Property-Hunter.git
cd SG-Property-Hunter
python -m venv .venv
.venv\Scripts\activate            # Windows. On Mac or Linux: source .venv/bin/activate
pip install -e ".[test]"
pytest                            # should end with all tests passing
```

## 2. Make a Telegram bot

1. In Telegram, open **@BotFather** and send `/newbot`. Pick a name and a username ending in `bot`.
2. BotFather replies with a token that looks like `123456:ABC...`. Keep it secret.
3. Open a chat with your new bot and press **Start**, so the bot may message you.
4. Find your own numeric user id by messaging **@userinfobot**. It replies with `Id: 123456789`.

## 3. Get a Claude token

The hourly listing hunt and `/propask` call the `claude` command line tool. Skip this step and propbot
still runs the HDB and condo deal alerts and `/propanalyse`, but not the hunt or `/propask`.

1. Install the Claude Code CLI: `npm install -g @anthropic-ai/claude-code`.
2. Run `claude setup-token` and follow the prompts. It prints a long token.
3. Alternatively, use an Anthropic API key and set `claude.auth: apikey` in `config.yaml`.

## 4. Optional free keys

| Key | What it adds | Where to get it |
| --- | --- | --- |
| `URA_ACCESS_KEY` | Private condo deals (`/propcondo`) and condo price trends | URA issues it only to registered companies at https://eservice.ura.gov.sg/maps/api/, so most individuals cannot get one. Skip it |
| `ONEMAP_EMAIL` and `ONEMAP_PASSWORD`, or `ONEMAP_ACCESS_TOKEN` | Distance to the nearest MRT station on every card | Register at https://www.onemap.gov.sg/apidocs/. With the email and password the bot renews its own token. A pasted `ONEMAP_ACCESS_TOKEN` works for about 3 days, then the distance line drops out until you paste a new one. `propbot status` shows the expiry |

Without these keys the bot works and simply leaves those lines out. Without the URA key, condo cards show the model's `est` outlook instead of a `data` one, and there is no `/propcondo`. HDB deals, the MRT station list
and planned stations need no key.

## 5. Fill in `.env`

Copy `.env.example` to `.env` in the project folder and fill it in. Never commit this file.

```
TELEGRAM_BOT_TOKEN=123456:ABC...
CLAUDE_CODE_OAUTH_TOKEN=...        # or ANTHROPIC_API_KEY=...
URA_ACCESS_KEY=                    # optional
ONEMAP_EMAIL=                      # optional, with the password the bot renews its own token
ONEMAP_PASSWORD=                   # optional
ONEMAP_ACCESS_TOKEN=               # optional alternative: a pasted token, lasts about 3 days
```

## 6. Edit `config.yaml`

Open `config.yaml` and change these parts. Everything else has sensible defaults.

**Where to post.** For a private chat with the bot, use your own user id as the chat id.

```yaml
telegram:
  chat_id: "123456789"        # your numeric user id from step 2
  thread_id: <THREAD_ID>                # 0 unless you post into a forum topic
  owner_user_id: 123456789    # the same id
  allowed_user_ids: []        # friends who may also message the bot privately, see step 9
```

**Who you are.** The affordability maths needs these. The bot refuses to run `/propanalyse` while
income is 0. Fixed income is before CPF, per month.

```yaml
profile:
  citizenship: SC             # SC, PR or foreigner
  age: 30
  first_timer: true           # never owned a home or took a housing subsidy
  marital_status: single      # single, engaged, married
  buying_with: none           # none, fiance, spouse, parents, sibling, joint_singles
  gross_monthly_income: 6500
  monthly_debt_repayments: 0  # car loan, study loan, card instalments
  cpf_oa_balance: 40000
  cash_available: 80000
  properties_owned: 0
  hold_years: 10
```

**What to search for.** `search.budget_min_sgd`, `search.budget_max_sgd`, `search.areas` (empty means all of
Singapore) and `search.min_remaining_lease_years` decide which deals and listings appear.

If you use Obsidian, you can instead keep these fields in `Profile.md` in your vault, see the README.
Turn the vault off with `obsidian.enabled: false` if you do not use it. That is the simplest start.

## 7. Check everything works

```
propbot status                    # shows which keys are set and how much data is stored
propbot test-telegram             # sends and deletes a test message in your chat
propbot backfill                  # one time, about 10 minutes: downloads HDB sales since 2017
propbot pulse                     # prints the HDB deals it would post, nothing is sent
propbot analyse "hdb_resale, Tampines, 600000, 1001, 99 year, 68, 3200"
```

`backfill` gives the price trends behind each card's 🔮 outlook, and it is the data the backtest uses. Run it once.
It stays inside data.gov.sg's daily limit. The bot keeps itself current after that.

## 8. Run it

```
propbot serve
```

This listens for commands and posts an HDB pulse at minute 7 of every hour, a condo pulse about 5 minutes
later if you set the URA key, and a listing hunt at minute 37 if Claude is set up. It only posts a pulse
when something new appears. Leave it running, or use Docker as below.

In your chat with the bot, send `/prophelp` for the command list.

### Docker (a home server or NAS)

`docker-compose.yml` and `deploy.sh` run it as a container with the project folder mounted. Copy the project
to the server, put `.env` next to it, then:

```
docker compose up -d --build
docker logs -f sg-property-hunter
```

Edit the vault volume line in `docker-compose.yml` to a folder on your server, or remove it and set
`obsidian.enabled: false`. `deploy.sh` is the owner's own script with his server address in it, so change
`NAS` and the paths before using it.

## 9. Let friends use your bot

A friend does not need their own copy. Add their Telegram user id (they get it from @userinfobot) to
`telegram.allowed_user_ids` and restart. Then each friend:

1. Opens a chat with your bot and presses **Start**.
2. Sends their own figures once:
   `/propprofile set age=31 gross_monthly_income=6500 cash_available=80000 cpf_oa_balance=40000 citizenship=SC`
3. Sends `/propanalyse hdb_resale, Tampines, 600000, 1001, 99 year, 68, 3200`.

Their figures are stored separately and used only for their own `/propanalyse`. They never see yours.
Friends can use `/proppulse`, `/propcondo`, `/propanalyse`, `/propprofile`, `/propstatus` and `/prophelp`.
`/prophunt` and `/propask` stay owner only, because they use your Claude plan.

## Checking the lease decay assumptions

After `backfill`, run `propbot backtest`. It compares what the model predicts with what the same flat sold
for the second time, by lease band. The current result is in the README.

## When something goes wrong

| What you see | Likely cause |
| --- | --- |
| `TELEGRAM_BOT_TOKEN is not set` | `.env` is missing or not in the folder you run the command from |
| No messages arrive | You did not press Start on the bot, or `chat_id` is wrong |
| `profile.gross_monthly_income is 0` | Fill in the profile, step 6 |
| No 🚇 line on cards | OneMap login not set or the pasted token expired (`propbot status` shows which). Planned stations still show by name |
| `/propcondo` says URA key missing | Expected for individuals. URA gives the key only to companies |
| Hunt and `/propask` do nothing | Claude token not set, step 3 |
| Emoji crash on a Windows console | Update to the latest code. The command line now forces UTF-8 |
