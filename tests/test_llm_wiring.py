"""Unit tests for optional LLM client wiring."""

from __future__ import annotations

from zetryn_bot.runtime.llm import _CANDIDATES, try_build_llm_client

# Every env var name any candidate provider might read.
_ALL_LLM_ENVS = [name for c in _CANDIDATES for name in c.key_envs] + ["LLM_MODEL"]


def _clear_llm_env(monkeypatch):
    for name in _ALL_LLM_ENVS:
        monkeypatch.delenv(name, raising=False)


def test_returns_none_when_no_provider_key_set(monkeypatch):
    _clear_llm_env(monkeypatch)
    assert try_build_llm_client() is None


def test_builds_client_when_a_provider_key_is_present(monkeypatch):
    _clear_llm_env(monkeypatch)
    monkeypatch.setenv("GROQ_API_KEY", "test-key")
    client = try_build_llm_client()
    assert client is not None


def test_llm_model_override_is_applied(monkeypatch):
    _clear_llm_env(monkeypatch)
    monkeypatch.setenv("GROQ_API_KEY", "test-key")
    monkeypatch.setenv("LLM_MODEL", "custom-model-x")
    # First candidate is groq; its config.model should reflect the override.
    try_build_llm_client()
    groq = next(c for c in _CANDIDATES if c.name == "groq")
    assert groq.model == "custom-model-x"
