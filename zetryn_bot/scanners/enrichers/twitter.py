"""Twitter — social-signal enrichment for token candidates.

Source: Twitter (X) via :mod:`twitter_login` (cookie-based, no API key)
Auth: Per-account cookie files in a configurable directory
    (default: ``twitter_cookies/account_{i}.json``). Cookies are
    obtained via a one-time interactive login; the directory is
    gitignored — never commit cookies.
Mechanism: Per-mint search of recent (last 1h) tweets matching
    ``$SYMBOL``, ``SYMBOL`` (for longer symbols), and the first 12 chars
    of the mint address. Results aggregated, deduplicated, and scored
    for mention rate, growth, influencer count, engagement, velocity,
    and VADER sentiment with a crypto-tuned lexicon.
Rate limits: ``twitter_login`` raises HTTP 429 when an account exceeds
    its read quota; the :class:`TwitterAccountPool` cools the offending
    account for 15 minutes and rotates to the next. Auth errors cool the
    account for 1 hour.
Populates: twitter_mentions_1h, twitter_mention_growth_pct,
    twitter_influencer_count, twitter_top_influencer_handle,
    twitter_top_influencer_followers, twitter_sentiment,
    twitter_engagement, twitter_velocity_tpm.

NLTK's VADER analyzer is used because it's purpose-built for averaging
sentiment across short, noisy texts (which is exactly what crypto
Twitter is). The default lexicon is English-prose-tuned and scores
"going to moon 🚀" as 0.0 — useless for memecoin context — so we merge
a crypto-tuned lexicon at import time.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from datetime import UTC, datetime

import aiohttp
import nltk
from loguru import logger
from nltk.sentiment.vader import SentimentIntensityAnalyzer
from twitter_login import Client
from twitter_login.enums import SearchTimelineProduct
from twitter_login.errors import HTTPError
from twitter_login.headers import UserAgent

from zetryn_bot.models.token import TokenCandidate

_COOLDOWN_429 = 900  # 15 min
_COOLDOWN_AUTH = 3600  # 1 hour
_INFLUENCER_MIN_FOLLOWERS = 10_000

# Aggregate mean-compound threshold. VADER compound range is [-1, +1];
# ±0.2 is stricter than VADER's default ±0.05, which is appropriate for
# short-form Twitter where everyone is mildly positive by default.
_SENTIMENT_THRESHOLD = 0.2

# Ensure VADER lexicon is available. The first run downloads to
# ~/nltk_data; subsequent runs are no-ops.
try:
    nltk.data.find("sentiment/vader_lexicon.zip")
except LookupError:
    nltk.download("vader_lexicon", quiet=True)

# Crypto-tuned lexicon merged into VADER. Values are on VADER's [-4, +4]
# scale. The two-tier classification (strong vs. ordinary) preserves the
# averaging behaviour: a tweet with one strong bullish term outweighs a
# tweet with several mild ones.
_CRYPTO_LEXICON: dict[str, float] = {
    # Strong bullish (+3.5 to +4.0)
    "moonshot": 4.0,
    "10000x": 4.0,
    "100x": 3.5,
    "1000x": 3.5,
    "gem": 3.5,
    "hidden gem": 3.5,
    "alpha": 3.5,
    "wagmi": 3.5,
    "diamond hands": 3.5,
    "🚀🚀🚀": 4.0,
    "🚀": 3.0,
    "🌙": 2.5,
    "💎": 3.0,
    "🔥": 3.0,
    "💰": 2.5,
    "📈": 2.5,
    "🥇": 2.5,
    "⭐": 2.0,
    "💯": 2.5,
    "✨": 2.0,
    "💵": 2.5,
    "🤑": 2.5,
    # Bullish (+1.5 to +2.5)
    "moon": 2.5,
    "mooning": 2.5,
    "moonbound": 2.5,
    "bullish": 2.0,
    "bull": 2.0,
    "bullrun": 2.5,
    "ape": 2.0,
    "aping": 2.0,
    "aped": 1.5,
    "apes": 1.5,
    "fullport": 2.5,
    "pump": 2.0,
    "pumping": 2.5,
    "pumped": 1.5,
    "send": 1.5,
    "sending": 2.0,
    "sendit": 2.0,
    "sent": 1.5,
    "gm": 1.5,
    "gmgm": 2.0,
    "based": 2.0,
    "chad": 2.0,
    "giga": 2.0,
    "degen": 1.5,
    "degens": 1.5,
    "early": 2.0,
    "stealth": 2.0,
    "presale": 1.5,
    "fairlaunch": 2.0,
    "viral": 2.5,
    "trending": 2.0,
    "explosive": 2.5,
    "kol": 2.5,
    "smartmoney": 2.5,
    "smart money": 2.5,
    "whale": 2.0,
    "buy": 1.5,
    "buying": 1.5,
    "loaded": 2.0,
    "loading": 2.0,
    "stacking": 2.0,
    "accumulating": 2.0,
    "lfg": 3.0,
    "letsgo": 2.5,
    "lambo": 2.5,
    "winner": 2.0,
    "next gem": 3.0,
    "graduated": 2.0,
    "graduating": 2.0,
    "graduation": 2.0,
    "ath": 2.0,
    "all time high": 2.5,
    "new ath": 2.5,
    # Strong bearish (-3.5 to -4.0)
    "rugpull": -4.0,
    "rugged": -4.0,
    "honeypot": -4.0,
    "scam": -3.5,
    "scammed": -3.5,
    "scammer": -3.5,
    "rekt": -3.5,
    "liquidated": -3.5,
    "ngmi": -3.0,
    "deadcoin": -3.5,
    "❌": -3.0,
    "💀": -3.0,
    "🤡": -2.5,
    # Bearish (-1.5 to -3.0)
    "rug": -3.0,
    "rugging": -3.0,
    "dump": -2.5,
    "dumping": -2.5,
    "dumped": -2.0,
    "bearish": -2.0,
    "bear": -1.5,
    "avoid": -2.5,
    "stay away": -2.5,
    "do not buy": -3.0,
    "dnb": -2.5,
    "dead": -2.5,
    "dying": -2.5,
    "tank": -2.0,
    "tanking": -2.5,
    "beware": -2.5,
    "warning": -2.0,
    "caution": -1.5,
    "fud": -1.5,
    "fudding": -1.5,
    "paper hands": -1.5,
    "paperhands": -1.5,
    "sell": -1.5,
    "selling": -1.5,
    "sold": -1.0,
    "exit": -1.0,
    "drop": -1.5,
    "dropping": -1.5,
    "bundler": -2.5,
    "bundled": -2.5,
    "snipers": -1.5,
    "dev rug": -3.5,
    "dev sold": -3.0,
    "dev dump": -3.0,
    "low liquidity": -2.0,
    "illiquid": -2.0,
    "manipulated": -2.5,
    "wash trade": -2.5,
    "fake volume": -3.0,
}

_VADER = SentimentIntensityAnalyzer()
_VADER.lexicon.update(_CRYPTO_LEXICON)

# Chrome 131 — matches the curl-cffi impersonate target used by
# twitter_login internally.
_USER_AGENT = UserAgent(
    ch_ua='"Google Chrome";v="131", "Chromium";v="131", "Not_A Brand";v="24"',
    ch_ua_mobile="?0",
    ch_ua_platform='"Linux"',
    user_agent=(
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
)
_IMPERSONATE = "chrome131"


class TwitterAccountPool:
    """Pool of :class:`twitter_login.Client` instances, one per cookie file.

    Rotates on rate-limit error (15-minute cooldown). Auth errors cool the
    account for one hour. Cooled accounts are skipped silently; if every
    account is cooled the pool returns ``(None, None)`` and the caller
    must skip its enrichment.
    """

    def __init__(self, cookies_dir: str = "twitter_cookies") -> None:
        self._cookies_dir = cookies_dir
        self._clients: list[Client] = []
        self._index = 0
        self._cooldown_until: list[float] = []
        self._lock = asyncio.Lock()
        self._log = logger.bind(component="twitter.pool")

    async def initialize(self) -> None:
        """Load every ``account_*.json`` cookie file from the configured dir."""
        if not os.path.isdir(self._cookies_dir):
            raise RuntimeError(f"Twitter cookies dir not found: {self._cookies_dir}")
        files = sorted(
            f
            for f in os.listdir(self._cookies_dir)
            if f.startswith("account_") and f.endswith(".json")
        )
        if not files:
            raise RuntimeError(f"No account_*.json files found in {self._cookies_dir}")

        for fname in files:
            path = os.path.join(self._cookies_dir, fname)
            try:
                cookies = _load_cookies(path)
                client = Client(user_agent=_USER_AGENT, impersonate=_IMPERSONATE)
                # validate_cookies=False so a stale auth_token doesn't blow
                # up startup; the runtime call surfaces auth errors when
                # they actually matter.
                client.load_cookies(cookies, validate_cookies=False)
                self._clients.append(client)
                self._log.info(f"loaded {fname} ({len(cookies)} cookies)")
            except Exception as exc:
                self._log.warning(f"failed to load {fname}: {exc} — skipping")

        if not self._clients:
            raise RuntimeError("TwitterAccountPool: no accounts loaded")

        self._cooldown_until = [0.0] * len(self._clients)
        self._log.info(f"pool ready — {len(self._clients)} account(s)")

    async def acquire(self) -> tuple[Client, int] | tuple[None, None]:
        async with self._lock:
            now = time.monotonic()
            for _ in range(len(self._clients)):
                idx = self._index
                self._index = (self._index + 1) % len(self._clients)
                if now < self._cooldown_until[idx]:
                    continue
                return self._clients[idx], idx
            self._log.warning("all accounts in cooldown")
            return None, None

    async def mark_rate_limited(self, idx: int, cooldown: int = _COOLDOWN_429) -> None:
        async with self._lock:
            self._cooldown_until[idx] = time.monotonic() + cooldown
            self._log.warning(f"account {idx} rate limited — cooldown {cooldown}s")

    async def mark_auth_error(self, idx: int) -> None:
        async with self._lock:
            self._cooldown_until[idx] = time.monotonic() + _COOLDOWN_AUTH
            self._log.warning(f"account {idx} auth error — cooldown {_COOLDOWN_AUTH}s")


class TwitterEnricher:
    """On-demand Twitter social-signal enricher.

    Requires a pre-initialized :class:`TwitterAccountPool`. Each
    :meth:`enrich` call performs up to three Twitter searches per mint
    (``$SYMBOL``, ``SYMBOL`` for longer names, ``address[:12]``),
    deduplicates results, and aggregates the metrics into the candidate.

    The ``session`` parameter is ignored — :mod:`twitter_login` manages
    its own HTTP transport via :mod:`curl_cffi`. The arg is kept only
    to satisfy the :class:`TokenEnricher` Protocol.
    """

    name = "twitter"

    def __init__(self, pool: TwitterAccountPool) -> None:
        self._pool = pool
        self._log = logger.bind(component=self.name)

    async def enrich(
        self,
        mint: str,
        candidate: TokenCandidate,
        session: aiohttp.ClientSession,
    ) -> TokenCandidate:
        social = await _fetch_twitter_social(self._pool, candidate.symbol, mint)
        if not social or social.get("mentions_1h", 0) == 0:
            return candidate

        sources = list(candidate.sources)
        if "twitter" not in sources:
            sources.append("twitter")

        return candidate.model_copy(
            update={
                "twitter_mentions_1h": social["mentions_1h"],
                "twitter_mention_growth_pct": social["mention_growth_pct"],
                "twitter_influencer_count": social["influencer_count"],
                "twitter_top_influencer_handle": social["top_influencer_handle"],
                "twitter_top_influencer_followers": social["top_influencer_followers"],
                "twitter_sentiment": social["sentiment"],
                "twitter_engagement": social["engagement"],
                "twitter_velocity_tpm": social["velocity_tpm"],
                "sources": sources,
            }
        )


def build_twitter_pool_from_config(settings) -> TwitterAccountPool | None:
    """Build a :class:`TwitterAccountPool` from a Settings object.

    Reads ``settings.twitter_cookies_path`` (default ``twitter_cookies``).
    Returns ``None`` when the directory doesn't exist or has no
    ``account_*.json`` files. The caller is responsible for awaiting
    :meth:`TwitterAccountPool.initialize` before using the pool.
    """
    cookies_dir = getattr(settings, "twitter_cookies_path", "twitter_cookies")
    if not os.path.isdir(cookies_dir):
        return None
    files = [f for f in os.listdir(cookies_dir) if f.startswith("account_") and f.endswith(".json")]
    if not files:
        return None
    return TwitterAccountPool(cookies_dir=cookies_dir)


# ──────────────────────────────────────────────────────────────────────────
# Internal helpers (fetch + process + lexicon)
# ──────────────────────────────────────────────────────────────────────────


def _load_cookies(path: str) -> dict:
    with open(path) as f:
        data = json.load(f)
    if isinstance(data, list):
        return {item["name"]: item["value"] for item in data if "name" in item and "value" in item}
    return data


def _build_queries(symbol: str, address: str) -> list[str]:
    """Build prioritised query variations for a token search.

    - ``$SYMBOL`` — standard ticker style (most common)
    - ``SYMBOL``  — plain name; only useful for symbols ≥ 6 chars to
      avoid noise on common short tickers (SOL, PEPE, etc.)
    - ``address[:12]`` — contract address prefix, used by alpha bots
      and KOLs who paste contracts inline
    """
    queries: list[str] = []
    if symbol and len(symbol) >= 2:
        queries.append(f"${symbol}")
        if len(symbol) >= 6:
            queries.append(symbol)
    if address:
        queries.append(address[:12])
    return queries


async def _fetch_twitter_social(pool: TwitterAccountPool, symbol: str, address: str) -> dict | None:
    """Fetch Twitter social signals for a token.

    Tries multiple query variations and aggregates unique tweets across
    them. Returns a metrics dict computed by :func:`_process_tweets`, or
    :func:`_empty_social` if no tweets were found or every account was
    cooled.
    """
    log = logger.bind(component="twitter.fetch")
    queries = _build_queries(symbol, address)
    if not queries:
        return _empty_social()

    seen_tweet_ids: set = set()
    all_tweets: list = []
    last_query_for_log = queries[0]

    for query in queries:
        client, idx = await pool.acquire()
        if client is None:
            break  # All accounts cooled — return whatever we have.
        try:
            count = 40 if len(queries) == 1 else 25
            result = await client.search(query, product=SearchTimelineProduct.LIVE, count=count)
            for tweet in result:
                tid = getattr(tweet, "id", None) or getattr(tweet, "rest_id", None)
                if tid is not None:
                    if tid in seen_tweet_ids:
                        continue
                    seen_tweet_ids.add(tid)
                all_tweets.append(tweet)
            last_query_for_log = query
        except HTTPError as exc:
            status = getattr(exc, "status", 0)
            if status == 429:
                await pool.mark_rate_limited(idx)
                break
            if status in (401, 403):
                await pool.mark_auth_error(idx)
                break
            log.debug(f"HTTP {status} for {query}: {exc}")
            continue
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.debug(f"fetch error for {query}: {type(exc).__name__}: {exc}")
            continue

    if not all_tweets:
        return _empty_social()
    return _process_tweets(all_tweets, last_query_for_log)


def _process_tweets(tweets, query: str) -> dict:
    """Aggregate raw tweet list into the metrics dict applied to TokenCandidate."""
    log = logger.bind(component="twitter.process")
    now_ts = datetime.now(UTC).timestamp()
    one_hour_ago = now_ts - 3600
    two_hours_ago = now_ts - 7200
    thirty_min_ago = now_ts - 1800

    mentions_1h = []
    mentions_prev_1h = []
    for tweet in tweets:
        ts = _parse_tweet_ts(getattr(tweet, "created_at", None))
        if ts is None:
            continue
        if ts > one_hour_ago:
            mentions_1h.append(tweet)
        elif ts > two_hours_ago:
            mentions_prev_1h.append(tweet)

    count_1h = len(mentions_1h)
    count_prev = len(mentions_prev_1h)
    growth_pct = ((count_1h - count_prev) / max(count_prev, 1)) * 100

    influencer_count = 0
    top_influencer_handle = ""
    top_influencer_followers = 0
    for tw in mentions_1h:
        user = getattr(tw, "user", None)
        if user is None:
            continue
        followers = getattr(user, "followers_count", 0) or 0
        if followers >= _INFLUENCER_MIN_FOLLOWERS:
            influencer_count += 1
        if followers > top_influencer_followers:
            top_influencer_followers = followers
            top_influencer_handle = (
                getattr(user, "screen_name", "") or getattr(user, "name", "") or ""
            )

    engagement = sum(
        (getattr(t, "favorite_count", 0) or 0)
        + (getattr(t, "retweet_count", 0) or 0)
        + (getattr(t, "reply_count", 0) or 0)
        + (getattr(t, "quote_count", 0) or 0)
        for t in mentions_1h
    )

    recent_30m = [
        t
        for t in mentions_1h
        if (_parse_tweet_ts(getattr(t, "created_at", None)) or 0) > thirty_min_ago
    ]
    velocity_tpm = round(len(recent_30m) / 30, 2)

    if mentions_1h:
        compounds = [
            _VADER.polarity_scores(getattr(t, "text", "") or "")["compound"] for t in mentions_1h
        ]
        mean_compound = sum(compounds) / len(compounds)
    else:
        mean_compound = 0.0
    if mean_compound >= _SENTIMENT_THRESHOLD:
        sentiment = "bullish"
    elif mean_compound <= -_SENTIMENT_THRESHOLD:
        sentiment = "bearish"
    else:
        sentiment = "neutral"

    result = {
        "mentions_1h": count_1h,
        "mention_growth_pct": round(growth_pct, 1),
        "influencer_count": influencer_count,
        "top_influencer_handle": top_influencer_handle,
        "top_influencer_followers": top_influencer_followers,
        "sentiment": sentiment,
        "engagement": engagement,
        "velocity_tpm": velocity_tpm,
    }
    log.debug(
        f"[{query}] {count_1h} mentions/h (+{growth_pct:.0f}%) "
        f"| sentiment={sentiment} | engagement={engagement} "
        f"| vel={velocity_tpm}tpm"
    )
    return result


def _parse_tweet_ts(created_at: str | None) -> float | None:
    if not created_at:
        return None
    try:
        dt = datetime.strptime(created_at, "%a %b %d %H:%M:%S %z %Y")
        return dt.timestamp()
    except (ValueError, TypeError):
        return None


def _empty_social() -> dict:
    return {
        "mentions_1h": 0,
        "mention_growth_pct": 0.0,
        "influencer_count": 0,
        "top_influencer_handle": "",
        "top_influencer_followers": 0,
        "sentiment": "",
        "engagement": 0,
        "velocity_tpm": 0.0,
    }


__all__ = [
    "TwitterAccountPool",
    "TwitterEnricher",
    "build_twitter_pool_from_config",
]
