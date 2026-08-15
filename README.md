# Biwenger Agent

![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)
![Python](https://img.shields.io/badge/Python-3.11+-3776AB?logo=python&logoColor=white)
![Docker](https://img.shields.io/badge/Docker-ready-2496ED?logo=docker&logoColor=white)
![Tests](https://img.shields.io/badge/tests-65%20passing-brightgreen)
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
pip install -r requirements.txt
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

A single daily message with buttons (no spam). From there or by typing the command:

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
- `/token <value>` — hot-refresh the Biwenger token, without touching the server.
- `/equipo`, `/help`.

### Bounded automations (`/auto`)

Two switches, **both off by default**. The agent never acts unless you turn them on:

- **Auto-lineup** (zero risk): sets your optimal XI once per gameweek, ~4h before
  the first match. Reversible: you can change it before the deadline.
- **Auto clause-raising**: shields your vulnerable stars (low clause) by raising it
  once per player. It spends budget and uses a write endpoint that isn't fully
  confirmed yet — watch it the first time you enable it.

### Rival spy (proactive)

Once a day (15:00) the agent sends a **single digest** with all of your rivals'
transfers/sales for that day, grouped in one message (deduplicated in the DB, no
repeats) — instead of dripping one message per move. The first time it just learns
the state, without dumping a giant alert.

It also alerts (deduplicated, urgent drops only) when one of your players is worth
selling on price before it falls — the rest you check yourself with `/vender`.

**Optional league-rule checks.** If you create `config/league_rules.py` (see
`config/league_rules.example.py`; it's gitignored, so your rules stay private),
the agent flags rivals that break your league's agreed rules — clauses below the
required minimum, a captain over a value cap, too many players from the same club
— inside that same daily digest. You can also pull the digest on demand with
`/resumendiario`.

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
