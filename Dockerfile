# zetryn-bot runtime image (M8).
#
#   docker build -t zetryn-bot .
#   docker run --rm --env-file .env zetryn-bot
#
# Two stages so git (needed only to pip-install twitter_login from GitHub)
# never ships in the final image. All heavy deps (solders, curl-cffi,
# asyncpg) install from manylinux wheels — no compiler needed.

FROM python:3.12-slim AS builder

RUN apt-get update \
    && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /src
COPY pyproject.toml README.md ./
COPY zetryn_bot ./zetryn_bot
RUN pip install --no-cache-dir --prefix=/install .


FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    NLTK_DATA=/usr/local/share/nltk_data

COPY --from=builder /install /usr/local

# Pre-download the VADER lexicon so the twitter enricher never downloads at
# runtime (the container may have no writable HOME cache and cold-start lag
# would hit the first scan). twitter_login writes a .cache dir INSIDE its own
# site-packages at import time — pre-create it writable for the non-root user
# or every cookie account fails with EACCES (seen on the first VPS deploy).
RUN python -c "import nltk; nltk.download('vader_lexicon', quiet=True, download_dir='$NLTK_DATA')" \
    && useradd --create-home --uid 1000 bot \
    && mkdir -p /usr/local/lib/python3.12/site-packages/twitter_login/.cache \
    && chown -R bot:bot /usr/local/lib/python3.12/site-packages/twitter_login/.cache

WORKDIR /app
# Alembic (migrations run as a one-off container in deploy.sh) and the smoke
# scripts (in-container verification) are repo files, not part of the package.
COPY alembic.ini ./
COPY alembic ./alembic
COPY scripts ./scripts
# logs/ = loguru file sink, data/ = telegram session + (later) wallet.enc —
# both bind-mounted in docker-compose.vps.yml so they outlive the container.
RUN mkdir -p /app/logs /app/data && chown -R bot:bot /app

USER bot

CMD ["python", "-m", "zetryn_bot"]
