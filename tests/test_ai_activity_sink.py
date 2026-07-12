"""Unit tests for AiActivitySink + ExecutionSink outcome reporting (fake repo)."""

from __future__ import annotations

import pytest
from trading.schemas import Decision, FullAnalysis

from zetryn_bot.models.token import TokenCandidate
from zetryn_bot.pipeline.sinks import AiActivitySink


class _FakeRepo:
    def __init__(self) -> None:
        self.rows: list[dict] = []
        self.outcomes: dict[int, tuple[str, str]] = {}
        self.fail = False

    async def insert(self, **kw) -> int:
        if self.fail:
            raise RuntimeError("db down")
        self.rows.append(kw)
        return len(self.rows)

    async def set_outcome(self, row_id: int, outcome: str, detail: str = "") -> None:
        self.outcomes[row_id] = (outcome, detail)


def _analysis(rec: str = "watch", score: float = 0.62) -> FullAnalysis:
    aspect = {"score": 0.5, "verdict": "neutral", "signals": [], "reasoning": "x"}
    return FullAnalysis(
        safety=aspect,
        market=aspect,
        wallets=aspect,
        social=aspect,
        final_score=score,
        recommendation=rec,
        reasoning="momentum building",
    )


def _cand(mint="MintA") -> TokenCandidate:
    return TokenCandidate(address=mint, symbol="AAA", sources=["dexscreener", "rugcheck"])


@pytest.mark.asyncio
async def test_only_analysis_bearing_decisions_recorded():
    repo = _FakeRepo()
    sink = AiActivitySink(repo)
    await sink.emit(_cand(), Decision(action="skip", confidence=0.0))  # hard-gate style
    assert repo.rows == []
    await sink.emit(_cand(), Decision(action="watch", confidence=0.62, analysis=_analysis()))
    assert len(repo.rows) == 1
    assert repo.rows[0]["reasoning"] == "momentum building"
    assert repo.rows[0]["primary_source"] == "dexscreener"


@pytest.mark.asyncio
async def test_ai_skip_gets_terminal_outcome_at_insert():
    repo = _FakeRepo()
    sink = AiActivitySink(repo)
    await sink.emit(_cand(), Decision(action="skip", confidence=0.3, analysis=_analysis("skip")))
    assert repo.rows[0]["outcome"] == "ai_skip"
    # terminal rows are not pending — outcome updates for the mint are no-ops
    await sink.set_outcome("MintA", "opened")
    assert repo.outcomes == {}


@pytest.mark.asyncio
async def test_buyable_action_outcome_resolved_later():
    repo = _FakeRepo()
    sink = AiActivitySink(repo)
    await sink.emit(_cand(), Decision(action="watch", confidence=0.65, analysis=_analysis()))
    assert repo.rows[0]["outcome"] == ""
    await sink.set_outcome("MintA", "opened")
    assert repo.outcomes[1] == ("opened", "")


@pytest.mark.asyncio
async def test_db_failure_never_raises():
    repo = _FakeRepo()
    repo.fail = True
    sink = AiActivitySink(repo)
    await sink.emit(_cand(), Decision(action="watch", confidence=0.65, analysis=_analysis()))
    await sink.set_outcome("MintA", "opened")  # nothing pending; still no raise


@pytest.mark.asyncio
async def test_rule_route_decisions_are_recorded():
    # Sniper/graduation are rule-mode (analysis=None) but must still appear
    # in the live feed (user requirement 2026-07-12).
    repo = _FakeRepo()
    sink = AiActivitySink(repo)
    d = Decision(action="skip", confidence=0.0, reasons=["liquidity $38 too low"])
    d.meta["route"] = "sniper"
    await sink.emit(_cand(), d)
    assert len(repo.rows) == 1
    assert repo.rows[0]["route"] == "sniper"
    assert repo.rows[0]["outcome"] == "rule_skip"
    assert repo.rows[0]["reasoning"] == ""

    buy = Decision(action="buy", confidence=0.6, reasons=["pure-rule entry"])
    buy.meta["route"] = "sniper"
    await sink.emit(_cand("MintB"), buy)
    assert repo.rows[1]["outcome"] == ""  # resolved later by ExecutionSink
    await sink.set_outcome("MintB", "buy_failed")
    assert repo.outcomes[2][0] == "buy_failed"


@pytest.mark.asyncio
async def test_scanner_rule_reject_still_excluded():
    repo = _FakeRepo()
    sink = AiActivitySink(repo)
    d = Decision(action="skip", confidence=0.0, reasons=["hard gate"])
    d.meta["route"] = "scanner"
    await sink.emit(_cand(), d)
    assert repo.rows == []
