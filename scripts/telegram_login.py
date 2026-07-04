#!/usr/bin/env python3
"""One-time interactive Telegram login — creates the Telethon .session file.

Run this ONCE, interactively, before starting the runtime::

    python scripts/telegram_login.py

It prompts (at the terminal, never logged) for your phone number, the OTP code
Telegram sends you, and — if you have two-factor enabled — your password. On
success it writes the session file to ``TELEGRAM_SESSION_PATH`` (default
``telegram_session``). After that the runtime (`python -m zetryn_bot`) reuses
that session with no credentials at all, so phone / OTP / password never appear
in the runtime logs.

Requires ``TELEGRAM_API_ID`` and ``TELEGRAM_API_HASH`` in ``.env`` (get them
from https://my.telegram.org/apps). The generated ``*.session`` file is secret
(a full login) and is gitignored — never commit it.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
from telethon import TelegramClient

from zetryn_bot.config import Settings


async def login() -> int:
    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
    settings = Settings()

    if not settings.telegram_api_id or not settings.telegram_api_hash:
        print(
            "ERROR — set TELEGRAM_API_ID and TELEGRAM_API_HASH in .env first "
            "(get them at https://my.telegram.org/apps)."
        )
        return 1

    session_path = settings.telegram_session_path
    print(f"Session file → {session_path}.session")
    print("You'll be prompted for phone, OTP code, and 2FA password (if set).")
    print("These are entered here only — they are NOT written to any log.\n")

    client = TelegramClient(session_path, settings.telegram_api_id, settings.telegram_api_hash)
    # start() drives the interactive flow: phone (input), code (input), and
    # password (getpass — hidden). Nothing is echoed to logs.
    await client.start()

    me = await client.get_me()
    handle = f"@{me.username}" if me.username else (me.first_name or str(me.id))
    print(f"\nLogged in as {handle}. Session saved to {session_path}.session")
    print("You can now run: python -m zetryn_bot  (Telegram scanner will use this session)")
    await client.disconnect()
    return 0


def main() -> int:
    try:
        return asyncio.run(login())
    except KeyboardInterrupt:
        print("\nCancelled.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
