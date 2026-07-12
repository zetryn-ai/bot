"""FastAPI application — every endpoint is GET + Bearer-token guarded.

Boot: ``uvicorn zetryn_bot.api.app:app --host 0.0.0.0 --port 8140``.
Refuses to start without ``DASHBOARD_TOKEN`` (read-only or not, this is
trade data). Serves the built SPA from ``zetryn_bot/api/static`` when that
directory exists (created by the Docker build stage; absent in dev, where
the Vite dev server proxies /api instead).
"""

from __future__ import annotations

import importlib.metadata
from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.staticfiles import StaticFiles
from sqlalchemy import case, func, select

from zetryn_bot.config import Settings
from zetryn_bot.db.engine import build_engine, build_session_factory
from zetryn_bot.db.models import (
    AiDecisionModel,
    ClosedTradeModel,
    PositionModel,
    RiskStateModel,
)

settings = Settings()
if not settings.dashboard_token:
    raise RuntimeError("DASHBOARD_TOKEN is not set — the dashboard API refuses to start without it")

engine = build_engine(settings.database_url)
session_factory = build_session_factory(engine)

app = FastAPI(title="ZETRYN dashboard", docs_url=None, redoc_url=None, openapi_url=None)


async def require_token(request: Request) -> None:
    header = request.headers.get("authorization", "")
    if header != f"Bearer {settings.dashboard_token}":
        raise HTTPException(status_code=401, detail="invalid or missing token")


@app.get("/api/auth/check", dependencies=[Depends(require_token)])
async def auth_check() -> dict:
    return {"ok": True}


@app.get("/api/overview", dependencies=[Depends(require_token)])
async def overview() -> dict:
    async with session_factory() as session:
        open_rows = (
            (await session.execute(select(PositionModel).order_by(PositionModel.opened_at.desc())))
            .scalars()
            .all()
        )
        today_pnl = (
            await session.execute(
                select(RiskStateModel.realized_pnl_sol).where(
                    RiskStateModel.day == datetime.now(UTC).date()
                )
            )
        ).scalar_one_or_none()
        closed_count, total_pnl, wins = (
            await session.execute(
                select(
                    func.count(ClosedTradeModel.id),
                    func.coalesce(func.sum(ClosedTradeModel.pnl_sol), 0),
                    func.count(ClosedTradeModel.id).filter(ClosedTradeModel.pnl_sol > 0),
                )
            )
        ).one()
    daily_limit = settings.risk_daily_loss_limit_sol
    today = float(today_pnl or 0)
    return {
        "open_positions": [
            {
                "mint": p.mint,
                "symbol": p.symbol,
                "size_sol": float(p.size_sol),
                "tokens_atomic": int(p.tokens_atomic),
                "confidence": float(p.confidence),
                "take_profit_pct": float(p.take_profit_pct),
                "stop_loss_pct": float(p.stop_loss_pct),
                "max_hold_s": float(p.max_hold_s),
                "opened_at": p.opened_at.isoformat(),
                "status": p.status,
                "execution_mode": p.execution_mode,
                "route": p.route,
                "unrealized_pnl_pct": (
                    float(p.unrealized_pnl_pct) if p.unrealized_pnl_pct is not None else None
                ),
                "marked_at": p.marked_at.isoformat() if p.marked_at is not None else None,
                "partials": p.partials or [],
            }
            for p in open_rows
        ],
        "today_pnl_sol": today,
        "circuit_breaker": {
            "limit_sol": daily_limit,
            "tripped": today <= -daily_limit,
        },
        "closed_count": int(closed_count),
        "total_pnl_sol": float(total_pnl),
        "win_rate": (int(wins) / int(closed_count)) if closed_count else 0.0,
    }


@app.get("/api/ai-activity", dependencies=[Depends(require_token)])
async def ai_activity(limit: int = Query(default=100, ge=1, le=500)) -> list[dict]:
    async with session_factory() as session:
        rows = (
            (
                await session.execute(
                    select(AiDecisionModel).order_by(AiDecisionModel.ts.desc()).limit(limit)
                )
            )
            .scalars()
            .all()
        )
    return [
        {
            "ts": r.ts.isoformat(),
            "mint": r.mint,
            "symbol": r.symbol,
            "source": r.primary_source,
            "route": r.route,
            "action": r.action,
            "confidence": float(r.confidence),
            "final_score": float(r.final_score),
            "scores": r.scores,
            "reasoning": r.reasoning,
            "reasons": r.reasons,
            "outcome": r.outcome,
            "outcome_detail": r.outcome_detail,
        }
        for r in rows
    ]


