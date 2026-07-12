"""LLM wiring for the runtime — a failover router over every configured provider.

Boundary note: the framework (`zetryn-trading`) owns keys, rotation, throttle
tracking, and failover (``KeyPool`` + ``LLMRouter``). This module only detects
which providers have keys in the environment and declares the failover order;
the framework does the rest.

Failover chain (first configured entry wins per call; on rate-limit /
exhaustion the router moves down). Each groq model is a SEPARATE per-key
quota bucket, so one set of keys carries several entries:

    1. groq / llama-3.3-70b-versatile     (primary quality)
    2. cerebras / gpt-oss-120b            (needs CEREBRAS_API_KEY — 2.6k tok/s)
    3. groq / llama-4-scout-17b           (30k TPM/key — biggest headroom)
    4. groq / gpt-oss-120b
    5. sambanova / llama-3.3-70b          (needs SAMBANOVA_API_KEY)
    6. groq / llama-3.1-8b-instant        (volume: 14.4k RPD/key)
    7. openrouter / llama-3.3-70b:free
    8. gemini / 2.5-flash, 9. 2.5-flash-lite (last resort — tiny free RPD)

Rationale (2026-07-11 dry run): a single groq client with 3 retry attempts
produced bursts of "LLM unavailable (LLMError)" conservative skips whenever a
candidate burst exceeded the per-key RPM/TPM — 14 of 17 keys were idle-cooling
while the call gave up. Key rotation alone is not enough; model- and
provider-level failover is, and the framework ships it (``LLMRouter``).
"""

from __future__ import annotations

import os

from loguru import logger
from zetryn.auth.subscription import RateLimit
from zetryn.llm import (
    CEREBRAS_BASE_URL,
    GEMINI_BASE_URL,
    GROQ_BASE_URL,
    OPENROUTER_BASE_URL,
    PROVIDER_FREE_TIER_LIMITS,
    SAMBANOVA_BASE_URL,
    LLMClient,
    LLMRouter,
    OpenAICompatibleClient,
    ProviderConfig,
    RouterEntry,
)

log = logger.bind(component="runtime.llm")

# (entry name, provider, base_url, model, env vars with the CSV keys)
# Ordered by quality-then-volume; entries whose env vars are unset are skipped,
# so adding a provider is just adding its key to .env (e.g. CEREBRAS_API_KEY /
# SAMBANOVA_API_KEY slots activate on the next restart, no code change).
# All groq model ids verified live 2026-07-12 (17 models on /v1/models).
_CHAIN: list[tuple[str, str, str, str, list[str]]] = [
    (
        "groq/llama-3.3-70b",
        "groq",
        GROQ_BASE_URL,
        "llama-3.3-70b-versatile",
        ["GROQ_API_KEY", "GROQ_API_KEYS"],
    ),
    (
        # ~2,600 tok/s on a separate provider's quota. NOTE: cerebras retired
        # llama-3.3-70b (live check 2026-07-12: gpt-oss-120b / gemma-4-31b /
        # zai-glm-4.7 only) — the framework's limits table is stale there.
        "cerebras/gpt-oss-120b",
        "cerebras",
        CEREBRAS_BASE_URL,
        "gpt-oss-120b",
        ["CEREBRAS_API_KEY", "CEREBRAS_API_KEYS"],
    ),
    (
        # Separate per-key quota bucket with the biggest TPM on groq free (30k).
        "groq/llama-4-scout-17b",
        "groq",
        GROQ_BASE_URL,
        "meta-llama/llama-4-scout-17b-16e-instruct",
        ["GROQ_API_KEY", "GROQ_API_KEYS"],
    ),
    (
        "groq/gpt-oss-120b",
        "groq",
        GROQ_BASE_URL,
        "openai/gpt-oss-120b",
        ["GROQ_API_KEY", "GROQ_API_KEYS"],
    ),
    (
        "sambanova/llama-3.3-70b",
        "sambanova",
        SAMBANOVA_BASE_URL,
        "Meta-Llama-3.3-70B-Instruct",
        ["SAMBANOVA_API_KEY", "SAMBANOVA_API_KEYS"],
    ),
    (
        # Volume workhorse: 14.4k RPD/key.
        "groq/llama-3.1-8b",
        "groq",
        GROQ_BASE_URL,
        "llama-3.1-8b-instant",
        ["GROQ_API_KEY", "GROQ_API_KEYS"],
    ),
    (
        "openrouter/llama-3.3-70b:free",
        "openrouter",
        OPENROUTER_BASE_URL,
        "meta-llama/llama-3.3-70b-instruct:free",  # :free id verified 2026-07-12
        ["OPENROUTER_API_KEY", "OPENROUTER_API_KEYS"],
    ),
    (
        "gemini/2.5-flash",
        "gemini",
        GEMINI_BASE_URL,
        "gemini-2.5-flash",
        ["GEMINI_API_KEY", "GOOGLE_API_KEY"],
    ),
    (
        "gemini/2.5-flash-lite",
        "gemini",
        GEMINI_BASE_URL,
        "gemini-2.5-flash-lite",
        ["GEMINI_API_KEY", "GOOGLE_API_KEY"],
    ),
]


