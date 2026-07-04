"""Regression tests for Settings parsing quirks."""

from __future__ import annotations

import os
from unittest import mock

from zetryn_bot.config import Settings


def test_blank_live_priority_fee_env_becomes_none():
    # A blank LIVE_PRIORITY_FEE_LAMPORTS= in .env arrives as "" — it must become
    # None ("Jupiter auto"), not crash int parsing.
    with mock.patch.dict(os.environ, {"LIVE_PRIORITY_FEE_LAMPORTS": ""}, clear=False):
        s = Settings(_env_file=None)
    assert s.live_priority_fee_lamports is None


def test_numeric_live_priority_fee_env_is_parsed():
    with mock.patch.dict(os.environ, {"LIVE_PRIORITY_FEE_LAMPORTS": "50000"}, clear=False):
        s = Settings(_env_file=None)
    assert s.live_priority_fee_lamports == 50000


def test_execution_mode_defaults_to_paper():
    assert Settings(_env_file=None).execution_mode == "paper"
