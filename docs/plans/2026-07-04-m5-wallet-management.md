# M5 — Wallet Management (live execution)

**Date:** 2026-07-04
**Status:** Shipped (v0.5.0)
**Target version:** v0.5.0

M4 shipped a paper-trading execution engine behind an `Executor` Protocol.
M5 adds a real Solana wallet — an encrypted keypair, safe non-interactive
loading, and a `LiveExecutor` that builds, signs, and submits real Jupiter
swaps — without changing `RiskManager` or `PositionTracker`. Executing live
is opt-in and guarded at multiple layers so it can never activate by
accident.

## 1. Goals

1. A private key is stored **encrypted at rest**; a one-time interactive
   script (`scripts/wallet_init.py`) creates it, and the key is **never**
   written to logs, shell history, or version control.
2. `LiveExecutor` implements the same `Executor` Protocol as `PaperExecutor`
   (M4) — real Jupiter swap, `solders` signing, RPC submission, confirmation,
   and retry-safety (never blind-resend; check on-chain truth first).
3. `EXECUTION_MODE=paper|live` selects the executor; **live requires every
   guard to pass** (enabled flag + explicit mode + wallet decrypts + trade cap)
   or it silently falls back to paper with a loud log — never a crash, never
   a silent live activation.
4. Our own compute overhead (decision → sign → submit call) stays minimal
   (no busy-waits, no blocking calls, cached balance) — the total swap
   latency is still dominated by the Jupiter API and Solana confirmation
   time (hundreds of ms to a few seconds), which is outside the bot's control.
5. Zero changes to `RiskManager` / `PositionTracker` internals — only a new
   `Executor` implementation and a hard cap on live trade size.

## 2. Decisions (locked via brainstorming)

| # | Topic | Decision |
|---|---|---|
| 1 | Key storage | **Encrypted keyfile (Fernet) + passphrase from env.** `wallet.enc` (gitignored) holds the base58 private key encrypted; `WALLET_PASSPHRASE` decrypts it into an in-memory `solders.Keypair` at startup. Never plaintext at rest. |
| 2 | Mode toggle | **`EXECUTION_MODE=paper\|live`, layered guards.** Live activates only when `EXECUTION_ENABLED=true` AND `EXECUTION_MODE=live` AND the wallet decrypts AND a trade-size cap is set. Any failure falls back to paper with a loud warning — never implicit, never a crash. |
| 3 | Retry safety | **Confirm + idempotency guard.** Per-mint `asyncio.Lock` (no parallel swaps for the same mint). On confirmation timeout, check on-chain truth (signature status / balance delta) before ever retrying — never blind-resend. |
| 4 | Swap params | **Configurable + balance guard.** Slippage bps and priority fee from `Settings`; a cached SOL balance (refreshed on a TTL, not queried on the hot path) gates every buy against insufficient funds + gas reserve. |
| 5 | Latency target | **Our own compute overhead only** (sign + decision + logging), not full swap latency. Sign is local (<1ms via solders); the Jupiter API + Solana confirmation dominate end-to-end time (hundreds of ms–seconds) and are outside the bot's control. Balance checks are cached specifically to keep the buy hot path free of an extra RPC round-trip. |

Additional constraints:

- **Boundary:** wallet, signing, and live execution are bot-owned (M4/M5).
  The framework never sees the keypair or touches execution.
- No real transactions are ever sent in tests or CI — the live path is only
  exercised against mocked RPC/Jupiter.
- Zero changes to `scanners/`, `adapters/`, `models/token.py`, `RiskManager`'s
  or `PositionTracker`'s core logic beyond adding the trade-size cap.
- `SOLANA_RPC_URL` in `.env` currently holds a bare API key/UUID, not a full
  URL — this must be corrected to a real RPC endpoint before live mode can
  work (e.g. `https://mainnet.helius-rpc.com/?api-key=<key>`).

## 3. Architecture

```
wallet.enc (Fernet, gitignored)         scripts/wallet_init.py (one-time, interactive)
        │  WALLET_PASSPHRASE (env)                base58 via getpass -> writes wallet.enc
        ▼
   Wallet.load() -> solders.Keypair (in-memory only; repr/str never expose it)
        │
        ▼
   LiveExecutor(wallet, rpc, jupiter)  -- implements Executor Protocol, same as PaperExecutor
        │  balance cache (TTL) -> guard -> Jupiter /swap -> sign (solders) -> send (RPC)
        │  -> confirm -> on timeout: check on-chain BEFORE any retry
        ▼
   Selected in __main__ by EXECUTION_MODE, behind layered guards
   (falls back to PaperExecutor on any guard failure)
```

### 3.1 New modules (all under `zetryn_bot/`)

| Path | Responsibility |
|---|---|
| `wallet/__init__.py` | package |
| `wallet/keystore.py` | `Wallet` — decrypt `wallet.enc` into a `solders.Keypair`; `pubkey`; no-key-in-log |
| `execution/rpc.py` | `SolanaRpc` — thin `solana-py AsyncClient` wrapper: send, confirm, check-landed, token balance delta |
| `execution/live.py` | `LiveExecutor` — real swap: quote → build → sign → send → confirm → verify; per-mint lock; `BalanceCache` |
| `scripts/wallet_init.py` | one-time interactive keyfile creation (`getpass`, never logged) |

