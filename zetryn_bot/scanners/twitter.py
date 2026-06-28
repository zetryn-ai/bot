from __future__ import annotations

import asyncio
import json
import os
import time
from datetime import datetime, timezone

from loguru import logger
from twitter_login import Client
from twitter_login.headers import UserAgent
from twitter_login.enums import SearchTimelineProduct
from twitter_login.errors import HTTPError

log = logger.bind(component="scanner.twitter")

_COOLDOWN_429 = 900   # 15 min
_COOLDOWN_AUTH = 3600 # 1 hour

_INFLUENCER_MIN_FOLLOWERS = 10_000

# VADER sentiment analyzer + crypto-lexicon extension. VADER's default dictionary
# is English-prose-tuned and scores "going to moon 🚀" as 0.0 — useless for our
# context. We merge the same bullish/bearish vocab the old keyword counter used,
# weighted on VADER's [-4, +4] scale, so the analyzer recognises crypto slang.
# Lexicon update is in-place and one-time per process.
import nltk
from nltk.sentiment.vader import SentimentIntensityAnalyzer
try:
    nltk.data.find("sentiment/vader_lexicon.zip")
except LookupError:
    # Fresh install — fetch lexicon (~120KB, cached to ~/nltk_data afterwards).
    nltk.download("vader_lexicon", quiet=True)

_CRYPTO_LEXICON = {
    # ── Strong bullish (+3.5 to +4.0) — high-conviction signals ─────────────
    "moonshot": 4.0, "10000x": 4.0, "100x": 3.5, "1000x": 3.5,
    "gem": 3.5, "hidden gem": 3.5, "alpha": 3.5, "wagmi": 3.5,
    "diamond hands": 3.5, "🚀🚀🚀": 4.0,
    # Emojis (strong)
    "🚀": 3.0, "🌙": 2.5, "💎": 3.0, "🔥": 3.0, "💰": 2.5, "📈": 2.5,
    "🥇": 2.5, "⭐": 2.0, "💯": 2.5, "✨": 2.0, "💵": 2.5, "🤑": 2.5,
    # ── Bullish (+1.5 to +2.5) — common bullish slang ──────────────────────
    "moon": 2.5, "mooning": 2.5, "moonbound": 2.5,
    "bullish": 2.0, "bull": 2.0, "bullrun": 2.5,
    "ape": 2.0, "aping": 2.0, "aped": 1.5, "apes": 1.5, "fullport": 2.5,
    "pump": 2.0, "pumping": 2.5, "pumped": 1.5,
    "send": 1.5, "sending": 2.0, "sendit": 2.0, "sent": 1.5,
    "gm": 1.5, "gmgm": 2.0, "based": 2.0, "chad": 2.0, "giga": 2.0,
    "degen": 1.5, "degens": 1.5, "playing": 1.5,
    "early": 2.0, "stealth": 2.0, "presale": 1.5, "fairlaunch": 2.0,
    "viral": 2.5, "trending": 2.0, "explosive": 2.5,
    "kol": 2.5, "smartmoney": 2.5, "smart money": 2.5, "whale": 2.0,
    "buy": 1.5, "buying": 1.5, "loaded": 2.0, "loading": 2.0,
    "bagholder": 1.0, "bag": 1.0, "stacking": 2.0, "accumulating": 2.0,
    "lfg": 3.0, "letsgo": 2.5, "lets fucking go": 3.0, "lambo": 2.5,
    "topblast": 2.0, "winner": 2.0, "next": 1.5, "next gem": 3.0,
    "graduated": 2.0, "graduating": 2.0, "graduation": 2.0,  # pump.fun graduations
    "ath": 2.0, "all time high": 2.5, "new ath": 2.5,
    "kek": 1.5, "based dev": 2.5,
    # ── Strong bearish (-3.5 to -4.0) — exit signals ───────────────────────
    "rugpull": -4.0, "rugged": -4.0, "honeypot": -4.0,
    "scam": -3.5, "scammed": -3.5, "scammer": -3.5,
    "rekt": -3.5, "liquidated": -3.5,
    "ngmi": -3.0, "ded": -3.0, "deadcoin": -3.5,
    "❌": -3.0, "💀": -3.0, "🤡": -2.5,
    # ── Bearish (-1.5 to -2.5) — caution signals ───────────────────────────
    "rug": -3.0, "rugging": -3.0,
    "dump": -2.5, "dumping": -2.5, "dumped": -2.0,
    "bearish": -2.0, "bear": -1.5,
    "avoid": -2.5, "stay away": -2.5, "do not buy": -3.0, "dnb": -2.5,
    "dead": -2.5, "dying": -2.5, "tank": -2.0, "tanking": -2.5, "tanked": -2.0,
    "beware": -2.5, "warning": -2.0, "careful": -1.5, "caution": -1.5,
    "fud": -1.5, "fudding": -1.5,
    "paper hands": -1.5, "paperhands": -1.5, "weak hands": -1.5,
    "sell": -1.5, "selling": -1.5, "sold": -1.0, "exit": -1.0,
    "down": -1.5, "drop": -1.5, "dropping": -1.5,
    "bundler": -2.5, "bundled": -2.5, "snipers": -1.5, "sniper crowd": -2.0,
    "dev rug": -3.5, "dev sold": -3.0, "dev dump": -3.0,
    "low liquidity": -2.0, "thin": -1.5, "illiquid": -2.0,
    "manipulated": -2.5, "wash trade": -2.5, "fake volume": -3.0,
}

