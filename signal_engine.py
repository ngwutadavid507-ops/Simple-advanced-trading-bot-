"""
Signal engine - the only place that decides "is this a good setup".

Design intent (per the agreed strategy):
- Scan across the top-volume pairs (multi-pair, like Phoenix), not just BTC
- Target small, high-probability moves (0.5-2% price move -> 5-10% ROI at 7x leverage)
- Trend filter (4h/1h) + pullback entry (15m structure) + trigger confirmation (5m)
- Every rejection is logged with a reason AND the symbol it applies to, so the
  evaluation data shows which pairs are actually producing signals.
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
    symbol they belong to (for logging purposes only - the logic itself is
    symbol-agnostic).
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
    last_5m = df_5m.iloc[-1]
    prev_5m = df_5m.iloc[-2]

    if trend == "long":
        pulled_back = last_15m["low"] <= last_15m["ema_fast"] * 1.003
    else:
        pulled_back = last_15m["high"] >= last_15m["ema_fast"] * 0.997

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

    if trend == "long":
        trigger_fired = (last_5m["close"] > prev_5m["high"]) and (last_5m["close"] > last_5m["ema_fast"])
    else:
        trigger_fired = (last_5m["close"] < prev_5m["low"]) and (last_5m["close"] < last_5m["ema_fast"])

    if not trigger_fired:
        _log_evaluation({"result": "rejected", "reason": "no_5m_trigger", "trend": trend, "symbol": symbol})
        return None

    volume_confirmed = last_5m["volume"] > (last_5m["volume_ma"] * config.VOLUME_CONFIRMATION_MULTIPLIER)
    if not volume_confirmed:
        _log_evaluation({"result": "rejected", "reason": "insufficient_volume_confirmation", "symbol": symbol})
        return None

    entry_price = float(last_5m["close"])
    atr = float(last_15m["atr"])

    if trend == "long":
        structure_stop = float(last_15m["swing_low"])
        stop_loss = min(structure_stop, entry_price - atr * config.ATR_STOP_MULTIPLIER)
        stop_distance_pct = (entry_price - stop_loss) / entry_price
        take_profit_1 = entry_price * (1 + config.ROI_TARGET_MIN_PCT / config.LEVERAGE)
        take_profit_2 = entry_price * (1 + config.ROI_TARGET_MAX_PCT / config.LEVERAGE)
    else:
        structure_stop = float(last_15m["swing_high"])
        stop_loss = max(structure_stop, entry_price + atr * config.ATR_STOP_MULTIPLIER)
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
        })
        return None

    reasoning = (
        f"{trend.upper()} {symbol} | 4h/1h trend aligned | 15m pullback to EMA{config.EMA_FAST} "
        f"with RSI {last_15m['rsi']:.1f} | 5m trigger break with "
        f"{last_5m['volume']/last_5m['volume_ma']:.2f}x avg volume | "
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

    _log_evaluation({"result": "signal_taken", "symbol": symbol, **asdict(signal)})
    return signal
