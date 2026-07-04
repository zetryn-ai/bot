"""Compose M1 ``TokenEnricher`` implementations over one candidate.

Kept separate from the adapter (``adapters/token_input.py``) per the M2
design doc: the adapter is a pure function and never touches the network;
enrichment is where the I/O happens.
"""

from __future__ import annotations

import aiohttp
from loguru import logger

from zetryn_bot.models.token import TokenCandidate
from zetryn_bot.scanners.protocol import TokenEnricher

# Bind a component so error records survive setup_logger's component filter.
log = logger.bind(component="pipeline.enrich")


async def enrich_candidate(
    candidate: TokenCandidate,
    enrichers: list[TokenEnricher],
    session: aiohttp.ClientSession,
) -> TokenCandidate:
    """Run ``candidate`` through each enricher in order, accumulating fields.

    Each ``TokenEnricher.enrich()`` already treats transient errors as
    "log and return unchanged" per its Protocol contract. If an enricher
    raises anyway (persistent/config errors), that single enricher is
    skipped so one bad source doesn't block the others — the candidate
    carries forward whatever earlier enrichers already filled in.
    """
    for enricher in enrichers:
        try:
            candidate = await enricher.enrich(candidate.address, candidate, session)
        except Exception:
            log.exception("enricher {} raised for {}; skipping", enricher.name, candidate.address)
    return candidate
