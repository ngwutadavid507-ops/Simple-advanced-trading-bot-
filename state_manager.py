"""
Persistent state via Redis (same Upstash-style convention as Phoenix's phoenix-bot-2 instance).

State tracked:
- day_start_capital / current trading day marker (resets daily counters)
- trades_today, consecutive_losses
- last_trade_close_time, last_trade_was_win (for cooldown)
- open_position (so a restart doesn't lose track of a live trade) - now includes
  which symbol/pair is currently open, since the bot scans multiple pairs
- day_wins, day_losses, day_pnl, summary_sent_for_day (for the daily Telegram summary)
- evaluation_log (every signal decision, taken or rejected - survives restarts,
  unlike Render's ephemeral filesystem which wipes local files on every redeploy)
- cached_top_pairs (the top-volume pairs list, refreshed periodically rather than
  re-fetched every single cycle - reduces API calls significantly)

All keys are namespaced under config.REDIS_KEY_PREFIX to avoid collisions if this
Redis instance is ever shared with another bot.
"""

import json
import time
from datetime import datetime, timezone

import redis
import config

_client = None


def get_client():
    global _client
    if _client is None:
        _client = redis.from_url(config.REDIS_URL, decode_responses=True)
    return _client


def _key(name: str) -> str:
    return f"{config.REDIS_KEY_PREFIX}:{name}"


def _today_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def ensure_daily_reset(current_capital: float):
    """Call at the start of every main loop iteration. Resets daily counters if the UTC day changed."""
    r = get_client()
    stored_day = r.get(_key("current_day"))
    today = _today_str()

    if stored_day != today:
        r.set(_key("current_day"), today)
        r.set(_key("day_start_capital"), current_capital)
        r.set(_key("trades_today"), 0)
        r.set(_key("consecutive_losses"), 0)
        r.set(_key("day_wins"), 0)
        r.set(_key("day_losses"), 0)
        r.set(_key("day_pnl"), 0.0)
        r.set(_key("summary_sent_for_day"), "0")


def get_day_start_capital() -> float:
    r = get_client()
    val = r.get(_key("day_start_capital"))
    return float(val) if val else 0.0


def get_trades_today() -> int:
    r = get_client()
    val = r.get(_key("trades_today"))
    return int(val) if val else 0


def increment_trades_today():
    r = get_client()
    r.incr(_key("trades_today"))


def get_consecutive_losses() -> int:
    r = get_client()
    val = r.get(_key("consecutive_losses"))
    return int(val) if val else 0


def record_trade_result(was_win: bool, pnl: float):
    r = get_client()
    r.set(_key("last_trade_close_time"), time.time())
    r.set(_key("last_trade_was_win"), "1" if was_win else "0")

    if was_win:
        r.set(_key("consecutive_losses"), 0)
        r.incr(_key("day_wins"))
    else:
        r.incr(_key("consecutive_losses"))
        r.incr(_key("day_losses"))

    r.incrbyfloat(_key("day_pnl"), pnl)


def get_day_stats():
    r = get_client()
    return {
        "wins": int(r.get(_key("day_wins")) or 0),
        "losses": int(r.get(_key("day_losses")) or 0),
        "pnl": float(r.get(_key("day_pnl")) or 0.0),
    }


def summary_already_sent_today() -> bool:
    r = get_client()
    return r.get(_key("summary_sent_for_day")) == "1"


def mark_summary_sent():
    r = get_client()
    r.set(_key("summary_sent_for_day"), "1")


def get_last_trade_info():
    r = get_client()
    close_time = r.get(_key("last_trade_close_time"))
    was_win = r.get(_key("last_trade_was_win"))
    return (
        float(close_time) if close_time else None,
        was_win == "1" if was_win is not None else False,
    )


def set_open_position(position: dict):
    r = get_client()
    r.set(_key("open_position"), json.dumps(position))


def get_open_position():
    r = get_client()
    val = r.get(_key("open_position"))
    return json.loads(val) if val else None


def clear_open_position():
    r = get_client()
    r.delete(_key("open_position"))


def log_evaluation_event(event: dict):
    """
    Appends a signal evaluation decision (taken or rejected) to a Redis list.
    Kept in Redis instead of a local file because Render's free tier wipes
    local disk on every restart/redeploy - Redis persists independently.
    List is trimmed to the most recent 500 entries to avoid unbounded growth.
    """
    r = get_client()
    event = dict(event)
    event["logged_at"] = time.time()
    r.rpush(_key("evaluation_log"), json.dumps(event))
    r.ltrim(_key("evaluation_log"), -500, -1)


def get_recent_evaluation_events(limit: int = 30):
    r = get_client()
    raw = r.lrange(_key("evaluation_log"), -limit, -1)
    return [json.loads(x) for x in raw]


def cache_top_pairs(pairs: list):
    """Stores the freshly-fetched top-volume pairs list along with when it was fetched."""
    r = get_client()
    payload = {"pairs": pairs, "cached_at": time.time()}
    r.set(_key("cached_top_pairs"), json.dumps(payload))


def get_cached_top_pairs():
    """
    Returns (pairs, cached_at) if a cached list exists and is still fresh
    (within config.PAIRS_REFRESH_HOURS), otherwise returns (None, None) so
    the caller knows to fetch a fresh list.
    """
    r = get_client()
    val = r.get(_key("cached_top_pairs"))
    if not val:
        return None, None

    payload = json.loads(val)
    age_hours = (time.time() - payload["cached_at"]) / 3600
    if age_hours >= config.PAIRS_REFRESH_HOURS:
        return None, None

    return payload["pairs"], payload["cached_at"]
