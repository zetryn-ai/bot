#!/usr/bin/env python3
"""GMGN API key checker — verify GMGN_API_KEY works against the live OpenAPI.

Run from the repo root (loads .env)::

    python scripts/gmgn_check.py [<solana_mint>]

Makes ONE authenticated read call (``/v1/token/info``) for a known token (USDC
by default) and reports PASS/FAIL with the server's exact response. Use this to
confirm a GMGN key is valid before wiring it into the runtime — the enricher's
auth (X-APIKEY + client_id, curl_cffi to clear Cloudflare) is the real path, so
a PASS here means GMGN enrichment will work in the bot.
"""

from __future__ import annotations

import asyncio
import os
import sys
import time
import uuid
from pathlib import Path

from curl_cffi.requests import AsyncSession
from dotenv import load_dotenv

# USDC — a permanently valid, active Solana token; good auth probe target.
_DEFAULT_MINT = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
_HOST = os.environ.get("GMGN_API_HOST", "https://openapi.gmgn.ai").rstrip("/")


async def check(mint: str) -> int:
    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
    raw = os.environ.get("GMGN_API_KEY", "").strip()
    if not raw:
        print("FAIL — GMGN_API_KEY is not set in .env")
        return 1
    api_key = raw.split(",")[0].strip()  # first key if a rotation list is given
    print(f"key={api_key[:12]}… (len {len(api_key)}) · host={_HOST}")

    params = {
        "chain": "sol",
        "address": mint,
        "timestamp": int(time.time()),
        "client_id": str(uuid.uuid4()),
    }
    headers = {
        "X-APIKEY": api_key,
        "Content-Type": "application/json",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    }
    async with AsyncSession() as s:
        resp = await s.get(
            f"{_HOST}/v1/token/info",
            params=params,
            headers=headers,
            impersonate="chrome120",
            timeout=12,
        )

    if resp.status_code == 200:
        try:
            body = resp.json()
        except Exception:
            print(f"FAIL — HTTP 200 but non-JSON body: {resp.text[:200]}")
            return 1
        if body.get("code") == 0:
            print(f"PASS — GMGN key valid. token/info returned data for {mint}.")
            return 0
        print(
            f"FAIL — HTTP 200 but code={body.get('code')} error={body.get('error')} "
            f"message={body.get('message')}"
        )
        return 1

    # Non-200: surface the server's reason (401 AUTH_KEY_INVALID, Cloudflare 403, …)
    detail = resp.text[:300]
    try:
        body = resp.json()
        detail = f"{body.get('error')}: {body.get('message')}"
    except Exception:
        pass
    print(f"FAIL — HTTP {resp.status_code}: {detail}")
    if resp.status_code == 401:
        print(
            "  → Key rejected. Regenerate at https://gmgn.ai/ai (upload your public "
            "key) and confirm the key is active."
        )
    return 1


def main() -> int:
    mint = sys.argv[1] if len(sys.argv) > 1 else _DEFAULT_MINT
    return asyncio.run(check(mint))


if __name__ == "__main__":
    sys.exit(main())
