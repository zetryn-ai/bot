# M8 — Deployment (Docker, VPS)

**Date:** 2026-07-10
**Status:** Approved
**Target version:** v0.8.0

M1–M7 built a bot that scans, decides, executes (paper/live), persists, and
notifies — but it only runs while a terminal is open on the dev machine. M8
packages the runtime into a Docker image and deploys it to the user's VPS so
it runs 24/7, restarts itself after crashes and reboots, and updates with one
command.

## 1. Goals

1. `python -m zetryn_bot` runs as a Docker container on the VPS, supervised
   by the Docker daemon (`restart: unless-stopped`) — survives crashes and
   VPS reboots with no systemd unit needed.
2. Paper positions persist to the VPS Postgres and survive container
   restarts (M6 guarantees, now exercised in production).
3. Telegram notifications (M7) become the primary monitoring channel for the
   deployed bot.
4. Updating = `./scripts/deploy.sh` on the VPS: pull, build, migrate,
   restart, show status.
5. Secrets (`.env`, later `wallet.enc`) never enter the image or the repo.

## 2. Decisions (locked via brainstorming)

| # | Topic | Decision |
|---|---|---|
| 1 | Mechanism | **Docker Compose on the VPS** (carried from M6's locked decision). No systemd unit — `restart: unless-stopped` + the Docker daemon covers crash-restart and boot, matching every other project already on this VPS. |
| 2 | Image build/update | **Clone the repo on the VPS, build the image there.** Deploy = `git pull && docker compose build && up -d`. No registry, no CI publish; needs a read-only deploy key on the VPS. |
| 3 | Database | **Reuse the existing `postgres-16` container** (the odoo-lema project's Postgres, network `odoo-lema_odoo-net`, `restart: always`). One-time: create role `zetryn` + database `zetryn_bot` on it. The bot container joins `odoo-lema_odoo-net` as an external network and connects via Docker DNS (`postgres-16:5432`) — not through the host-published port 8519. |
| 4 | Deploy automation | **`scripts/deploy.sh` run on the VPS** + documented one-time setup. No GitHub-Actions auto-deploy (keeps VPS SSH keys out of GitHub Secrets). |
| 5 | Execution mode on VPS | **`EXECUTION_MODE=paper`** until M10 (live on-chain testing explicitly deferred by the user). `NOTIFY_ENABLED=true`. |

Accepted trade-off (surfaced during brainstorming): the bot's persistence now
depends on another project's Postgres container. If odoo-lema is ever torn
down (compose down, network removed), the bot loses its DB → M6's fallback
kicks in (in-memory state, ERROR log → Telegram via the M7 log bridge), so
trading degrades gracefully but persistence stops until repaired.

## 3. Architecture

```
VPS (Ubuntu 24.04, Docker 29 + Compose v5)
│
├── /opt/zetryn-bot                ← git clone (read-only deploy key)
│   ├── .env                       ← chmod 600, never in git/image
│   ├── docker-compose.vps.yml     ← bot service only
│   ├── scripts/deploy.sh          ← pull → build → migrate → up -d → status
│   └── (repo contents)
│
├── network: odoo-lema_odoo-net (external)
│   ├── postgres-16                ← existing container (odoo-lema's)
│   │     └── role zetryn / db zetryn_bot   (one-time CREATE)
│   └── zetryn-bot                 ← new container
│         DATABASE_URL=postgresql+asyncpg://zetryn:***@postgres-16:5432/zetryn_bot
│
└── volumes (bind mounts under /opt/zetryn-bot):
    logs/               ← loguru file sink
    telegram_session    ← telethon session (created once via telegram_login.py)
```

### 3.1 New files

| Path | Responsibility |
|---|---|
| `Dockerfile` | `python:3.12-slim`; install the package; pre-download the NLTK `vader_lexicon` at build time (no first-boot download); non-root `bot` user; `CMD ["python", "-m", "zetryn_bot"]` |
| `.dockerignore` | exclude `.env`, `wallet.enc`, `logs/`, `*.session`, `twitter_cookies*`, `.git`, caches |
| `docker-compose.vps.yml` | `bot` service: build from repo, `restart: unless-stopped`, `env_file: .env`, joins external `odoo-lema_odoo-net`, bind-mounts `logs/` + telegram session |
| `scripts/deploy.sh` | VPS-side update: `git pull` → `docker compose -f docker-compose.vps.yml build` → `alembic upgrade head` (one-off container run) → `up -d` → `docker compose ps` + recent logs |
| `docs/plans/…` (this doc) + README deployment section | one-time setup + update instructions |

### 3.2 Unchanged

- `docker-compose.yml` (local-dev Postgres) stays as-is for laptop use.
- No application code changes — M8 is packaging only. `Settings` already
  reads everything from `.env`/environment.

## 4. One-time VPS setup (documented, executed in B2)

1. Deploy key: `ssh-keygen` on the VPS, add as read-only deploy key on
   `zetryn-ai/bot`, clone to `/opt/zetryn-bot`.
2. Database: `CREATE ROLE zetryn LOGIN PASSWORD '…'; CREATE DATABASE
   zetryn_bot OWNER zetryn;` on `postgres-16` (as the `odoo` superuser).
3. `.env`: copy from `.env.example`, fill keys (scanner APIs, LLM, Telegram
   notifier), set `DATABASE_URL` to the `postgres-16` DSN,
   `EXECUTION_MODE=paper`, `chmod 600 .env`.
4. Telegram scanner session: run `scripts/telegram_login.py` once (interactive)
   and place the session file where the compose bind-mount expects it, or
   leave the Telegram scanner disabled initially.
5. First deploy: `./scripts/deploy.sh`.

## 5. Testing plan

- **B1 (local):** `docker build` succeeds; `docker run --rm zetryn-bot
  python scripts/m1_smoke.py` (and m7) pass inside the image; container
  starts with an empty env and runs rule-only (no crash, missing keys just
  disable sources — the M3 contract).
- **B2 (VPS):** after first deploy — container healthy for a sustained
  window; a paper position round-trip persists across `docker compose
  restart bot` (M6 restore log line); Telegram heartbeat/notifications
  arrive from the VPS.
- **B3 (CI):** new workflow job builds the Docker image on every push
  (catches Dockerfile rot); no registry push.

## 6. Definition of Done

- [ ] Bot container runs on the VPS with `restart: unless-stopped`, comes
      back by itself after `docker kill` and after a daemon restart.
- [ ] Open paper positions survive `docker compose restart bot`
      (`restored N open position(s) from DB` in logs).
- [ ] Telegram heartbeat + trade notifications arrive from the VPS.
- [ ] `./scripts/deploy.sh` performs a clean update from a new commit.
- [ ] CI builds the image; README documents setup + update.

## 7. Sub-phases

- **B1** — `Dockerfile`, `.dockerignore`, `docker-compose.vps.yml`; local
  image build + smoke-in-container verified.
- **B2** — `scripts/deploy.sh`; one-time VPS setup (deploy key, role/db on
  `postgres-16`, `.env`); first deploy; live verification (restart
  survival, Telegram from VPS).
- **B3** — CI image-build job, README deployment section, version 0.8.0,
  CHANGELOG, ROADMAP/plan status, tag + GitHub release.
