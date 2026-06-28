# M1 — Scanner Refactor & Baseline

**Date:** 2026-06-28
**Status:** Shipped (v0.1.0)
**Target version:** v0.1.0

Refactor the 11 scanner source modules imported from the cdexio bot
(see foundational commit) into a coherent, designed subsystem with a
well-defined `Scanner` Protocol, a separate `TokenEnricher` Protocol for
on-demand enrichment, and a consistent style and language convention
suitable for a public template repository.

## 1. Goals

1. Every scanner adheres to one of two clear contracts (`Scanner` or
   `TokenEnricher`) — no more "11 scripts each doing their own thing".
2. Scanners are **decoupled from their sink** — they yield
   `TokenCandidate` values rather than publishing to Redis directly, so
   downstream callers (Redis, `zetryn-trading` agents, tests, dashboard
   feeders) can consume scanners uniformly.
3. The codebase reads as a designed template, not a copy — forkers
   should be able to read any scanner and understand its contract from
   the module docstring alone.
4. Style and language are unified to a single English baseline,
   enforced by a strict but reasonable `ruff` configuration.

## 2. Decisions (locked via brainstorming)

| # | Topic | Decision |
|---|---|---|
| 1 | Refactor scope | (C) Architectural — define Protocol, standardize all scanners, factor common helpers, categorize where useful |
| 2 | Scanner interface | (B) Pull-based — `def stream(...) -> AsyncIterator[TokenCandidate]`. Caller decides what to do with each candidate. Separate `TokenEnricher` Protocol for on-demand lookups |
| 3 | File organization | (C) Hybrid — `enrichers/` subfolder for `TokenEnricher` impls; all other scanners flat at `scanners/` |
| 4 | Language | (A1) All English — module/function docstrings, inline comments, commit messages. No Indonesian in committed code |
| 5 | Style strictness | (B1) Production-grade strict — `ruff` select `["E","F","I","B","UP","SIM","TCH","RUF"]`, line 100, `from __future__ import annotations` everywhere, pre-commit hook |
| 6 | Type hints | (C1) Best-effort — public functions typed; private functions encouraged; no `mypy --strict` in CI (overhead vs benefit) |

Additional declared constraints:
- `TokenCandidate` schema **stays as-is** in M1. Schema redesign /
  normalization is M2 scope (when wiring to `zetryn-trading.TokenInput`).
- `Scanner` and `TokenEnricher` Protocols live in
  `zetryn_bot/scanners/protocol.py` (separate file, importable cleanly).
- Zero tests in M1. Testing happens in M2 around the integration with
  `zetryn-trading`.
- Minimal CI in M1 — `ruff check` + import smoke test only.
- Every module exports its public API explicitly via `__all__`.

## 3. Protocol contracts

### 3.1 `Scanner` — continuous candidate stream

For streaming/polling/social sources that yield `TokenCandidate`s over
time.

```python
# zetryn_bot/scanners/protocol.py

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Protocol, runtime_checkable

import aiohttp

from zetryn_bot.models.token import TokenCandidate


@runtime_checkable
class Scanner(Protocol):
    """A source of token candidates.

    Implementations yield :class:`TokenCandidate` values as they are
    discovered (polling), streamed (WebSocket), or surfaced from social
    feeds. The caller decides what to do with each candidate — publish to
    Redis, feed into a ``zetryn-trading`` agent, log, filter, etc.

    Scanners must:
    - Not call any sink (Redis, network) other than the source they
      scan. No direct publishing.
    - Be cancellable: ``async for`` callers can break out at any time;
      the scanner must release HTTP / WebSocket / DB resources cleanly
      via context managers or ``finally`` blocks.
    - Handle transient errors gracefully — log and continue the loop;
      don't propagate one-off HTTP failures to the caller.
    - Sleep between polls when polling; use ``asyncio.sleep`` not
      blocking sleep.
    """

    name: str
    """Stable identifier, used in logs and supervision (e.g. ``"dexscreener"``)."""

    def stream(self, session: aiohttp.ClientSession) -> AsyncIterator[TokenCandidate]:
        """Yield :class:`TokenCandidate` values indefinitely.

        Args:
            session: A shared :class:`aiohttp.ClientSession`. The scanner
                must not close this session — the caller owns its
                lifecycle.

        Yields:
            One :class:`TokenCandidate` per discovered token. Duplicate
            handling (within the same scanner) is the scanner's
            responsibility; cross-scanner dedup is the caller's.
        """
        ...
```

