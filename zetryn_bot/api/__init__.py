"""Dashboard API (M9) — read-only FastAPI app over the bot's Postgres state.

Runs as its OWN container (same image, ``uvicorn zetryn_bot.api.app:app``);
the bot process never imports this package. Strictly SELECT-only — there is
no write path from the web to the bot.
"""

from __future__ import annotations
