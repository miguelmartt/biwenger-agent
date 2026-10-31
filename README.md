# Biwenger Agent

![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)
![Python](https://img.shields.io/badge/Python-3.11+-3776AB?logo=python&logoColor=white)
![Docker](https://img.shields.io/badge/Docker-ready-2496ED?logo=docker&logoColor=white)
![Tests](https://img.shields.io/badge/tests-166%20passing-brightgreen)
![Telegram](https://img.shields.io/badge/Telegram-bot-26A5E4?logo=telegram&logoColor=white)

**🌐 English** · [Español](./README.es.md)

A personal AI-assisted agent to optimize your [Biwenger](https://biwenger.as.com)
fantasy football league: optimal weekly lineup with a smart captain, last-minute
bidding and "sniping", squad economy/market management, a fixture-calendar
planner, differentials and a rival spy — all driven from a Telegram bot with
button confirmation. It ships with a points predictor that **self-calibrates**,
learning from its own hits.

> **Philosophy:** the agent *recommends*, you *decide*. No real action (buy, bid,
> pay a clause, sell) runs without your explicit confirmation. The few optional
> automations are **off by default**. It starts in safe mode (`DRY_RUN=true`),
> which only simulates write actions.

Every confirmation button is **single-use** and **expires after 10 minutes**, and before
acting the agent re-reads the price, clause, owner and balance from Biwenger: if anything
changed since you asked, it does nothing and tells you what changed. Bids go through the
same two-step confirmation (picking a bid level asks before bidding).

See [`ARCHITECTURE.md`](./ARCHITECTURE.md) for the full design and
[`DEPLOY_VPS.md`](./DEPLOY_VPS.md) to run it 24/7 with Docker.

## About the Biwenger API

Biwenger has **no** public API, so `core/client.py` is built by reverse-engineering
the web app's requests (headers, payloads), also drawing on community projects
like [pablopb3/biwenger-api](https://github.com/pablopb3/biwenger-api) and
[biwenger-java-api](https://github.com/jbujalance/biwenger-java-api). Since the
unofficial API can change without notice, if something breaks check the endpoints
with your browser devtools (Network tab). All HTTP access is isolated in a single
file (`core/client.py`) precisely so that's the only place you need to touch.

## Quickstart

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.lock   # the exact versions the Docker image uses
cp .env.example .env   # fill in your credentials and league IDs
python main.py --once  # run one manual cycle (no automated bidding yet)
```

## Modules

- `core/` — Biwenger HTTP client (login, squad, market, bids) and data models.
- `lineup/` — per-player points prediction + optimal-lineup solver (PuLP).
- `bidding/` — ideal/maximum bid calculation + "sniping" engine at market close.
- `economy/` — trend analysis, buy/sell suggestions, budget simulation.
- `bot/` — Telegram notifications and interaction.
- `scheduler/` — orchestration of all periodic jobs (APScheduler).
- `data/` — database models (SQLAlchemy) to store price/points history.

## Bot commands (Telegram)

Every morning at 9:00 you get **one report** with what matters already inside: your
position in the league and the gap to the leader, when the gameweek starts, your
budget and squad value, injuries, offers, players worth selling now and the suggested
captain. It comes with just two buttons (lineup and menu). The menu has six buttons
(report, lineup, market, league, gameweek and "more"); navigating edits the same message
instead of piling up new ones.

- `/parte` — the morning report, now.
- `/liga` — the league table, with the gaps above and below you.
- `/jornada` — the gameweek's matches with kick-off times and which of your players play in each.
- `/menu` — everything with buttons.
- `/alineacion` — the optimal XI of the gameweek, with a **smart captain**: combines expected points (already including match difficulty) with a small bonus if the player is a differential (few others own him → captaining him pays off more for climbing the table).
- `/mercado` — bargains, price trends and clauses (with buttons to buy/sell).
- `/optimizar` — the best move: sell X and buy Z to gain points, **looking at upcoming gameweeks** (rewards players with an easy run ahead).
- `/vender` — sell timing: who to offload NOW because their price has peaked or is dropping, and/or a tough fixture run is coming (with a sell button).
- `/calendario` — difficulty of your team's next 5 gameweeks: separates easy runs (who to captain/hold) from tough runs (who to rotate or sell before the price drops).
- `/diferenciales` — your strong players that almost nobody else owns (your edge) plus "free gems": strong players that NOBODY owns and are on the free market.
- `/quiniela` — 1X2 forecast of the gameweek (saved to evaluate later).
- `/resumen` — how your team did last gameweek + model learning.
- `/objetivos` — your scheduled auto-bids (sniping).
- `/auto` — enable/disable the automations (see below).
- `/aprendizaje` — status of the predictor's self-calibration.
- `/token <value>` — hot-refresh the Biwenger token, without touching the server. The bot deletes your message with the token from the chat once it's stored.
- `/equipo`, `/help`.

### Bounded automations (`/auto`)

Two switches, **both off by default**. The agent never acts unless you turn them on:

- **Auto-lineup** (zero risk): sets your optimal XI once per gameweek, ~4h before
  the first match. Reversible: you can change it before the deadline.
- **Auto clause-raising**: shields your vulnerable stars (low clause) by raising it.
  It spends budget and uses a write endpoint that isn't fully confirmed yet — watch it
  the first time you enable it. Limits: at most `AUTO_CLAUSE_MAX_PER_DAY` attempts a day
  (1 by default), only if the whole raise fits in your balance minus
  `BUDGET_SAFETY_MARGIN`, and one attempt per player: if it fails, it tells you once and
  doesn't retry every 30 minutes.

Pre-authorized auto-bids (sniping) are tied to the market listing you marked them on: the
agent only bids on that listing, right before it closes, and only if the cap fits in your
balance minus the safety margin. If that market closes, the target expires.

### Rival spy (proactive)

Once a day (15:00) the agent sends a **single digest** with all of your rivals'
transfers/sales for that day, grouped in one message (deduplicated in the DB, no
repeats) — instead of dripping one message per move. The first time it just learns
the state, without dumping a giant alert.

Players worth selling before their price falls go in the morning report (urgent
drops only); the full analysis is in `/vender`.

**Optional league-rule checks.** If you create `config/league_rules.py` (see
`config/league_rules.example.py`; it's gitignored, so your rules stay private),
the agent flags rivals that break your league's agreed rules — clauses below the
required minimum, a captain over a value cap, too many players from the same club
— inside that same daily digest. You can also pull the digest on demand with
`/resumendiario`.

## Staying up on its own

The unofficial API changes without notice, so the agent is built to survive it:

- **App version.** When Biwenger ships a new app it rejects the old `X-Version` with
  "Old version". The agent reads the new number from biwenger.as.com, keeps using it
  (it's stored in the database) and tells you on Telegram. `BIWENGER_APP_VERSION` in
  `.env` is only the starting value.
- **Gameweeks.** The current gameweek comes from the competition's `activeEvents`, not
  from `rounds/league` (which points at the finished or a postponed round between
  gameweeks).
- **Failure alerts.** If a scheduled job stops working you get one message when it
  breaks and one when it recovers, never one per run. An expired token is a single
  alert for every job.
- **Pinned dependencies.** The image installs `requirements.lock` (exact versions tested
  together), so a library update can't break it behind your back. `.dockerignore` keeps
  `.env`, the database and the tests out of the image.

The tests always use an in-memory SQLite database (`tests/conftest.py` forces it before
importing anything, and blanks the credentials), so they can't touch your real one. The
image doesn't ship the tests; to run them in a throwaway container, mount them:

```bash
docker build -t biwenger-agent:test .
docker run --rm --user root -v "$PWD/tests:/app/tests:ro" biwenger-agent:test \
  sh -c "pip install -q pytest==8.3.5 && python -m pytest -q -p no:cacheprovider"
```

## Security

- **Only your private chat.** The bot handles messages and button clicks only when they
  come from a private chat whose id, and the sender's id, are `TELEGRAM_CHAT_ID`. It
  ignores groups, other users and edited messages.
- **Nothing replays after a restart.** On startup it drops whatever Telegram queued while
  it was down, stores the update offset before acting on each update, and ignores action
  buttons from messages sent before the process started (navigation buttons still work).
- **No double writes.** The Biwenger client retries reads, but never retries a write (bid,
  clause, sale) that may have reached Biwenger; and it never retries a login rejected with
  a 4xx (wrong password), to avoid locking the account.
- **Secrets out of the logs.** A logging filter masks the Telegram bot token and the
  Biwenger token and password (including the `/bot<TOKEN>/` URL that `requests` puts in
  its exception messages). Docker logs rotate (3 files of 10 MB).
- **If your logs were ever shared, rotate the bot token** (@BotFather → `/revoke`) and
  recreate the container; see [`DEPLOY_VPS.md`](./DEPLOY_VPS.md). Do the same with the
  Biwenger password or token if they show up in old logs.
- **Hardened container.** Non-root user, read-only filesystem (writes only to `/data` and
  an in-memory `/tmp`), no capabilities, `no-new-privileges`, memory and process limits.
- The SQLite database stores the token renewed with `/token` in plain text: the file is
  owner-only (0600) and any backup of it is a secret.

## Important notice

Automating your account may conflict with Biwenger's terms of use. The real risk
is an account ban, not a legal one, but:

- Start in **dry-run** mode (notify only, no actions) until you trust the logic.
- Add random jitter to automated bid timings so it doesn't look like perfect bot traffic.
- Don't poll the market aggressively; once every few minutes is more than enough except in the final minutes before close.

This is a personal, educational tool, unaffiliated with Biwenger or AS.
"Biwenger" is a trademark of its respective owners.

## License

[MIT](./LICENSE) © 2026 VerticeDev. Use it, modify it and share it freely;
attribution appreciated. No warranty (see the license).