**Caller pattern**:

```python
import aiohttp
from zetryn_bot.storage import publish_sniper, connect
from zetryn_bot.scanners.dexscreener import DexscreenerNewPairs

async def main():
    redis = await connect("redis://localhost:6379/0")
    async with aiohttp.ClientSession() as session:
        scanner = DexscreenerNewPairs()
        async for candidate in scanner.stream(session):
            await publish_sniper(redis, candidate.model_dump())
```

### 3.2 `TokenEnricher` — on-demand lookup

For sources whose job is to take a mint address and return enrichment
data (holder distribution, safety analysis, etc.).

```python
@runtime_checkable
class TokenEnricher(Protocol):
    """An on-demand token-detail lookup.

    Unlike :class:`Scanner`, an enricher does not stream candidates — it
    takes a mint address and returns enriched data, used to top up a
    :class:`TokenCandidate` already obtained from a Scanner.
    """

    name: str

    async def enrich(
        self,
        mint: str,
        candidate: TokenCandidate,
        session: aiohttp.ClientSession,
    ) -> TokenCandidate:
        """Return a new candidate with enriched fields populated.

        Args:
            mint: SPL mint address.
            candidate: Current :class:`TokenCandidate` for the mint. Pass
                a fresh one if you only have the address.
            session: Shared :class:`aiohttp.ClientSession`.

        Returns:
            A new :class:`TokenCandidate` with this enricher's fields
            populated. Other fields are passed through unchanged. The
            input ``candidate`` should be treated as immutable —
            implementations return a copy via ``model_copy(update=...)``.

        Raises:
            Implementation-specific. Callers should expect transient
            HTTP failures and decide whether to retry, skip, or abort.
        """
        ...
```

**Caller pattern**:

```python
from zetryn_bot.scanners.enrichers.helius import HeliusEnricher

helius = HeliusEnricher(api_keys=[...])

async for candidate in dexscreener.stream(session):
    enriched = await helius.enrich(candidate.address, candidate, session)
    # ``enriched`` now has holder_count, top10_holder_pct populated
    await sink(enriched)
```

## 4. File reorganization

### 4.1 Target layout

```
zetryn_bot/scanners/
├── __init__.py                # Re-exports Protocols; no orchestration
├── protocol.py                # Scanner + TokenEnricher Protocols + shared types
├── _common.py                 # Shared helpers (rate-limit decorator, retry wrapper, etc.)
├── birdeye.py                 # Scanner (polling, requires BIRDEYE_API_KEYS)
├── dexscreener.py             # Scanner (polling)
├── geckoterminal.py           # Scanner (polling)
├── gmgn_openapi.py            # Scanner (polling, curl-cffi for TLS impersonation)
├── jupiter.py                 # Scanner (polling, price/quote)
├── pumpfun.py                 # Scanner (WebSocket streaming)
├── raydium.py                 # Scanner (polling)
├── telegram.py                # Scanner (social, telethon)
├── twitter.py                 # Scanner (social, twitter_login + VADER)
└── enrichers/                 # TokenEnricher impls (different Protocol)
    ├── __init__.py
    ├── helius.py              # Holder distribution, on-chain enrichment
    └── rugcheck.py            # Safety analysis enrichment
```

### 4.2 What moves

| File | From | To | Reason |
|---|---|---|---|
| `helius.py` | `scanners/` | `scanners/enrichers/` | Implements `TokenEnricher`, not `Scanner` |
| `rugcheck.py` | `scanners/` | `scanners/enrichers/` | Implements `TokenEnricher`, not `Scanner` |
| `protocol.py` | (new) | `scanners/` | Holds the two Protocol definitions |
| `_common.py` | (new) | `scanners/` | Shared rate-limit / retry / source-name helpers |

