# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.0.0] - 2026-10-31

First versioned release: everything the agent runs today, after a security review of every path
that spends money or handles secrets. The Docker image runs Python 3.12.

### Security
- **Money actions are single-use, expire and are re-checked.** A button carries only the id of a
  stored pending action, which is claimed atomically before it runs (a double click or a replayed
  update can't run it twice), expires after 10 minutes and re-reads price, listing, clause, owner
  and balance from Biwenger first: if anything changed, it does nothing and says what.
- **Bids need an explicit confirmation**, and the bid selector refuses when the balance can't be
  read or doesn't reach the market minimum once the safety margin is taken off.
- **Private chat only.** Messages and clicks are handled only from your private chat with the bot
  (`TELEGRAM_CHAT_ID`). The backlog is dropped on startup and the offset is saved before acting, so
  a crash can't replay an action. `/token` deletes your message once the token is stored.
- **No double writes to Biwenger.** Reads are retried on network errors, timeouts and 5xx/429;
  writes only when the connection failed before anything was sent. A wrong password is not retried.
- **Spending caps.** The sniper and auto clause-raising check the balance minus
  `BUDGET_SAFETY_MARGIN` right before spending; auto clause-raising tries at most
  `AUTO_CLAUSE_MAX_PER_DAY` times a day (default 1).
- **Secrets out of the logs.** A logging filter masks the bot token, the Biwenger password and
  token and anything shaped like a secret; Telegram failures are logged as type and HTTP status
  only. Docker log rotation (10 MB × 3).
- **Hardened container.** Non-root user (uid/gid 10001), read-only root filesystem with a tmpfs on
  `/tmp`, `no-new-privileges`, no capabilities, memory and process limits. The image installs
  `requirements.lock` and leaves out `.env*`, the database, `.venv` and the tests.
- **The SQLite file is owner-only** (`0600`, `umask 077`): it stores the Biwenger session token.
- The tests always run on an in-memory database, with blank credentials and `DRY_RUN=true`.

### Added
- Morning report at 9:00: league position and gaps, gameweek start, budget and squad value,
  injuries, offers, urgent sells and the suggested captain.
- A six-button menu that edits the same message, and HTML messages: lineup with rival difficulty
  and kick-off time, squad with prices and trends, league table, fixtures.
- Commands `/parte`, `/liga` and `/jornada`.
- One alert when a scheduled job breaks and one when it recovers; an expired token is a single
  alert for every job.
- Live alerts for goals, assists, red cards, own goals, missed penalties and injuries.
- League rules: the clause check can be turned off and only flags big gaps (`CLAUSE_MIN_GAP`,
  `CLAUSE_MAX_PER_MANAGER`); the daily digest shows the 15 worst infractions.
- CI on GitHub Actions: the tests on Python 3.12 with `requirements.lock`, and the image built with
  the tests run inside it.

### Fixed
- Follows Biwenger's app version: on "Old version" it reads the new `X-Version`, retries once and
  stores it.
- The current gameweek comes from the competition's active events, so live tracking, the quiniela
  and the saved predictions no longer look at a finished or postponed round, and the predictor gets
  match difficulty back.
- Live tracking reads the events from the round, where Biwenger moved them, and maps event types
  correctly (5 is a substitution, not a red card).
- Telegram messages over 4000 characters are split.

### Changed
- `.env.example` starts with `DRY_RUN=true`, as the README promises.

### Upgrading from an earlier install
- Chown the `biwenger_data` volume to `10001:10001` once before starting the new image (exact
  command in [DEPLOY_VPS.md](DEPLOY_VPS.md)).
- Buttons of old messages, with the amount inside, do nothing; auto-bid targets without the close
  time of their listing are dropped with a notice.
- If the logs were ever shared, rotate the bot token ([DEPLOY_VPS.md](DEPLOY_VPS.md)).

## [0.1.0] - 2026-08-14

First public release (MIT, README in English and Spanish).

- Biwenger client, optimal-lineup solver (PuLP) with a smart captain, ideal and maximum bids with
  sniping at market close, price trends and buy/sell suggestions.
- Telegram bot: one daily message with buttons, and commands for the lineup, market, transfers, sell
  timing, fixture difficulty, differentials, quiniela, gameweek summary, auto-bids and `/token`.
- Two automations, both off by default: auto-lineup and auto clause-raising.
- Rival spy: one daily digest of your rivals' moves.
- A points predictor that calibrates itself.
- Daily summary and a detector of league-rule infractions (added on 2026-08-15).

[1.0.0]: https://github.com/miguelmartt/biwenger-agent/releases/tag/v1.0.0
