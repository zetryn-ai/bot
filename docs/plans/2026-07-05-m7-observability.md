# M7 — Observability (Telegram notifier, heartbeat, crash dump)

**Date:** 2026-07-05
**Status:** Shipped (v0.7.0)
**Target version:** v0.7.0

M1–M6 built a bot that scans, decides, executes, and persists — but it is
silent. Finding out a trade fired, a source got rate-limited, or the process
crashed requires tailing `run.log` by hand. M7 adds a push-based observability
layer: Telegram notifications for the events that need a human, a periodic
heartbeat, and crash-dump capture — without touching decision logic or the
framework boundary.

## 1. Goals

1. Trade opens/closes (with PnL) and daily circuit-breaker trips push a
   Telegram message immediately.
2. Critical errors (DB unreachable, wallet load failure, RPC dead, scanner
   crash-loop) push a Telegram message — deduplicated so a repeating failure
   doesn't flood the chat.
3. Scanner rate-limits and LLM key/model rotation push a Telegram message,
   same dedup rule — visibility into *why* candidate flow slowed down.
4. A periodic heartbeat message confirms the process is alive and summarizes
   uptime, open positions, and today's PnL.
5. An unhandled crash sends a short Telegram alert and writes the full
   traceback to a local file for offline debugging.
6. All of the above is off by default (`NOTIFY_ENABLED=false`) and Telegram
   being unreachable/misconfigured never crashes the bot or blocks the
   pipeline — matches the M5/M6 fallback-safe pattern.

## 2. Decisions (locked via brainstorming)

| # | Topic | Decision |
|---|---|---|
| 1 | Channel | **Telegram only.** New Bot API token (`TELEGRAM_BOT_TOKEN` via BotFather) + `TELEGRAM_CHAT_ID` — separate from the existing `TELEGRAM_API_ID/HASH` (telethon user session used by the scanner). |
| 2 | Events | Trade open/close (PnL), circuit-breaker trips, critical errors, scanner rate-limits, and LLM key/model rotation. Routine skip/watch decisions are **not** notified (LogSink already covers those; would be too noisy). |
| 3 | Heartbeat | Fixed-interval Telegram message (default 1h): uptime, open positions, today's realized PnL, scan throughput. No HTTP `/health` endpoint yet (deferred to M9 dashboard). |
| 4 | Crash dump | Global exception handler: short traceback excerpt → Telegram, full traceback → local file (`crash-<unix-ts>.log`). |
| 5 | Throttling | Rate-limit / key-rotation / repeating-error notifications are **deduplicated per event key within a rolling window** (default 15 min) — a storm of identical warnings sends one Telegram message, not hundreds. Trade and circuit-breaker events are never throttled (each is a distinct, low-frequency, high-value event). |

Additional constraints:

- **Boundary:** notification is bot-owned I/O infrastructure, same tier as
  the M6 persistence layer. No framework code changes.
- **Fallback-safe:** `NOTIFY_ENABLED=false` (default) or a missing/invalid
  token/chat ID → `NullNotifier` (no-op). A Telegram API failure at send time
  logs a warning once and drops that message — never raises into the
  pipeline.
- **Log-bridge, not call-site rewiring:** rate-limit and key-rotation events
  already exist as `log.warning(...)` calls scattered across scanner modules
  and the framework's key pool. Rather than threading a `Notifier` into every
  one of those call sites, a loguru sink filters WARNING/ERROR records and
  forwards matching ones to the notifier. This keeps scanners/framework
  untouched — the notifier is a passive log listener for this category.
- Trade/circuit-breaker events, which need structured data (PnL, mint, size),
  are **not** log-scraped — `ExecutionSink`, `PositionTracker`, and
  `RiskManager` call the notifier directly with a formatted message.

## 3. Architecture

```
Settings (NOTIFY_ENABLED, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, ...)
        │
        ▼
  TelegramNotifier (or NullNotifier if disabled/unconfigured)
        │
   ┌────┼──────────────────────┬───────────────────────┬─────────────────┐
   ▼                           ▼                        ▼                 ▼
ExecutionSink              PositionTracker          RiskManager      log_bridge sink
(trade opened)             (trade closed, PnL)     (breaker tripped)  (WARNING: rate-limit,
   │                           │                        │              key rotation
   │                           │                        │              ERROR: critical)
   └───────────────┬───────────┴────────────────────────┘
                    ▼
           Notifier.notify(text, dedup_key=None|str)
                    │
              dedup check (in-memory, per key, rolling window)
                    ▼
           Telegram Bot API sendMessage (aiohttp, best-effort)

heartbeat_loop (background task, like execution.monitor) ──▶ same Notifier

__main__.main() ──except unhandled──▶ traceback → file + Notifier (best-effort)
```

### 3.1 New modules (all under `zetryn_bot/notify/`)