New dependencies: `solders`, `solana` (AsyncClient), `base58`, `cryptography` (Fernet).

### 3.2 `Settings` additions

```python
execution_mode: str = "paper"          # "paper" | "live"
wallet_keyfile_path: str = "wallet.enc"
wallet_passphrase: str = ""            # env only; never defaulted, never logged
wallet_min_sol_reserve: float = 0.05   # gas reserve, never spent on trades
wallet_max_trade_sol: float = 0.5      # absolute per-trade cap for live (independent of RISK_BASE_SIZE_SOL)
live_slippage_bps: int = 200
live_priority_fee_lamports: int | None = None  # None = Jupiter auto
```

## 4. Components

**`Wallet`** — `Wallet.load(path, passphrase)` decrypts (Fernet, PBKDF2HMAC-derived
key) and returns a `Wallet` wrapping a `solders.Keypair`. `__repr__`/`__str__`
only ever show the pubkey. Raises clearly on wrong passphrase / corrupt file
(caught by the caller for paper fallback, never crashes the whole runtime).

**`scripts/wallet_init.py`** — interactive, one-time: `getpass` for the base58
private key and a new passphrase, encrypts, writes `wallet.enc`, prints the
public key. Neither secret ever touches disk unencrypted or a log line.

**`BalanceCache`** — refreshes the wallet's SOL balance on a TTL (e.g. 10s) via
a background refresh, not queried inline on every buy — keeps the decision
hot path free of an extra RPC round trip.

**`LiveExecutor.buy`** — per-mint `asyncio.Lock` → balance-cache guard
(`balance >= size + min_reserve`) → Jupiter `/swap` build → `VersionedTransaction`
sign (local, <1ms) → `SolanaRpc.send_transaction` → `confirm(timeout)` → on
timeout, `check_signature_landed` (source of truth) before any retry decision
→ read actual token balance delta → return `Position`.
**`LiveExecutor.sell`** — mirror flow, mint→SOL.

**Executor selection (`__main__`)** — live requires
`execution_enabled AND execution_mode=="live" AND Wallet.load succeeds`;
any failure logs an error and falls back to `PaperExecutor`. When live is
active, a `WARNING`-level startup banner announces it with the pubkey and
`wallet_max_trade_sol`. `RiskManager` gets one addition: sizing is capped at
`min(base_size_sol × confidence, wallet_max_trade_sol)`.

## 5. Testing (offline, no RPC, no real funds)

| Test | Coverage |
|---|---|
| `tests/test_wallet_keystore.py` | encrypt→decrypt round trip; wrong passphrase raises clearly; `repr()`/`str()` never leak the private key |
| `tests/test_live_executor.py` | buy/sell with mocked `SolanaRpc` + `JupiterQuote`: balance guard, per-mint lock (concurrency, like M4's), timeout → on-chain check → no blind retry |
| `tests/test_balance_cache.py` | TTL refresh behaviour; not queried inline on the buy path (injected clock) |
| Extend `tests/test_risk.py` | `wallet_max_trade_sol` cap applied when set |

`scripts/wallet_init.py` is not auto-tested (interactive by design) — documented
manually in the README. `scripts/m5_smoke.py` — offline: decrypt a throwaway
test keyfile, drive `LiveExecutor.buy()` against fully mocked RPC/Jupiter,
assert the sign→guard→confirm flow without ever sending a real transaction.

## 6. Out of scope

- Wallet monitoring / sweeper, multi-wallet rotation — future hardening, not
  blocking v0.5.0.
- Jito bundles / private mempool / co-location for sub-block-time execution —
  out of scope for this bot's architecture entirely (see latency discussion).
- Persistence of wallet/trade history — M6.
- Notifier on live fills — M7.

## 7. Execution sub-phases

| Sub-phase | Scope |
|---|---|
| **B1** | `wallet/keystore.py` + `scripts/wallet_init.py` + keystore tests. New deps in pyproject. |
| **B2** | `execution/rpc.py` + `execution/live.py` (+ `BalanceCache`) + `__main__` wiring (`EXECUTION_MODE` + guards) + `RiskManager` cap + `scripts/m5_smoke.py` + tests. |
| **B3** | Version `0.5.0`, CHANGELOG, ROADMAP M5 → ✅, `.env.example`, `.gitignore` (`wallet.enc`), design doc → Shipped, tag + release. |

## 8. Definition of done

1. `scripts/wallet_init.py` creates `wallet.enc` from a pasted base58 key;
   neither secret ever appears in logs or shell history.
2. `EXECUTION_MODE=live` with all guards passing activates `LiveExecutor` with
   a visible `WARNING` banner; any guard failure falls back to paper.
3. `python -m pytest` green (M4's 55 + ~12 new); `ruff` clean.
4. `scripts/m5_smoke.py` passes offline — no real transaction ever sent in
   tests/CI.
5. `__version__ = "0.5.0"`; CHANGELOG; ROADMAP M5 ✅; this doc Shipped.
