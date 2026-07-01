"""Unit tests for the config → instance registry."""

from __future__ import annotations

from zetryn_bot.config import Settings
from zetryn_bot.runtime.registry import build_enabled_scanners, build_enrichers

_ZERO_ARG_NAMES = {
    "dexscreener.new_pairs",
    "dexscreener.trending",
    "dexscreener.boost",
    "geckoterminal.new_pools",
    "geckoterminal.trending",
    "raydium.new_pools",
}


def _settings(**overrides) -> Settings:
    # _env_file=None so a developer's real .env doesn't leak into the test.
    return Settings(_env_file=None, **overrides)


def test_no_keys_yields_only_zero_arg_scanners():
    scanners = build_enabled_scanners(_settings())
    assert {s.name for s in scanners} == _ZERO_ARG_NAMES


def test_birdeye_keys_add_two_birdeye_scanners():
    scanners = build_enabled_scanners(_settings(birdeye_api_keys=["k1", "k2"]))
    names = {s.name for s in scanners}
    assert "birdeye.trending" in names
    assert "birdeye.new_listing" in names


def test_pumpportal_key_adds_pumpfun_scanner():
    scanners = build_enabled_scanners(_settings(pumpportal_api_key="pp-key"))
    assert "pumpfun.ws" in {s.name for s in scanners}


def test_scanners_enabled_filters_by_name():
    scanners = build_enabled_scanners(
        _settings(scanners_enabled=["dexscreener.boost", "raydium.new_pools"])
    )
    assert {s.name for s in scanners} == {"dexscreener.boost", "raydium.new_pools"}


def test_enrichers_without_keys_are_rugcheck_and_jupiter_in_order():
    enrichers = build_enrichers(_settings())
    assert [e.name for e in enrichers] == ["rugcheck", "jupiter"]


def test_enrichers_with_keys_include_helius_first_and_gmgn():
    enrichers = build_enrichers(_settings(helius_api_keys=["h1"], gmgn_api_key="g1"))
    names = [e.name for e in enrichers]
    assert names == ["helius", "rugcheck", "gmgn", "jupiter"]