@app.get("/api/trades", dependencies=[Depends(require_token)])
async def trades(
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    reason: str | None = None,
    since_days: float | None = Query(default=None, gt=0),
) -> dict:
    stmt = select(ClosedTradeModel).order_by(ClosedTradeModel.closed_at.desc())
    count_stmt = select(func.count(ClosedTradeModel.id))
    if reason:
        stmt = stmt.where(ClosedTradeModel.reason == reason)
        count_stmt = count_stmt.where(ClosedTradeModel.reason == reason)
    if since_days:
        cutoff = datetime.now(UTC) - timedelta(days=since_days)
        stmt = stmt.where(ClosedTradeModel.closed_at >= cutoff)
        count_stmt = count_stmt.where(ClosedTradeModel.closed_at >= cutoff)
    async with session_factory() as session:
        total = (await session.execute(count_stmt)).scalar_one()
        rows = (await session.execute(stmt.limit(limit).offset(offset))).scalars().all()
    return {
        "total": int(total),
        "trades": [
            {
                "mint": t.mint,
                "symbol": t.symbol,
                "size_sol": float(t.size_sol),
                "tokens_atomic": int(t.tokens_atomic),
                "exit_sol": float(t.exit_sol),
                "pnl_sol": float(t.pnl_sol),
                "reason": t.reason,
                "confidence": float(t.confidence),
                "opened_at": t.opened_at.isoformat(),
                "closed_at": t.closed_at.isoformat(),
                "held_minutes": (t.closed_at - t.opened_at).total_seconds() / 60,
                "execution_mode": t.execution_mode,
                "route": t.route,
            }
            for t in rows
        ],
    }


@app.get("/api/stats", dependencies=[Depends(require_token)])
async def stats() -> dict:
    wins = func.count(ClosedTradeModel.id).filter(ClosedTradeModel.pnl_sol > 0)
    n = func.count(ClosedTradeModel.id)
    pnl = func.coalesce(func.sum(ClosedTradeModel.pnl_sol), 0)

    conf_band = case(
        (ClosedTradeModel.confidence >= 0.75, ">=0.75"),
        (ClosedTradeModel.confidence >= 0.65, "0.65-0.75"),
        else_="0.60-0.65",
    )
    day = func.date_trunc("day", ClosedTradeModel.closed_at)

    async def _grouped(session, key, label: str) -> list[dict]:
        rows = (
            await session.execute(select(key.label("k"), n, wins, pnl).group_by("k").order_by(pnl))
        ).all()
        return [
            {
                label: (k.isoformat() if isinstance(k, datetime) else str(k)),
                "trades": int(cnt),
                "wins": int(w),
                "win_rate": int(w) / int(cnt) if cnt else 0.0,
                "pnl_sol": float(p),
            }
            for k, cnt, w, p in rows
        ]

    route_key = case((ClosedTradeModel.route == "", "unrouted"), else_=ClosedTradeModel.route)

    async with session_factory() as session:
        by_reason = await _grouped(session, ClosedTradeModel.reason, "reason")
        by_route = await _grouped(session, route_key, "route")
        by_conf = await _grouped(session, conf_band, "band")
        by_day = await _grouped(session, day, "day")
    return {
        "by_reason": by_reason,
        "by_route": by_route,
        "by_confidence": by_conf,
        "by_day": by_day,
    }


@app.get("/api/equity", dependencies=[Depends(require_token)])
async def equity() -> list[dict]:
    async with session_factory() as session:
        rows = (
            await session.execute(
                select(ClosedTradeModel.closed_at, ClosedTradeModel.pnl_sol).order_by(
                    ClosedTradeModel.closed_at
                )
            )
        ).all()
    total = 0.0
    series = []
    for closed_at, pnl in rows:
        total += float(pnl)
        series.append({"ts": closed_at.isoformat(), "equity_sol": round(total, 6)})
    return series


@app.get("/api/status", dependencies=[Depends(require_token)])
async def status() -> dict:
    db_ok = True
    latest_activity = latest_trade = None
    try:
        async with session_factory() as session:
            latest_activity = (
                await session.execute(select(func.max(AiDecisionModel.ts)))
            ).scalar_one_or_none()
            latest_trade = (
                await session.execute(select(func.max(ClosedTradeModel.closed_at)))
            ).scalar_one_or_none()
    except Exception:
        db_ok = False
    return {
        "db_ok": db_ok,
        "latest_ai_activity": latest_activity.isoformat() if latest_activity else None,
        "latest_trade": latest_trade.isoformat() if latest_trade else None,
        "bot_version": importlib.metadata.version("zetryn-bot"),
        "execution_mode": settings.execution_mode,
    }


# Serve the built SPA (present in the Docker image; absent in dev).
_static = Path(__file__).parent / "static"
if _static.is_dir():
    app.mount("/", StaticFiles(directory=_static, html=True), name="spa")