### 4.3 What changes inside each scanner file

Each top-level scanner module gets normalized to this template:

```python
"""<Scanner name> — <one-line what it scans>.

Source: <URL or docs link>
Auth: <env var name(s) or "none">
Mechanism: <"REST polling every Ns" | "WebSocket stream" | "on-demand lookup">
Rate limits: <known limits, e.g. "60 RPM per key">
Output channel hint: <"sniper" | "momentum" | "migration"> (for downstream routing)
"""

from __future__ import annotations

# ... imports

class DexscreenerNewPairs:
    """Polling scanner for DexScreener new-pair endpoint."""

    name = "dexscreener.new_pairs"

    def __init__(self, poll_interval_s: float = 10.0) -> None:
        self._poll_interval_s = poll_interval_s

    async def stream(self, session: aiohttp.ClientSession) -> AsyncIterator[TokenCandidate]:
        while True:
            try:
                async with session.get(URL) as r:
                    data = await r.json()
                for item in data:
                    yield self._to_candidate(item)
            except aiohttp.ClientError as exc:
                logger.bind(component=self.name).warning(f"poll error: {exc}")
            await asyncio.sleep(self._poll_interval_s)

    @staticmethod
    def _to_candidate(raw: dict) -> TokenCandidate:
        ...
```

Key shifts vs the cdexio originals:
- **Free function → class**. Each scanner becomes a class implementing
  the `Scanner` Protocol. This makes the `name` attribute explicit and
  allows scanner-specific config (poll interval, API key reference) to
  live on `self`.
- **Push → yield**. `await publish_X(...)` calls inside scanner bodies
  are removed. Replaced with `yield candidate` from the async
  iterator.
- **Class per "mode"**. A scanner that has multiple modes in the
  cdexio (`poll_dexscreener_new_pairs`, `poll_dexscreener_trending`,
  `poll_dexscreener_boost`) becomes multiple classes:
  `DexscreenerNewPairs`, `DexscreenerTrending`, `DexscreenerBoost`.
  Each class instance is one scanner with one name.

## 5. Style + language config

### 5.1 `pyproject.toml` additions

```toml
[tool.ruff]
line-length = 100
target-version = "py311"

[tool.ruff.lint]
select = [
    "E",    # pycodestyle errors
    "F",    # pyflakes
    "I",    # isort
    "B",    # bugbear (mutable defaults, etc.)
    "UP",   # pyupgrade (modern syntax)
    "SIM",  # simplify (no-else-return, etc.)
    "TCH",  # type-checking only imports
    "RUF",  # ruff-specific
]
ignore = [
    "E501",  # line-too-long — formatter handles it
    "B008",  # function call in default arg — pydantic Field uses this
]

[tool.ruff.lint.isort]
known-first-party = ["zetryn_bot"]

[tool.ruff.format]
quote-style = "double"
```

### 5.2 Pre-commit hook (optional, recommended)

`.pre-commit-config.yaml`:

```yaml
repos:
  - repo: https://github.com/astral-sh/ruff-pre-commit
    rev: v0.6.9
    hooks:
      - id: ruff
        args: [--fix]
      - id: ruff-format
```

### 5.3 Language sweep

- All `# komentar` (Indonesian inline comments) → English translation.
- Docstrings: triple-quoted, English, with section headings (`Args:`,
  `Returns:`, `Yields:`, `Raises:`) per PEP 257 + Google-style.
- Variable names: already English; verify on the sweep.
- Log messages: English. Format
  `logger.bind(component="...").info(f"...")`.

## 6. Out of scope

- Schema redesign — `TokenCandidate` stays. M2 concern.
- Tests — zero in M1. Real tests come in M2 when wired up.
- CI workflow beyond `ruff check` — M2/M3 grow this.
- Integration with `zetryn-trading` agents — M2.
- `main.py` / runtime orchestration — M3.
- Performance benchmarking — not a baseline concern.
- New scanner sources — anything new (Cielo, Photon, Trojan, etc.) is a
  separate milestone or a follow-up commit, not part of M1.