def _env_keys(names: list[str]) -> list[str]:
    """Return the comma-split keys from the first set env var in ``names``.

    Detection + key count only — since zetryn-trading 1.2.0 the framework's
    resolver splits CSV values itself; this module never passes key values.
    """
    for name in names:
        raw = os.environ.get(name, "").strip()
        if raw:
            return [k.strip() for k in raw.split(",") if k.strip()]
    return []


def _pool_limit(provider: str, model: str, n_keys: int) -> RateLimit | None:
    """Scale the framework's per-KEY free-tier limit to the whole key pool.

    The router's throttle is per entry; with N rotated keys the effective
    budget is N x the per-key numbers. Best-effort — the provider's own 429
    stays the source of truth (the entry then cools down and the router
    fails over).
    """
    per_key = PROVIDER_FREE_TIER_LIMITS.get(provider, {}).get(model)
    if per_key is None:
        return None
    scale = lambda v: v * n_keys if v else None  # noqa: E731
    return RateLimit(
        rpm=scale(per_key.rpm),
        rpd=scale(per_key.rpd),
        tpm=scale(per_key.tpm),
        tpd=scale(per_key.tpd),
    )


def try_build_llm_client() -> LLMClient | None:
    """Build the failover router from every provider with keys in the env.

    Returns ``None`` (rule-only path) when no provider is configured, the
    single client when exactly one chain entry is available, or an
    ``LLMRouter`` over all available entries otherwise. ``LLM_MODEL``
    overrides the PRIMARY entry's model only.
    """
    model_override = os.environ.get("LLM_MODEL", "").strip()

    entries: list[RouterEntry] = []
    for name, provider, base_url, model, key_envs in _CHAIN:
        keys = _env_keys(key_envs)
        if not keys:
            continue
        if model_override and not entries:  # primary entry only
            model = model_override
        config = ProviderConfig(
            name=provider,
            base_url=base_url,
            model=model,
            key_envs=key_envs,
            # More key-rotation attempts per call when the pool is deep —
            # 3 attempts against 17 keys was the observed failure mode.
            max_retries=min(max(3, len(keys) // 2), 8),
        )
        entries.append(
            RouterEntry(
                client=OpenAICompatibleClient(config),
                name=name,
                limit=_pool_limit(provider, model, len(keys)),
            )
        )
        log.info("LLM chain entry — {} keys={} model={}", name, len(keys), model)

    if not entries:
        log.info("no LLM provider key found — running rule-only (llm_client=None)")
        return None
    if len(entries) == 1:
        log.info("LLM client built — single entry {}", entries[0].name)
        return entries[0].client
    log.info("LLM router built — {} failover entries", len(entries))
    return LLMRouter(entries)
