"""
Signal engine - the only place that decides "is this a good setup".

MARKET REGIME AWARE: instead of one fixed entry style, the bot now classifies
each pair's current regime (see indicators.classify_regime) and adapts:
  - extreme_volatility / ranging / insufficient_data -> skip entirely, no
    entry logic here is trustworthy in these conditions
  - normal_trend -> pullback + hold-confirmation entry (original logic)
  - parabolic -> continuation entry: no pullback required (there may not be
    one), relaxed RSI (only rejects at true exhaustion, since parabolic moves
    run RSI hot by design), wider ATR stop (chasing momentum is riskier than
    entering on a pullback, needs more room to avoid normal noise)

Both regimes share the same 5m breakout + hold-confirmation trigger mechanism -
the difference is only in what's required BEFORE that trigger (pullback or not)
and how the stop is sized AFTER it.
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

    # --- Regime-specific pre-trigger checks ---
    if regime == "normal_trend":
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

        stop_multiplier = config.ATR_STOP_MULTIPLIER

    else:  # regime == "parabolic" - no pullback required, only reject at true RSI exhaustion
        if trend == "long":
            rsi_ok = last_15m["rsi"] < config.PARABOLIC_RSI_EXHAUSTION_MAX
        else:
            rsi_ok = last_15m["rsi"] > (100 - config.PARABOLIC_RSI_EXHAUSTION_MAX)

        if not rsi_ok:
            _log_evaluation({"result": "rejected", "reason": "rsi_exhausted_parabolic", "rsi": float(last_15m["rsi"]), "symbol": symbol})
            return None

        stop_multiplier = config.BREAKOUT_ATR_STOP_MULTIPLIER

    # --- Shared 5m trigger: breakout candle + ATR-scaled hold confirmation ---
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

    breakout_volume_confirmed = breakout_candle["volume"] > (breakout_candle["volume_ma"] * config.VOLUME_CONFIRMATION_MULTIPLIER)
    if not breakout_volume_confirmed:
        _log_evaluation({"result": "rejected", "reason": "insufficient_volume_confirmation", "symbol": symbol, "regime": regime})
        return None

    hold_tolerance = breakout_candle["atr"] * config.HOLD_ATR_MULTIPLIER

    if trend == "long":
        hold_confirmed = confirm_candle["low"] >= breakout_candle["low"] - hold_tolerance
        still_bullish = confirm_candle["close"] > confirm_candle["ema_fast"]
    else:
        hold_confirmed = confirm_candle["high"] <= breakout_candle["high"] + hold_tolerance
        still_bullish = confirm_candle["close"] < confirm_candle["ema_fast"]

    if not (hold_confirmed and still_bullish):
        _log_evaluation({"result": "rejected", "reason": "breakout_did_not_hold", "trend": trend, "symbol": symbol, "regime": regime})
        return None

    # --- Build the trade ---
    entry_price = float(confirm_candle["close"])
    atr_15m = float(last_15m["atr"])

    if trend == "long":
        structure_stop = float(last_15m["swing_low"])
        stop_loss = min(structure_stop, entry_price - atr_15m * stop_multiplier)
        stop_distance_pct = (entry_price - stop_loss) / entry_price
        take_profit_1 = entry_price * (1 + config.ROI_TARGET_MIN_PCT / config.LEVERAGE)
        take_profit_2 = entry_price * (1 + config.ROI_TARGET_MAX_PCT / config.LEVERAGE)
    else:
        structure_stop = float(last_15m["swing_high"])
        stop_loss = max(structure_stop, entry_price + atr_15m * stop_multiplier)
        stop_distance_pct = (stop_loss - entry_price) / entry_price
        take_profit_1 = entry_price * (1 - config.ROI_TARGET_MIN_PCT / config.LEVERAGE)
        take_profit_2 = entry_price * (1 - config.ROI_TARGET_MAX_PCT / config.LEVERAGE)

    target_distance_pct = abs(take_profit_1 - entry_price) / entry_price
    risk_reward = target_distance_pct / stop_distance_pct if stop_distance_pct > 0 else 0

    if risk_reward < config.MIN_RISK_REWARD_RATIO:
        _log_evaluation({
            "result": "rejected",
            "reason": "risk_reward_below_minimum",
            "risk_reward": round(risk_reward, 2),
            "symbol": symbol,
            "regime": regime,
        })
        return None

    reasoning = (
        f"{trend.upper()} {symbol} [{regime}] | 4h/1h trend aligned | "
        f"{'15m pullback to EMA' + str(config.EMA_FAST) if regime == 'normal_trend' else 'continuation entry, no pullback required'} "
        f"with RSI {last_15m['rsi']:.1f} | 5m breakout+hold confirmed with "
        f"{breakout_candle['volume']/breakout_candle['volume_ma']:.2f}x avg volume | "
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
        reasoning=reasoning,
        timestamp=time.time(),
    )

    _log_evaluation({"result": "signal_taken", "symbol": symbol, "regime": regime, **asdict(signal)})
    return signal