## 7. Execution sub-phases

M1 is split into five sub-phases for reviewability. Each is one commit
(identity = random via `commit-as.sh`, since these are refactor commits,
not version-tag commits).

| Sub-phase | Scope | Approx files changed |
|---|---|---|
| **B1** | Add `scanners/protocol.py` + `scanners/_common.py` + update `scanners/__init__.py` re-exports | +3 |
| **B2** | Move enrichers — create `scanners/enrichers/`, move helius + rugcheck, refactor to `TokenEnricher` Protocol | 4 (2 moved, 2 refactored) |
| **B3** | Refactor 9 top-level scanners to `Scanner` Protocol (async iterator, class-based, `name` attribute). One commit per scanner OR bundled by category — TBD at execution time | 9 |
| **B4** | Apply ruff config to `pyproject.toml`, run `ruff check --fix` + `ruff format` across `zetryn_bot/`. Add `.pre-commit-config.yaml` | 1 config + sweep |
| **B5** | Update README (rewrite Phase 1 section to reflect M1-shipped state), update `__init__.py` version → `0.1.0`, add CHANGELOG entry, mark this design doc Status: Shipped (v0.1.0) | 4–5 |

Final commit at end of B5 bumps version and marks M1 done.

## 8. Verification (per sub-phase)

- After B1: `python -c "from zetryn_bot.scanners.protocol import Scanner, TokenEnricher"` works.
- After B2: `python -c "from zetryn_bot.scanners.enrichers.helius import HeliusEnricher"` works.
- After each scanner refactored in B3:
  `python -c "from zetryn_bot.scanners.dexscreener import DexscreenerNewPairs; assert hasattr(DexscreenerNewPairs, 'stream')"`.
- After B4: `ruff check zetryn_bot/` returns clean, `ruff format --check zetryn_bot/` returns clean.
- After B5: `from zetryn_bot import __version__; assert __version__ == "0.1.0"`.

A new minimal end-to-end smoke test goes in `scripts/m1_smoke.py` (a
one-shot script that instantiates one of each scanner type and runs the
`stream()` loop for 5 seconds against the real DexScreener endpoint —
opt-in via env flag, not run in CI).

## 9. Definition of done

M1 is shipped when:

1. All 11 scanner files conform to the appropriate Protocol (`Scanner` or
   `TokenEnricher`).
2. `ruff check` and `ruff format --check` pass on the entire `zetryn_bot/`
   tree.
3. Every scanner module has a module docstring per the template in §4.3.
4. README reflects the new architecture (no leftover "11 scripts each
   doing their own thing" phrasing).
5. `__version__ = "0.1.0"` in `zetryn_bot/__init__.py` and
   `pyproject.toml`.
6. CHANGELOG.md has a `## [0.1.0]` entry summarizing M1.
7. This design doc Status changes from `Draft` → `Shipped (v0.1.0)`.

## 10. Not covered (intentional defer)

The cdexio code carries patterns this milestone does **not** import:

- `executor.position_manager`, `executor.position_tracker`,
  `executor.reconciliation`, `executor.risk_agent` — execution layer, M4.
- `wallet.encryption`, `wallet.monitor`, `wallet.sweeper` — wallet, M5.
- `notifier.py`, `circuit_breaker.py`, `macro.context` — operational
  concerns, M7.
- `pipeline.scorer`, `pipeline.filter`, `pipeline.score_cache`,
  `pipeline.batch_collector` — decision tier, lives in
  `zetryn-trading` agents (wired in M2).
- `pipeline.normalizer`, `pipeline.deduplicator` — these MAY come back
  in M2 as part of "scanner output → `TokenInput` adapter", but their
  shape may change significantly.
- `storage.db` (PostgreSQL) — persistence, M6.
- `api/`, `web/`, `tools/`, `deploy/`, `Dockerfile`, `Makefile` — M8/M9.

If any of these patterns sneak into M1 commits, that's scope leak and
should be flagged in review.
