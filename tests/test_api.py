"""Dashboard API tests — auth always; data endpoints gated on DATABASE_URL_TEST."""

from __future__ import annotations

import importlib
import os

import pytest

TOKEN = "test-dashboard-token"


@pytest.fixture()
def api_app(monkeypatch, request):
    """Import the app fresh with a token + (optionally) the test DB."""
    monkeypatch.setenv("DASHBOARD_TOKEN", TOKEN)
    test_db = os.environ.get("DATABASE_URL_TEST", "")
    if test_db:
        monkeypatch.setenv("DATABASE_URL", test_db)
    import zetryn_bot.api.app as app_module

    importlib.reload(app_module)
    return app_module


@pytest.fixture()
async def client(api_app):
    from httpx import ASGITransport, AsyncClient

    async with AsyncClient(transport=ASGITransport(app=api_app.app), base_url="http://test") as c:
        yield c


@pytest.mark.asyncio
async def test_missing_token_is_401(client):
    r = await client.get("/api/overview")
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_wrong_token_is_401(client):
    r = await client.get("/api/auth/check", headers={"Authorization": "Bearer wrong"})
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_auth_check_ok(client):
    r = await client.get("/api/auth/check", headers={"Authorization": f"Bearer {TOKEN}"})
    assert r.status_code == 200 and r.json() == {"ok": True}


@pytest.mark.asyncio
async def test_overview_shape(client, session_factory, api_app):
    # session_factory fixture (conftest) skips when DATABASE_URL_TEST unset,
    # and guarantees the schema exists + M6 tables truncated.
    api_app.session_factory = session_factory
    r = await client.get("/api/overview", headers={"Authorization": f"Bearer {TOKEN}"})
    assert r.status_code == 200
    body = r.json()
    assert set(body) >= {
        "open_positions",
        "today_pnl_sol",
        "circuit_breaker",
        "closed_count",
        "win_rate",
    }


@pytest.mark.asyncio
async def test_ai_activity_roundtrip(client, session_factory, api_app):
    from sqlalchemy import text

    from zetryn_bot.db.ai_activity_repo import AiActivityRepo
    from zetryn_bot.db.models import Base

    # ensure the new table exists in the test DB + start clean
    async with session_factory().bind.begin() as conn:  # type: ignore[union-attr]
        await conn.run_sync(Base.metadata.create_all)
        await conn.execute(text("TRUNCATE ai_decisions"))

    repo = AiActivityRepo(session_factory)
    row_id = await repo.insert(
        mint="MintApi1",
        symbol="API",
        primary_source="dexscreener",
        route="scanner",
        action="watch",
        confidence=0.66,
        final_score=0.66,
        scores={"final": 0.66},
        reasoning="test reasoning",
        reasons=["r1"],
    )
    await repo.set_outcome(row_id, "opened")

    api_app.session_factory = session_factory
    r = await client.get("/api/ai-activity", headers={"Authorization": f"Bearer {TOKEN}"})
    assert r.status_code == 200
    rows = r.json()
    assert rows and rows[0]["mint"] == "MintApi1"
    assert rows[0]["outcome"] == "opened"
    assert rows[0]["reasoning"] == "test reasoning"
