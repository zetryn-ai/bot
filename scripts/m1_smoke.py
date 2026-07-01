#!/usr/bin/env python3
"""M1 smoke test — exercise the Scanner + TokenEnricher Protocols end-to-end.

Run from the repo root::

    python scripts/m1_smoke.py                  # offline: import + Protocol-conformance only
    M1_SMOKE_LIVE=1 python scripts/m1_smoke.py  # online: 5s real DexScreener poll

The offline mode is what CI runs; it requires no API keys and no network.
The live mode is opt-in for local sanity-checking against real endpoints.
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

# Make the repo importable when run as a script.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from zetryn_bot import __version__
from zetryn_bot.models.token import TokenCandidate
from zetryn_bot.scanners.birdeye import BirdeyeNewListing, BirdeyeTrending
from zetryn_bot.scanners.dexscreener import (
    DexscreenerBoost,
    DexscreenerNewPairs,
    DexscreenerTrending,
)
from zetryn_bot.scanners.enrichers.gmgn_openapi import GmgnEnricher
from zetryn_bot.scanners.enrichers.helius import HeliusEnricher
from zetryn_bot.scanners.enrichers.jupiter import JupiterEnricher
from zetryn_bot.scanners.enrichers.rugcheck import RugcheckEnricher
from zetryn_bot.scanners.geckoterminal import (
    GeckoTerminalNewPools,
    GeckoTerminalTrending,
)
from zetryn_bot.scanners.protocol import Scanner, TokenEnricher
from zetryn_bot.scanners.pumpfun import PumpfunStream
from zetryn_bot.scanners.raydium import RaydiumNewPools
from zetryn_bot.scanners.telegram import TelegramScanner

_SCANNER_CLASSES: list[tuple[str, type]] = [
    ("BirdeyeTrending", BirdeyeTrending),
    ("BirdeyeNewListing", BirdeyeNewListing),
    ("DexscreenerNewPairs", DexscreenerNewPairs),
    ("DexscreenerTrending", DexscreenerTrending),
    ("DexscreenerBoost", DexscreenerBoost),
    ("GeckoTerminalNewPools", GeckoTerminalNewPools),
    ("GeckoTerminalTrending", GeckoTerminalTrending),
    ("PumpfunStream", PumpfunStream),
    ("RaydiumNewPools", RaydiumNewPools),
    ("TelegramScanner", TelegramScanner),
]

# Most scanners are zero-arg constructible. Birdeye + Telegram need a pool /
# config; we skip those in the offline test (they're checked elsewhere).
_ZERO_ARG_SCANNERS: list[tuple[str, type]] = [
    ("DexscreenerNewPairs", DexscreenerNewPairs),
    ("DexscreenerTrending", DexscreenerTrending),
    ("DexscreenerBoost", DexscreenerBoost),
    ("GeckoTerminalNewPools", GeckoTerminalNewPools),
    ("GeckoTerminalTrending", GeckoTerminalTrending),
    ("PumpfunStream", PumpfunStream),
    ("RaydiumNewPools", RaydiumNewPools),
]

_ENRICHER_CLASSES: list[tuple[str, type, tuple]] = [
    ("HeliusEnricher", HeliusEnricher, ([],)),
    ("RugcheckEnricher", RugcheckEnricher, ()),
    ("JupiterEnricher", JupiterEnricher, ()),
    ("GmgnEnricher", GmgnEnricher, ("",)),
    # TwitterEnricher needs a pre-initialized TwitterAccountPool; skipping
    # in the offline check.
]


def check_offline() -> int:
    """Verify version + Protocol conformance for every implementation.

    Returns 0 on success, 1 on any failure.
    """
    failures: list[str] = []

    print(f"zetryn_bot.__version__ = {__version__!r}")
    if __version__ != "0.2.0":
        failures.append(f"version mismatch: expected '0.2.0', got {__version__!r}")

    # Every Scanner class must have a name attribute and a stream method.
    print()
    print("Scanner classes:")
    for name, cls in _SCANNER_CLASSES:
        if not hasattr(cls, "name"):
            failures.append(f"{name}: missing 'name' class attribute")
            print(f"  ✗ {name}: missing 'name'")
            continue
        if not hasattr(cls, "stream"):
            failures.append(f"{name}: missing 'stream' method")
            print(f"  ✗ {name}: missing 'stream'")
            continue
        print(f"  ✓ {name} (name={cls.name!r})")

    # Zero-arg scanners can be instantiated + checked against the Protocol.
    print()
    print("Zero-arg Scanner instances satisfy Protocol:")
    for name, cls in _ZERO_ARG_SCANNERS:
        try:
            instance = cls()
        except Exception as exc:
            failures.append(f"{name}: __init__ raised: {exc}")
            print(f"  ✗ {name}: {type(exc).__name__}: {exc}")
            continue
        if not isinstance(instance, Scanner):
            failures.append(f"{name}: does not satisfy Scanner Protocol")
            print(f"  ✗ {name}: not a Scanner")
            continue
        print(f"  ✓ {name}")

    # Enrichers (constructible) satisfy TokenEnricher Protocol.
    print()
    print("Enricher instances satisfy TokenEnricher Protocol:")
    for name, cls, args in _ENRICHER_CLASSES:
        try:
            instance = cls(*args)
        except Exception as exc:
            failures.append(f"{name}: __init__ raised: {exc}")
            print(f"  ✗ {name}: {type(exc).__name__}: {exc}")
            continue
        if not isinstance(instance, TokenEnricher):
            failures.append(f"{name}: does not satisfy TokenEnricher Protocol")
            print(f"  ✗ {name}: not a TokenEnricher")
            continue
        print(f"  ✓ {name}")

    # TokenCandidate constructs cleanly (smoke check for the schema we depend on).
    print()
    print("TokenCandidate construction:")
    try:
        c = TokenCandidate(address="So11111111111111111111111111111111111111112")
        assert c.address.startswith("So111")
        print("  ✓ TokenCandidate(address=...) constructs")
    except Exception as exc:
        failures.append(f"TokenCandidate: {exc}")
        print(f"  ✗ TokenCandidate: {type(exc).__name__}: {exc}")

    print()
    if failures:
        print(f"FAILED — {len(failures)} issue(s):")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("OK — offline smoke test passed.")
    return 0


async def check_live() -> int:
    """Real-network test: run DexscreenerNewPairs for 5 seconds.

    Requires aiohttp installed and outbound HTTPS to api.dexscreener.com.
    Prints up to the first 5 candidates yielded.
    """
    try:
        import aiohttp
    except ImportError:
        print("aiohttp not installed — skipping live test")
        return 1

    print("Live test: DexscreenerNewPairs.stream() for ~5s ...")
    scanner = DexscreenerNewPairs(poll_interval_s=2.0)
    seen = 0
    try:
        async with aiohttp.ClientSession() as session:

            async def runner() -> None:
                nonlocal seen
                async for candidate in scanner.stream(session):
                    seen += 1
                    print(
                        f"  candidate #{seen}: "
                        f"{candidate.symbol or '?':<10} "
                        f"{candidate.address[:12]}..."
                    )
                    if seen >= 5:
                        return

            await asyncio.wait_for(runner(), timeout=8.0)
    except TimeoutError:
        print(f"  (timeout reached, saw {seen} candidate(s))")

    if seen == 0:
        print("FAILED — saw 0 candidates in 5s. Network issue or scanner regression.")
        return 1
    print(f"OK — saw {seen} candidate(s).")
    return 0


def main() -> int:
    rc = check_offline()
    if rc != 0:
        return rc
    if os.environ.get("M1_SMOKE_LIVE") == "1":
        print()
        rc = asyncio.run(check_live())
    return rc


if __name__ == "__main__":
    sys.exit(main())
