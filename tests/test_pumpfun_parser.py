"""PumpPortal WS event parsing — txType discrimination.

Payloads verified live 2026-07-12: create events carry txType="create" AND a
``pool`` field ("pump"), so shape-sniffing on ``pool`` misroutes every launch
into the migration parser (the pre-v0.10.2 bug that starved the sniper route
and flooded graduation with age-86400 rejects).
"""

from zetryn_bot.scanners.pumpfun import _parse_event

_CREATE = {
    "signature": "sig",
    "mint": "CDD1HWtrN5bJeXGap7jXxxmU1bpQ8x7cxPmKALE4pump",
    "traderPublicKey": "AptgVT27LJUj7rn43EvQfZ82HkkBuhtynqtKb6ES48av",
    "txType": "create",
    "initialBuy": 354710743.77,
    "solAmount": 14.81,
    "bondingCurveKey": "C59Xnf4GiF59V2jBaWN97ieRpBHbLp5gQP3wCY17m4BG",
    "vTokensInBondingCurve": 718289256.22,
    "vSolInBondingCurve": 44.81,
    "marketCapSol": 62.39,
    "name": "Just a Bread",
    "symbol": "BREAD",
    "pool": "pump",  # present on CREATE events too — the old discriminator trap
}

_MIGRATE = {
    "signature": "sig2",
    "mint": "9c6MoxPW9RQoVkGrknD9HUhGuAN2qcEwmQzQ42CQpump",
    "txType": "migrate",
    "pool": "pumpswap",
}


def test_create_event_is_fresh_pumpfun_ws_candidate():
    cand = _parse_event(_CREATE)
    assert cand is not None
    assert cand.sources == ["pumpfun_ws"]
    assert cand.age_seconds == 0  # sniper rule needs age<=SNIPER_MAX_AGE_S
    assert cand.symbol == "BREAD"


def test_migrate_event_is_migration_candidate():
    cand = _parse_event(_MIGRATE)
    assert cand is not None
    assert cand.sources == ["pumpfun_migration"]
    assert cand.age_seconds >= 86400  # token age floor, NOT pair age


def test_no_txtype_falls_back_on_bonding_curve_shape():
    create_no_txtype = {k: v for k, v in _CREATE.items() if k != "txType"}
    cand = _parse_event(create_no_txtype)
    assert cand is not None
    assert cand.sources == ["pumpfun_ws"]

    migrate_no_txtype = {k: v for k, v in _MIGRATE.items() if k != "txType"}
    cand = _parse_event(migrate_no_txtype)
    assert cand is not None
    assert cand.sources == ["pumpfun_migration"]


def test_subscription_ack_is_ignored():
    assert _parse_event({"message": "Successfully subscribed to token creation events."}) is None