_VADER = SentimentIntensityAnalyzer()
_VADER.lexicon.update(_CRYPTO_LEXICON)

# Aggregate mean-compound threshold. Compound range is [-1, +1]; ±0.2 is
# stricter than VADER's default ±0.05 — filters hype-text noise from
# short-form Twitter where everyone is mildly positive by default.
_SENTIMENT_THRESHOLD = 0.2

# Chrome 131 — matches curl-cffi impersonate target
_USER_AGENT = UserAgent(
    ch_ua='"Google Chrome";v="131", "Chromium";v="131", "Not_A Brand";v="24"',
    ch_ua_mobile="?0",
    ch_ua_platform='"Linux"',
    user_agent="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
)
_IMPERSONATE = "chrome131"


def _load_cookies(path: str) -> dict:
    with open(path) as f:
        data = json.load(f)
    if isinstance(data, list):
        return {item["name"]: item["value"] for item in data if "name" in item and "value" in item}
    return data


class TwitterAccountPool:
    """
    Pool of twitter_login Client instances, one per account cookie file.
    Rotates on rate limit (cooldown 15 min).
    """

    def __init__(self, cookies_dir: str = "twitter_cookies") -> None:
        self._cookies_dir = cookies_dir
        self._clients: list[Client] = []
        self._index = 0
        self._cooldown_until: list[float] = []
        self._lock = asyncio.Lock()

    async def initialize(self) -> None:
        """Load all account_{i}.json cookie files from cookies_dir."""
        if not os.path.isdir(self._cookies_dir):
            raise RuntimeError(f"Twitter cookies dir not found: {self._cookies_dir}")

        files = sorted(
            f for f in os.listdir(self._cookies_dir)
            if f.startswith("account_") and f.endswith(".json")
        )
        if not files:
            raise RuntimeError(f"No account_*.json files found in {self._cookies_dir}")

        for fname in files:
            path = os.path.join(self._cookies_dir, fname)
            try:
                cookies = _load_cookies(path)
                client = Client(user_agent=_USER_AGENT, impersonate=_IMPERSONATE)
                # twitter_login API: load_cookies accepts dict | str-path | Path; sync call.
                # validate_cookies=False so a stale auth_token doesn't blow up startup —
                # the runtime call will surface the auth error if/when it actually matters.
                client.load_cookies(cookies, validate_cookies=False)
                self._clients.append(client)
                log.info(f"Twitter account loaded: {fname} ({len(cookies)} cookies)")
            except Exception as e:
                log.warning(f"Twitter account {fname} failed to load: {e} — skipping")

        if not self._clients:
            raise RuntimeError("TwitterAccountPool: no accounts loaded successfully")

        self._cooldown_until = [0.0] * len(self._clients)
        log.info(f"Twitter pool ready — {len(self._clients)} account(s)")

    async def acquire(self) -> tuple[Client, int] | tuple[None, None]:
        async with self._lock:
            now = time.monotonic()
            for _ in range(len(self._clients)):
                idx = self._index
                self._index = (self._index + 1) % len(self._clients)
                if now < self._cooldown_until[idx]:
                    remaining = self._cooldown_until[idx] - now
                    log.debug(f"Twitter account {idx} in cooldown for {remaining:.0f}s")
                    continue
                return self._clients[idx], idx
            log.error("All Twitter accounts in cooldown — skipping enrichment")
            return None, None

    async def mark_rate_limited(self, idx: int, cooldown: int = _COOLDOWN_429) -> None:
        async with self._lock:
            self._cooldown_until[idx] = time.monotonic() + cooldown
            log.error(f"Twitter account {idx} rate limited — cooldown {cooldown}s")

    async def mark_auth_error(self, idx: int) -> None:
        async with self._lock:
            self._cooldown_until[idx] = time.monotonic() + _COOLDOWN_AUTH
            log.error(f"Twitter account {idx} auth error — cooldown {_COOLDOWN_AUTH}s")

    def stats(self) -> dict:
        now = time.monotonic()
        return {
            i: {"cooldown_remaining": max(0, self._cooldown_until[i] - now)}
            for i in range(len(self._clients))
        }


