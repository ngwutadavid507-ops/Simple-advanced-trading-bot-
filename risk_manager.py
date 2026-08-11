"""
Risk management - the module that stands between a "good signal" and a live order.

This is deliberately the most conservative-by-default file in the project. Every function
here exists because of a specific failure mode discussed while designing this bot:
  - position_size():        prevents one bad stop from wiping the account (Phoenix's core flaw)
  - check_circuit_breakers(): prevents a bad DAY from wiping the account even with correct sizing
  - check_cooldown():        prevents revenge-trading / re-entering before the market has moved on

IMPORTANT: cooldown durations and entry criteria must stay FIXED regardless of recent P&L.
Do not add logic that loosens signal criteria or shortens cooldown after losses "to catch up" -
that pattern is one of the most common ways automated strategies degrade over time.
"""

import time
from dataclasses import dataclass
import config


@dataclass
class PositionSizeResult:
    approved: bool
    margin_to_use: float
    notional_size: float
    reason: str


def position_size(current_capital: float, stop_distance_pct: float, leverage: int) -> PositionSizeResult:
    """
    Calculates position size so that a stop-loss hit costs exactly RISK_PER_TRADE_PCT
    of current capital - regardless of how far away the stop needs to be.

    This REPLACES "use full capital every trade" - full capital is only used if the
    resulting risk is still within RISK_PER_TRADE_PCT given how tight the stop is.
    """
    if stop_distance_pct <= 0:
        return PositionSizeResult(False, 0, 0, "invalid_stop_distance")

    risk_amount = current_capital * config.RISK_PER_TRADE_PCT

    # notional needed so that stop_distance_pct move against us = risk_amount
    required_notional = risk_amount / stop_distance_pct
    required_margin = required_notional / leverage

    max_margin = current_capital * config.MAX_MARGIN_PCT_OF_CAPITAL

    if required_margin > max_margin:
        # Stop is too wide for this account size at this leverage to keep risk capped -
        # cap margin at max allowed, which means realized risk on this trade will be LOWER
        # than target (safer), never higher.
        margin_to_use = max_margin
        reason = "capped_at_max_margin_stop_too_wide_for_full_risk"
    else:
        margin_to_use = required_margin
        reason = "sized_to_target_risk_pct"

    notional_size = margin_to_use * leverage

    return PositionSizeResult(True, round(margin_to_use, 2), round(notional_size, 2), reason)


@dataclass
class CircuitBreakerResult:
    trading_allowed: bool
    reason: str


def check_circuit_breakers(day_start_capital: float, current_capital: float, consecutive_losses: int) -> CircuitBreakerResult:
    """
    Hard stops that override signal quality entirely. Checked BEFORE every new entry.
    """
    daily_loss_pct = (day_start_capital - current_capital) / day_start_capital if day_start_capital > 0 else 0

    if daily_loss_pct >= config.MAX_DAILY_LOSS_PCT:
        return CircuitBreakerResult(False, f"daily_loss_limit_hit ({daily_loss_pct:.1%})")

    if consecutive_losses >= config.MAX_CONSECUTIVE_LOSSES:
        return CircuitBreakerResult(False, f"consecutive_loss_limit_hit ({consecutive_losses})")

    return CircuitBreakerResult(True, "ok")


def check_cooldown(last_trade_close_time: float, last_trade_was_win: bool) -> tuple:
    """
    Returns (cooldown_active: bool, seconds_remaining: float).
    Cooldown duration is FIXED - never adjust based on recent performance.
    """
    if last_trade_close_time is None:
        return False, 0

    cooldown_minutes = (
        config.COOLDOWN_MINUTES_AFTER_WIN if last_trade_was_win else config.COOLDOWN_MINUTES_AFTER_LOSS
    )
    cooldown_seconds = cooldown_minutes * 60
    elapsed = time.time() - last_trade_close_time
    remaining = cooldown_seconds - elapsed

    if remaining > 0:
        return True, remaining
    return False, 0


def check_daily_trade_limit(trades_today: int) -> bool:
    """Returns True if another trade is allowed today."""
    return trades_today < config.MAX_TRADES_PER_DAY


def estimate_fee_cost(notional_size: float) -> float:
    """Round-trip (entry + exit) taker fee estimate for a given position notional."""
    return notional_size * config.TAKER_FEE_PCT * 2
