"""
Signal engine - the only place that decides "is this a good setup".

Design intent (per the agreed strategy):
- Scan across the top-volume pairs (multi-pair, like Phoenix), not just BTC
- Target small, high-probability moves (0.5-2% price move -> 5-10% ROI at 7x leverage)
- Trend filter (4h/1h) + pullback entry (15m structure) + trigger confirmation (5m)

VOLATILITY-ADAPTIVE THRESHOLDS: pullback tolerance and hold-confirmation tolerance
now scale with each pair's own ATR, instead of using fixed percentages. Fixed
percentages structurally favored smooth, low-volatility assets (like XAUT) over
genuinely volatile crypto pairs - a fixed 0.3% pullback zone is trivial for a calm
asset to satisfy but can be blown through entirely by a volatile coin's normal
noise, or never revisited at all. Scaling by ATR means a volatile pair gets a
proportionally wider tolerance that matches its real movement, and a calm pair
gets a tighter one - the filter adapts per-asset instead of applying one-size-fits-all
thresholds calibrated for "average" volatility to everything.

ENTRY TIMING: the 5m trigger requires a breakout candle (with volume confirmation)
followed by a HOLD confirmation candle - we enter on the confirmation candle's
close, one candle after the breakout, only if price hasn't given back more than
HOLD_ATR_MULTIPLIER worth of ATR. This targets the late-entry/flatline pattern
seen when entering on the breakout candle itself.
"""

import time
from dataclasses import dataclass, asdict
from typing import Optional

import pandas as pd
import config
import state_manager
from indicators import add_indicators, get_trend_direction


@dataclass
class Signal:
    direction: str          # 'long' or 'short'
    entry_price: float
    stop_loss: float
    take_profit_1: float    # 5% ROI target
    take_profit_2: float    # 10% ROI target
    stop_distance_pct: float
    risk_reward_ratio: float
    reasoning: str
    timestamp: float


def _log_evaluation(event: dict):
    """Append every signal decision (taken or rejected) to the Redis evaluation log."""
    if not config.EVALUATION_MODE:
        return
    try:
        state_manager.log_evaluation_event(event)
    except Exception as e:
        print(f"[signal_engine] Failed to log evaluation event: {e}")


def evaluate_setup(
    df_4h: pd.DataFrame,
    df_1h: pd.DataFrame,
    df_15m: pd.DataFrame,
    df_5m: pd.DataFrame,
    symbol: str,
) -> Optional[Signal]:
    """
    Main entry point. Pass in raw OHLCV dataframes for each timeframe, plus the
    symbol they belong to.
    Returns a Signal if a valid setup exists, otherwise None (and logs why).
    """
    df_4h = add_indicators(df_4h)
    df_1h = add_indicators(df_1h)
    df_15m = add_indicators(df_15m)
    df_5m = add_indicators(df_5m)

    trend = get_trend_direction(df_4h, df_1h)
    if trend == "none":
        _log_evaluation({"result": "rejected", "reason": "no_aligned_trend", "symbol": symbol})
        return None

    last_15m = df_15m.iloc[-1]

    if pd.isna(last_15m["atr"]) or pd.isna(last_15m["ema_fast"]):
        _log_evaluation({"result": "rejected", "reason": "insufficient_15m_history", "symbol": symbol})
        return None

    pullback_tolerance = last_15m["atr"] * config.PULLBACK_ATR_MULTIPLIER

    if trend == "long":
        pulled_back = last_15m["low"] <= last_15m["ema_fast"] + pullback_tolerance
    else:
        pulled_back = last_15m["high"] >= last_15m["ema_fast"] - pullback_tolerance

    if not pulled_back:
        _log_evaluation({"result": "rejected", "reason": "price_extended_no_pullback", "trend": trend, "symbol": symbol})
        return None

    if trend == "long":
        rsi_ok = config.RSI_PULLBACK_ZONE[0] <= last_15m["rsi"] <= 65
    else:
        rsi_ok = 35 <= last_15m["rsi"] <= (100 - config.RSI_PULLBACK_ZONE[0])

    if not rsi_ok:
        _log_evaluation({"result": "rejected", "reason": "rsi_out_of_healthy_zone", "rsi": float(last_15m["rsi"]), "symbol": symbol})
        return None

    # --- 5m trigger: breakout candle + ATR-scaled HOLD confirmation on the following candle ---
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
        _log_evaluation({"result": "rejected", "reason": "no_5m_breakout", "trend": trend, "symbol": symbol})
        return None

    breakout_volume_confirmed = breakout_candle["volume"] > (breakout_candle["volume_ma"] * config.VOLUME_CONFIRMATION_MULTIPLIER)
    if not breakout_volume_confirmed:
        _log_evaluation({"result": "rejected", "reason": "insufficient_volume_confirmation", "symbol": symbol})
        return None

    # --- Hold confirmation: did the move survive the next candle, within ATR-scaled tolerance? ---
    hold_tolerance = breakout_candle["atr"] * config.HOLD_ATR_MULTIPLIER

    if trend == "long":
        hold_confirmed = confirm_candle["low"] >= breakout_candle["low"] - hold_tolerance
        still_bullish = confirm_candle["close"] > confirm_candle["ema_fast"]
    else:
        hold_confirmed = confirm_candle["high"] <= breakout_candle["high"] + hold_tolerance
        still_bullish = confirm_candle["close"] < confirm_candle["ema_fast"]

    if not (hold_confirmed and still_bullish):
        _log_evaluation({"result": "rejected", "reason": "breakout_did_not_hold", "trend": trend, "symbol": symbol})
        return None

    # --- Build the trade: entry is the CONFIRMATION candle's close ---
    entry_price = float(confirm_candle["close"])
    atr_15m = float(last_15m["atr"])

    if trend == "long":
        structure_stop = float(last_15m["swing_low"])
        stop_loss = min(structure_stop, entry_price - atr_15m * config.ATR_STOP_MULTIPLIER)
        stop_distance_pct = (entry_price - stop_loss) / entry_price
        take_profit_1 = entry_price * (1 + config.ROI_TARGET_MIN_PCT / config.L
