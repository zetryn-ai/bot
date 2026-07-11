"""Unit tests for the LLM failover-router wiring."""

from __future__ import annotations

from zetryn.llm import LLMRouter

from zetryn_bot.runtime.llm import _CHAIN, try_build_llm_client

# Every env var name any chain entry might read.
_ALL_LLM_ENVS = sorted({name for entry in _CHAIN for name in entry[4]} | {"LLM_MODEL"})


def _clear_llm_env(monkeypatch):
    for name in _ALL_LLM_ENVS:
        monkeypatch.delenv(name, raising=False)


def test_returns_none_when_no_provider_key_set(monkeypatch):
    _clear_llm_env(monkeypatch)
    assert try_build_llm_client() is None


def test_single_provider_returns_router_over_its_models(monkeypatch):
    _clear_llm_env(monkeypatch)
    monkeypatch.setenv("GROQ_API_KEY", "k1,k2,k3")
    client = try_build_llm_client()
    # Groq appears twice in the chain (two model buckets) -> router.
    assert isinstance(client, LLMRouter)
    assert [e.name for e in client.entries] == ["groq/llama-3.3-70b", "groq/llama-3.1-8b"]


def test_all_providers_build_full_chain(monkeypatch):
    _clear_llm_env(monkeypatch)
    monkeypatch.setenv("GROQ_API_KEY", "g1")
    monkeypatch.setenv("OPENROUTER_API_KEY", "o1")
    monkeypatch.setenv("GEMINI_API_KEY", "m1")
    client = try_build_llm_client()
    assert isinstance(client, LLMRouter)
    assert len(client.entries) == 4  # groq x2 models + openrouter + gemini


def test_pool_limit_scales_with_key_count(monkeypatch):
    _clear_llm_env(monkeypatch)
    monkeypatch.setenv("GROQ_API_KEY", ",".join(f"k{i}" for i in range(10)))
    client = try_build_llm_client()
    assert isinstance(client, LLMRouter)
    primary = client.entries[0]
    # llama-3.3-70b free tier is rpm=30/key -> 300 for 10 keys.
    assert primary.limit is not None and primary.limit.rpm == 300


def test_llm_model_override_applies_to_primary_only(monkeypatch):
    _clear_llm_env(monkeypatch)
    monkeypatch.setenv("GROQ_API_KEY", "k1")
    monkeypatch.setenv("LLM_MODEL", "custom-model-x")
    client = try_build_llm_client()
    assert isinstance(client, LLMRouter)
    # Primary rides the override; the fallback keeps its own model.
    assert client.entries[0].name == "groq/llama-3.3-70b"
    assert "custom-model-x" not in client.entries[1].name
