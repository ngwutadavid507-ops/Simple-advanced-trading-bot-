"""
Signal engine - now grades signal quality instead of gating on it.

HARD requirements (must pass, no exceptions - these define whether a real
tradeable setup exists at all):
  - trend must be aligned (4h/1h)
  - regime must not be ranging/extreme_volatility/insufficient_data
  - a real 5m breakout candle must have occurred in the trend direction
  - in parabolic regime, RSI must not be at true exhaustion (85+/15-) -
    this signals imminent reversal, not just "lower quality"
  - if the confirmation candle fully erases the breakout (real invalidation,
    not just some give-back), reject - the setup genuinely broke down
  - final achieved R:R (using whichever tier the score lands in) must clear
    MIN_RISK_REWARD_RATIO - won't take structurally bad-expectancy trades
    even at the lowest confidence tier

Everything else that used to hard-reject (volume strength, RSI distance from
ideal, pullback tightness, how cleanly the breakout held) is now SCORED
instead. The total score picks a confidence tier (high/medium/low), and the
tier sets the ROI target and position size - not whether the trade happens.
"""

import time
from dataclasses import dataclass, asdict
from typing import Optional

import pandas as pd
import config
import state_manager
from indicators import add_indicators, get_trend_direction, classify_regime


@dataclass
class Signal:
    direction: str
    entry_price: float
    stop_loss: float
    take_profit_1: float
    take_profit_2: float
    stop_distance_pct: float
    risk_reward_ratio: float
    confidence_tier: str
    confidence_score: float
    reasoning: str
    timestamp: float


def _log_evaluation(event: dict):
    if not config.EVALUATION_MODE:
        return
    try:
        state_manager.log_evaluation_event(event)
    except Exception as e:
        print(f"[signal_engine] Failed to log evaluation event: {e}")


def _score_volume(ratio: float) -> float:
    """0 pts at 1.0x average volume, full weight at 3.0x+."""
    max_pts = config.SCORE_WEIGHTS["volume_strength"]
    score = (ratio - 1.0) / 2.0 * max_pts
    return max(0, min(max_pts, score))


def _score_rsi(rsi: float, trend: str) -> float:
    """Full weight at the exact center of the healthy zone, tapering off with distance."""
    max_pts = config.SCORE_WEIGHTS["rsi_quality"]
    zone_center = sum(config.RSI_PULLBACK_ZONE) / 2
    center = zone_center if trend == "long" else (100 - zone_center)
    distance = abs(rsi - center)
    half_width = 30
    return max_pts * max(0, 1 - distance / half_width)


def _score_pullback(regime: str, last_15m, trend: str, pullback_tolerance: float) -> float:
    """Full credit if not applicable (parabolic regime skips pullback entirely)."""
    max_pts = config.SCORE_WEIGHTS["pullback_tightness"]
    if regime != "normal_trend" or pullback_tolerance <= 0:
        return max_pts
    if trend == "long":
        raw_distance = abs(last_15m["low"] - last_15m["ema_fast"])
    else:
        raw_distance = abs(last_15m["high"] - last_15m["ema_fast"])
    ratio = raw_distance / pullback_tolerance
    return max_pts * max(0, 1 - ratio)


def _score_hold(giveback: float, reference_tolerance: float, still_aligned: bool) -> float:
    max_pts = config.SCORE_WEIGHTS["hold_strength"]
    ratio = giveback / reference_tolerance if reference_tolerance > 0 else 0
    base_score = max_pts * max(0, 1 - ratio)
    # Still-aligned-with-EMA is a bonus signal, not a hard requirement anymore -
    # losing it costs half the hold score rather than rejecting outright.
    if not still_aligned:
        base_score *= 0.5
    return base_score


def _pick_tier(score: float) -> str:
    for tier_name in ("high", "medium", "low"):
        if score >= config.CONFIDENCE_TIERS[tier_name]["min_score"]:
            return tier_name
    return "low"