| Path | Responsibility |
|---|---|
| `notify/__init__.py` | package |
| `notify/protocol.py` | `Notifier` Protocol — `async def notify(text, *, dedup_key=None)` |
| `notify/telegram.py` | `TelegramNotifier` (aiohttp `sendMessage`, in-memory dedup) + `NullNotifier` |
| `notify/log_bridge.py` | `install_log_bridge(notifier, dedup_window_s)` — loguru sinks for ERROR (always) and WARNING (rate-limit/rotation keyword filter) |
| `notify/heartbeat.py` | `heartbeat_loop(notifier, tracker, risk, interval_s)` — periodic summary, mirrors `PositionTracker.monitor_loop`'s background-task shape |

### 3.2 Touched modules

- `zetryn_bot/config.py` — `notify_enabled`, `telegram_bot_token`,
  `telegram_chat_id`, `notify_dedup_window_s` (default 900),
  `heartbeat_interval_s` (default 3600).
- `zetryn_bot/pipeline/sinks.py` — `ExecutionSink.__init__` gains
  `notifier=None`; notifies after a successful `buy()`.
- `zetryn_bot/execution/position.py` — `PositionTracker.__init__` gains
  `notifier=None`; notifies on each close (`check_once`) with mint, reason,
  PnL.
- `zetryn_bot/execution/risk.py` — `RiskManager.__init__` gains
  `notifier=None`; notifies once when the daily circuit breaker starts
  rejecting trades (edge-triggered, not on every subsequent rejected trade).
- `zetryn_bot/__main__.py` — build the notifier, call `install_log_bridge`,
  wire it into `ExecutionSink`/`PositionTracker`/`RiskManager`, add the
  heartbeat background task, wrap `_run()` in a crash-dump handler.

## 4. Dedup mechanism

`TelegramNotifier` keeps `dict[str, float]` (`dedup_key -> last_sent
monotonic`). `notify(text, dedup_key=None)`:

- `dedup_key is None` → always send (trade/circuit-breaker events).
- `dedup_key` set and last sent within `dedup_window_s` → drop silently.
- Otherwise send and record the timestamp.

`log_bridge` derives `dedup_key` from `f"{record['level'].name}:{record['message'][:80]}"`
so distinct messages of the same shape/level collapse together (e.g. every
"Raydium rate-limited" warning within the window becomes one Telegram
message), while genuinely different errors still get through.

## 5. Crash dump

`main()` wraps `asyncio.run(_run(settings))` in `try/except Exception`:

```python
except Exception:
    tb = traceback.format_exc()
    path = f"crash-{int(time.time())}.log"
    Path(path).write_text(tb)
    log.critical("unhandled crash — dumped to {}", path)
    asyncio.run(notifier.notify(f"🔴 CRASHED — see {path}\n\n{tb[-800:]}"))
    raise
```

Re-raises after notifying so the process still exits non-zero (systemd/PM2
restart semantics untouched — M8 territory).

## 6. Testing plan

- Unit: `TelegramNotifier` dedup logic (fake clock via injectable
  `now_fn=time.monotonic`), `NullNotifier` is a true no-op, `log_bridge`
  filter matches expected WARNING/ERROR patterns and ignores INFO/DEBUG.
- Unit: `ExecutionSink`/`PositionTracker`/`RiskManager` call `notifier.notify`
  with expected text/dedup_key on buy/close/breaker-trip (notifier is a
  `ListNotifier` test double — mirrors `ListSink`).
- `scripts/m7_smoke.py`: builds a `NullNotifier` path (no token configured)
  and, if `TELEGRAM_BOT_TOKEN`+`TELEGRAM_CHAT_ID` are set in the environment,
  a live path that actually sends one real Telegram message — network-safe
  (skips cleanly without credentials, like `gmgn_check.py`).
- No framework-side tests needed — zero framework changes.

## 7. Definition of Done

- [ ] Opening/closing a paper position (with `NOTIFY_ENABLED=true` +
      credentials) produces a real Telegram message within seconds.
- [ ] Killing a scanner's network dependency (or misconfiguring
      `DATABASE_URL`) produces exactly one Telegram error message, not a
      flood, across repeated failures within the dedup window.
- [ ] Heartbeat message arrives on schedule with correct open-position count
      and PnL.
- [ ] An injected unhandled exception produces a Telegram alert AND a
      `crash-*.log` file with the full traceback.
- [ ] `NOTIFY_ENABLED=false` (default): zero Telegram calls attempted, zero
      behavior change to existing paths — verified by running the full M1–M6
      test suite unmodified in outcome.

## 8. Sub-phases

- **B1** — `notify/` package: `Notifier` Protocol, `TelegramNotifier`,
  `NullNotifier`, unit tests.
- **B2** — `log_bridge` (ERROR + WARNING keyword-filtered sinks) + unit
  tests; wire into `__main__.py` at startup.
- **B3** — Wire `ExecutionSink`/`PositionTracker`/`RiskManager` to the
  notifier; `heartbeat_loop` + background task registration; crash-dump
  wrapper in `main()`; `scripts/m7_smoke.py`; live verification.
- **B4** — CI (no new service needed — notify tests are offline unit tests),
  `.env.example`, version bump to 0.7.0, CHANGELOG, ROADMAP/plan status.