def _build_queries(symbol: str, address: str) -> list[str]:
    """
    Build prioritized query variations for a token. Most likely match first.

    Memecoin Twitter shilling commonly uses:
    - "$SYMBOL" — standard ticker style (most common)
    - "SYMBOL" — plain name (occasional, only useful if symbol >= 4 chars to avoid noise)
    - address[:12] — contract address prefix (used by alpha bots)

    Generic short symbols (BONK, SOL, PEPE) return huge noise without $ prefix —
    we skip plain-name search for them. Address always included as fallback
    (alpha bots + KOLs frequently paste contract).
    """
    queries: list[str] = []
    if symbol and len(symbol) >= 2:
        queries.append(f"${symbol}")
        # Plain symbol only useful for longer, distinctive names (avoid noise on short tickers)
        if len(symbol) >= 6:
            queries.append(symbol)
    if address:
        queries.append(address[:12])
    return queries


async def fetch_twitter_social(
    pool: TwitterAccountPool,
    symbol: str,
    address: str,
) -> dict | None:
    """
    Fetch Twitter social signals for a token using twitter_login (curl-cffi based).
    Tries multiple query variations ($SYMBOL, plain name, address[:12]) and
    aggregates unique tweets. Returns metrics for last 1 hour, or None if pool unavailable.
    """
    queries = _build_queries(symbol, address)
    if not queries:
        return _empty_social()

    seen_tweet_ids: set[str] = set()
    all_tweets: list = []
    last_query_for_log = queries[0]

    for query in queries:
        client, idx = await pool.acquire()
        if client is None:
            break  # all accounts in cooldown — return whatever we have

        try:
            # Lower per-query count since we may run multiple — total budget stays similar
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
        except HTTPError as e:
            status = getattr(e, "status", 0)
            if status == 429:
                await pool.mark_rate_limited(idx)
                break  # don't burn other queries while rate limited
            elif status in (401, 403):
                await pool.mark_auth_error(idx)
                break
            else:
                log.debug(f"Twitter HTTP {status} for {query}: {e}")
                continue  # try next query variation
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log.debug(f"Twitter fetch error for {query}: {type(e).__name__}: {e}")
            continue

    if not all_tweets:
        log.debug(f"Twitter: no results across {len(queries)} query variation(s) for {symbol or address[:8]}")
        return _empty_social()

    log.debug(
        f"Twitter: {len(all_tweets)} unique tweets across {len(queries)} queries "
        f"for {symbol or address[:8]}"
    )
    return _process_tweets(all_tweets, last_query_for_log)


def _process_tweets(tweets, query: str) -> dict:
    now_ts = datetime.now(timezone.utc).timestamp()
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

    # Influencer detection — Twikit Tweet exposes the author's User object as `.user`,
    # which carries followers_count. Anyone with ≥10k followers counts as an "influencer";
    # we also track the single highest-follower account that mentioned the token in the
    # last 1h (used by the scorer's TWITTER SOCIAL boost rule).
    _INFLUENCER_FOLLOWERS_MIN = 10_000
    influencer_count = 0
    top_influencer_handle = ""
    top_influencer_followers = 0
    for tw in mentions_1h:
        user = getattr(tw, "user", None)
        if user is None:
            continue
        followers = getattr(user, "followers_count", 0) or 0
        if followers >= _INFLUENCER_FOLLOWERS_MIN:
            influencer_count += 1
        if followers > top_influencer_followers:
            top_influencer_followers = followers
            top_influencer_handle = getattr(user, "screen_name", "") or getattr(user, "name", "") or ""

    # Engagement
    engagement = sum(
        (getattr(t, "favorite_count", 0) or 0)
        + (getattr(t, "retweet_count", 0) or 0)
        + (getattr(t, "reply_count", 0) or 0)
        + (getattr(t, "quote_count", 0) or 0)
        for t in mentions_1h
    )

    # Velocity: tweets per minute in last 30 min
    recent_30m = [
        t for t in mentions_1h
        if (_parse_tweet_ts(getattr(t, "created_at", None)) or 0) > thirty_min_ago
    ]
    velocity_tpm = round(len(recent_30m) / 30, 2)

    # Sentiment via VADER aggregate. Compound is purpose-built for averaging across
    # short texts; the mean across all 1h mentions reflects overall conviction better
    # than a per-tweet majority vote.
    if mentions_1h:
        compounds = [
            _VADER.polarity_scores(getattr(t, "text", "") or "")["compound"]
            for t in mentions_1h
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
        f"Twitter [{query}]: {count_1h} mentions/h (+{growth_pct:.0f}%) "
        f"| sentiment={sentiment} | engagement={engagement} | vel={velocity_tpm}tpm"
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


def build_twitter_pool_from_config(settings) -> TwitterAccountPool | None:
    """
    Build TwitterAccountPool from cookies dir.
    Looks for engine/twitter_cookies/account_{i}.json files.
    No credentials needed — pure cookie-based auth.
    """
    cookies_dir = getattr(settings, "twitter_cookies_dir", "twitter_cookies")
    if not os.path.isdir(cookies_dir):
        return None
    files = [f for f in os.listdir(cookies_dir) if f.startswith("account_") and f.endswith(".json")]
    if not files:
        return None
    return TwitterAccountPool(cookies_dir=cookies_dir)
