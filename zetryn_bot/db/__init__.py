"""Persistence layer (M6) — PostgreSQL-backed state that survives restarts.

Open positions, closed-trade history, the daily-loss circuit breaker, and the
framework's decision log are persisted here so a restart or crash no longer
loses state. All async (SQLAlchemy 2.0 + asyncpg). Repositories are optional
everywhere — passing ``repo=None`` keeps the M4/M5 in-memory behaviour, and a
DB connection failure at startup falls back to in-memory rather than crashing.
"""
