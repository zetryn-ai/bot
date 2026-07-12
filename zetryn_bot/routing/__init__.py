"""Entry routing (M10b) — dispatch candidates to specialized framework agents.

One ``RoutedPipeline`` sits where the single generalist ``BotPipeline`` used
to: first-match characteristic rules pick a route (sniper / graduation /
scanner fallback), each route owns its own pipeline + framework agent, and
every route's ``Decision`` flows into the SAME shared sink — risk policy
(circuit breaker, max positions, cooldown, blocked sources) stays global.
"""

from __future__ import annotations
