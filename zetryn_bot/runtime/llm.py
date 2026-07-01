"""Optional LLM client wiring for the runtime.

Boundary note: the framework (`zetryn-trading`) owns LLM keys. This module
only *names* candidate providers and hands a ``ProviderConfig`` to the
framework, which resolves the key from the environment (loaded from ``.env``)
and owns rotation via its ``KeyPool``. The bot never reads or stores the key
value — it just detects, by letting the framework's resolver succeed or
raise, whether a provider is configured.

If no candidate provider has its key set, :func:`try_build_llm_client` returns
``None`` and the runtime runs the rule-only path (`build_scanner(llm_client=None)`),
which is exactly the offline path exercised by the M2 tests.
"""

from __future__ import annotations

import os

from loguru import logger
from zetryn.llm import (
    GEMINI_BASE_URL,
    GROQ_BASE_URL,
    OPENROUTER_BASE_URL,
    LLMClient,
    LLMError,
    OpenAICompatibleClient,
    ProviderConfig,
)

log = logger.bind(component="runtime.llm")

# Candidate providers tried in priority order. Each entry names the env var(s)
# holding the key (framework resolves the value) and a sensible default model.
# The model can be overridden per deployment via the ``LLM_MODEL`` env var.
_CANDIDATES: list[ProviderConfig] = [
    ProviderConfig(
        name="groq",
        base_url=GROQ_BASE_URL,
        model="llama-3.3-70b-versatile",
        key_envs=["GROQ_API_KEY", "GROQ_API_KEYS"],
    ),
    ProviderConfig(
        name="openrouter",
        base_url=OPENROUTER_BASE_URL,
        model="meta-llama/llama-3.3-70b-instruct",
        key_envs=["OPENROUTER_API_KEY", "OPENROUTER_API_KEYS"],
    ),
    ProviderConfig(
        name="gemini",
        base_url=GEMINI_BASE_URL,
        model="gemini-2.0-flash",
        key_envs=["GEMINI_API_KEY", "GOOGLE_API_KEY"],
    ),
]


def try_build_llm_client() -> LLMClient | None:
    """Build an LLM client from the first configured provider, or ``None``.

    Returns ``None`` (rule-only path) when no candidate provider's key is set.
    An ``LLM_MODEL`` env var, when present, overrides the chosen provider's
    default model.
    """
    model_override = os.environ.get("LLM_MODEL", "").strip()
    for config in _CANDIDATES:
        try:
            # build_key_pool -> resolve_keys raises LLMError when no key is set;
            # this is our presence check without the bot reading the key value.
            config.build_key_pool()
        except LLMError:
            continue
        if model_override:
            config.model = model_override
        log.info("LLM client built — provider={} model={}", config.name, config.model)
        return OpenAICompatibleClient(config)

    log.info("no LLM provider key found — running rule-only (llm_client=None)")
    return None
