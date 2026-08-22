"""
Risk management - the module that stands between a "good signal" and a live order.

Position sizing now also applies the confidence tier's size multiplier (from
signal_engine's grading system) - a high-confidence signal gets full risk-based
size, medium gets half, low gets a quarter. Stop-loss risk-per-trade percentage
stays identical across all tiers; only how much capital is committed scales down
for lower-confidence setups. This lets the bot trade partial-match setups without
risking full size on something that only scored a bare pass.
"""

import time
from dataclasses import dataclass
import config


@dataclass
class PositionSizeResult:
    approved: bool
    margin_to_use: float
    notional_size: float
    amount_in_base: float
    reason: str


def position_size(current_capital: float, stop_distance_pct: float, leverage: int, entry_price: float, tier_multiplier: float = 1.0) -> PositionSizeResult:
    """
    Calculates position size so that a stop-loss hit costs approximately
    RISK_PER_TRADE_PCT of current capital, scaled by the confidence tier's
    size multiplier (1.0 for high confidence, down to 0.25 for low confidence).

    Also enforces Bybit's minimum tradeable amount - bumps up to the minimum if
    that still fits within the capital ceiling, otherwise skips the trade cleanly.
    """
    if stop_distance_pct <= 0:
        return PositionSizeResult(False, 0, 0, 0, "invalid_stop_distance")

    risk_amount = current_capital * config.RISK_PER_TRADE_PCT * tier_multiplier

    required_notional = risk_amount / stop_distance_pct
    required_margin = required_notional / leverage

    max_margin = current_capital * config.MAX_MARGIN_PCT_OF_CAPITAL * tier_multiplier

    if required_margin > max_margin:
        margin_to_use = max_margin
        reason = "capped_at_max_margin_stop_too_wide_for_full_risk"
    else:
        margin_to_use = required_margin
        reason = "sized_to_target_risk_pct"

    notional_size = margin_to_use * leverage
    amount_in_base = notional_size / entry_price

    if amount_in_base < config.MIN_BTC_ORDER_SIZE:
        min_notional_needed = config.MIN_BTC_ORDER_SIZE * entry_price
        min_margin_needed = min_notional_needed / leverage

        # For minimum-size bump, check against the FULL (non-tier-scaled) capital ceiling -
        # a low-confidence trade should still be allowed to meet the exchange minimum,
        # it just won't get scaled back down from there.
        full_max_margin = current_capital * config.MAX_MARGIN_PCT_OF_CAPITAL

        if min_margin_needed <= full_max_margin:
            margin_to_use = round(min_margin_needed, 2)
            notional_size = round(min_notional_needed, 2)
            amount_in_base = round(config.MIN_BTC_ORDER_SIZE, 6)
            reason = "bumped_to_exchange_minimum_order_size"
        else:
            return PositionSizeResult(False, 0, 0, 0, "stop_too_tight_cannot_meet_exchange_minimum_safely")

    return PositionSizeResult(
        True,
        round(margin_to_use, 2),
        round(notional_size, 2),
        round(amount_in_base, 6),
        reason,
    )


@dataclass
class CircuitBreakerResult:
    trading_allowed: bool
    reason: str


def check_circuit_breakers(day_start_capital: float, current_capital: float, consecutive_losses: int) -> CircuitBreakerResult:
    daily_loss_pct = (day_start_capital - current_capital) / day_start_capital if day_start_capital > 0 else 0

    if daily_loss_pct >= config.MAX_DAILY_LOSS_PCT:
        return CircuitBreakerResult(False, f"daily_loss_limit_hit ({daily_loss_pct:.1%})")

    if consecutive_losses >= config.MAX_CONSECUTIVE_LOSSES:
        return CircuitBreakerResult(False, f"consecutive_loss_limit_hit ({consecutive_losses})")

    return CircuitBreakerResult(True, "ok")


def check_cooldown(last_trade_close_time: float, last_trade_was_win: bool) -> tuple:
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
    return trades_today < config.MAX_TRADES_PER_DAY


def estimate_fee_cost(notional_size: float) -> float:
    return notional_size * config.TAKER_FEE_PCT * 2