def evaluate_setup(
    df_4h: pd.DataFrame,
    df_1h: pd.DataFrame,
    df_15m: pd.DataFrame,
    df_5m: pd.DataFrame,
    symbol: str,
) -> Optional[Signal]:
    df_4h = add_indicators(df_4h)
    df_1h = add_indicators(df_1h)
    df_15m = add_indicators(df_15m)
    df_5m = add_indicators(df_5m)

    trend = get_trend_direction(df_4h, df_1h)
    if trend == "none":
        _log_evaluation({"result": "rejected", "reason": "no_aligned_trend", "symbol": symbol})
        return None

    last_15m = df_15m.iloc[-1]

    regime = classify_regime(last_15m, trend)
    if regime in ("extreme_volatility", "ranging", "insufficient_data"):
        _log_evaluation({"result": "rejected", "reason": f"regime_{regime}", "trend": trend, "symbol": symbol})
        return None

    pullback_tolerance = last_15m["atr"] * config.PULLBACK_ATR_MULTIPLIER if regime == "normal_trend" else 0

    # --- Parabolic-only hard gate: true RSI exhaustion ---
    if regime == "parabolic":
        if trend == "long":
            exhausted = last_15m["rsi"] >= config.PARABOLIC_RSI_EXHAUSTION_MAX
        else:
            exhausted = last_15m["rsi"] <= (100 - config.PARABOLIC_RSI_EXHAUSTION_MAX)
        if exhausted:
            _log_evaluation({"result": "rejected", "reason": "rsi_exhausted_parabolic", "rsi": float(last_15m["rsi"]), "symbol": symbol})
            return None

    # --- Hard gate: a real 5m breakout must exist ---
    if len(df_5m) < 3:
        _log_evaluation({"result": "rejected", "reason": "insufficient_5m_history", "symbol": symbol})
        return None

    prior_candle = df_5m.iloc[-3]
    breakout_candle = df_5m.iloc[-2]
    confirm_candle = df_5m.iloc[-1]

    if pd.isna(breakout_candle["atr"]) or pd.isna(breakout_candle["ema_fast"]):
        _log_evaluation({"result": "rejected", "reason": "insufficient_5m_atr_history", "symbol": symbol})
        return None

    if trend == "long":
        breakout_fired = (breakout_candle["close"] > prior_candle["high"]) and (breakout_candle["close"] > breakout_candle["ema_fast"])
    else:
        breakout_fired = (breakout_candle["close"] < prior_candle["low"]) and (breakout_candle["close"] < breakout_candle["ema_fast"])

    if not breakout_fired:
        _log_evaluation({"result": "rejected", "reason": "no_5m_breakout", "trend": trend, "symbol": symbol, "regime": regime})
        return None

    # --- Hard gate: full invalidation (confirm candle completely erases the breakout) ---
    if trend == "long":
        fully_invalidated = confirm_candle["close"] < prior_candle["high"]
        giveback = max(0, breakout_candle["low"] - confirm_candle["low"])
        still_aligned = confirm_candle["close"] > confirm_candle["ema_fast"]
    else:
        fully_invalidated = confirm_candle["close"] > prior_candle["low"]
        giveback = max(0, confirm_candle["high"] - breakout_candle["high"])
        still_aligned = confirm_candle["close"] < confirm_candle["ema_fast"]

    if fully_invalidated:
        _log_evaluation({"result": "rejected", "reason": "breakout_fully_invalidated", "trend": trend, "symbol": symbol, "regime": regime})
        return None

    # --- Score the quality factors ---
    volume_ratio = breakout_candle["volume"] / breakout_candle["volume_ma"] if breakout_candle["volume_ma"] > 0 else 0
    volume_score = _score_volume(volume_ratio)
    rsi_score = _score_rsi(float(last_15m["rsi"]), trend)
    pullback_score = _score_pullback(regime, last_15m, trend, pullback_tolerance)
    hold_reference_tolerance = breakout_candle["atr"] * 1.0  # wider reference band used only for grading giveback
    hold_score = _score_hold(giveback, hold_reference_tolerance, still_aligned)

    total_score = round(volume_score + rsi_score + pullback_score + hold_score, 1)
    tier_name = _pick_tier(total_score)
    tier = config.CONFIDENCE_TIERS[tier_name]

    # --- Build the trade using the chosen tier's ROI target ---
    entry_price = float(confirm_candle["close"])
    atr_15m = float(last_15m["atr"])
    stop_multiplier = config.BREAKOUT_ATR_STOP_MULTIPLIER if regime == "parabolic" else config.ATR_STOP_MULTIPLIER

    if trend == "long":
        structure_stop = float(last_15m["swing_low"])
        stop_loss = min(structure_stop, entry_price - atr_15m * stop_multiplier)
        stop_distance_pct = (entry_price - stop_loss) / entry_price
        take_profit_1 = entry_price * (1 + tier["roi_target_min_pct"] / config.LEVERAGE)
        take_profit_2 = entry_price * (1 + tier["roi_target_max_pct"] / config.LEVERAGE)
    else:
        structure_stop = float(last_15m["swing_high"])
        stop_loss = max(structure_stop, entry_price + atr_15m * stop_multiplier)
        stop_distance_pct = (stop_loss - entry_price) / entry_price
        take_profit_1 = entry_price * (1 - tier["roi_target_min_pct"] / config.LEVERAGE)
        take_profit_2 = entry_price * (1 - tier["roi_target_max_pct"] / config.LEVERAGE)

    target_distance_pct = abs(take_profit_1 - entry_price) / entry_price
    risk_reward = target_distance_pct / stop_distance_pct if stop_distance_pct > 0 else 0

    # --- Final hard gate: even the lowest tier must have sane expectancy ---
    if risk_reward < config.MIN_RISK_REWARD_RATIO:
        _log_evaluation({
            "result": "rejected",
            "reason": "risk_reward_below_minimum",
            "risk_reward": round(risk_reward, 2),
            "score": total_score,
            "tier": tier_name,
            "symbol": symbol,
            "regime": regime,
        })
        return None

    reasoning = (
        f"{trend.upper()} {symbol} [{regime}/{tier_name.upper()} conf={total_score}] | "
        f"vol={volume_score:.0f} rsi={rsi_score:.0f} pullback={pullback_score:.0f} hold={hold_score:.0f} | "
        f"R:R {risk_reward:.2f}"
    )

    signal = Signal(
        direction=trend,
        entry_price=entry_price,
        stop_loss=round(stop_loss, 2),
        take_profit_1=round(take_profit_1, 2),
        take_profit_2=round(take_profit_2, 2),
        stop_distance_pct=round(stop_distance_pct, 5),
        risk_reward_ratio=round(risk_reward, 2),
        confidence_tier=tier_name,
        confidence_score=total_score,
        reasoning=reasoning,
        timestamp=time.time(),
    )

    _log_evaluation({"result": "signal_taken", "symbol": symbol, "regime": regime, "tier": tier_name, "score": total_score, **asdict(signal)})
    return signal
