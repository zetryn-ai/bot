#!/usr/bin/env bash
# deploy.sh — update the bot on the VPS (run ON the VPS, from the repo root).
#
#   cd /opt/zetryn-bot && ./scripts/deploy.sh
#
# Steps: git pull → build image → run DB migrations → restart container →
# show status + recent logs. One-time setup (deploy key, role/db on
# postgres-16, .env) is documented in docs/plans/2026-07-10-m8-deployment.md.

set -euo pipefail

COMPOSE="docker compose -f docker-compose.vps.yml"

cd "$(dirname "$0")/.."

if [[ ! -f .env ]]; then
    echo "ERROR: .env not found — copy .env.example, fill it in, chmod 600." >&2
    exit 1
fi

echo "==> Pulling latest main ..."
git pull --ff-only

echo "==> Building image ..."
$COMPOSE build

# Bind-mounted dirs must be writable by the container's non-root user (uid 1000).
mkdir -p logs data
chown -R 1000:1000 logs data 2>/dev/null || true

echo "==> Running DB migrations (alembic upgrade head) ..."
# One-off container on the same network/env as the bot. If Postgres is down
# this fails loudly here — better at deploy time than silently at runtime.
$COMPOSE run --rm bot alembic upgrade head

echo "==> Restarting bot ..."
$COMPOSE up -d

echo "==> Status:"
$COMPOSE ps
echo
echo "==> First logs (Ctrl-C safe; follow later with: $COMPOSE logs -f bot)"
sleep 3
$COMPOSE logs --tail 30 bot
