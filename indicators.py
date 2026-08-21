"""
Technical indicator calculations.
Kept separate from signal logic so indicators can be unit-tested / backtested independently.
"""

import pandas as pd
import pandas_ta_classic as ta
import config


def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """
    Takes a raw OHLCV dataframe (columns: timestamp, open, high, low, close, volume)
    and appends the indicator columns used by the signal engine.
    """
    df = df.copy()

    df["ema_fast"] = ta.ema(df["close"], length=config.EMA_FAST)
    df["ema_slow"] = ta.ema(df["close"], length=config.EMA_SLOW)
    df["ema_macro"] = ta.ema(df["close"], length=config.EMA_MACRO)

    df["rsi"] = ta.rsi(df["close"], length=config.RSI_PERIOD)

    atr = ta.atr(df["high"], df["low"], df["close"], length=config.ATR_PERIOD)
    df["atr"] = atr
    df["atr_avg"] = df["atr"].rolling(config.ATR_AVG_PERIOD).mean()

    df["volume_ma"] = df["volume"].rolling(config.VOLUME_MA_PERIOD).mean()

    swing_lookback = 10
    df["swing_low"] = df["low"].rolling(swing_lookback).min()
    df["swing_high"] = df["high"].rolling(swing_lookback).max()

    return df


def get_trend_direction(df_high_tf: pd.DataFrame, df_mid_tf: pd.DataFrame) -> str:
    """
    Determines macro trend direction using 4h + 1h EMA alignment.
    Returns 'long', 'short', or 'none' (no clear/aligned trend, OR insufficient
    history to calculate the EMA yet).
    """
    last_high = df_high_tf.iloc[-1]
    last_mid = df_mid_tf.iloc[-1]

    required_values = [
        last_high["close"], last_high["ema_slow"], last_high["ema_macro"],
        last_mid["ema_fast"], last_mid["ema_slow"],
    ]
    if any(pd.isna(v) for v in required_values):
        return "none"

    macro_uptrend = last_high["close"] > last_high["ema_slow"] > last_high["ema_macro"]
    macro_downtrend = last_high["close"] < last_high["ema_slow"] < last_high["ema_macro"]

    mid_bullish = last_mid["ema_fast"] > last_mid["ema_slow"]
    mid_bearish = last_mid["ema_fast"] < last_mid["ema_slow"]

    if macro_uptrend and mid_bullish:
        return "long"
    if macro_downtrend and mid_bearish:
        return "short"
    return "none"


def classify_regime(last_15m, trend: str) -> str:
    """
    Classifies current market regime for a pair, using the 15m timeframe's
    latest indicator values. Returns one of:
      - 'extreme_volatility': current ATR is unusually high vs its own recent
        average - conditions too erratic to trust any entry logic
      - 'ranging': EMA20/50/100 are bunched too close together relative to ATR -
        no real directional trend to trade
      - 'parabolic': price is extended far beyond EMA20 relative to ATR, with
        an aligned trend - strong/fast trending conditions, suited to
        continuation entries rather than waiting for a pullback that may not come
      - 'normal_trend': aligned trend, price reasonably close to EMA20 -
        suited to the existing pullback + hold-confirmation entry

    Requires 'trend' (from get_trend_direction) to already be 'long' or 'short' -
    caller should skip regime classification entirely if trend is 'none'.
    """
    required = [last_15m["atr"], last_15m["atr_avg"], last_15m["ema_fast"], last_15m["ema_slow"], last_15m["ema_macro"], last_15m["close"]]
    if any(pd.isna(v) for v in required):
        return "insufficient_data"

    atr = last_15m["atr"]
    atr_avg = last_15m["atr_avg"]
    close = last_15m["close"]
    ema_fast = last_15m["ema_fast"]
    ema_slow = last_15m["ema_slow"]
    ema_macro = last_15m["ema_macro"]

    # --- Extreme volatility check (applies regardless of direction) ---
    if atr_avg > 0 and (atr / atr_avg) >= config.EXTREME_VOLATILITY_RATIO:
        return "extreme_volatility"

    # --- Ranging check: are the EMAs bunched too close together relative to ATR? ---
    ema_spread = max(ema_fast, ema_slow, ema_macro) - min(ema_fast, ema_slow, ema_macro)
    if atr > 0 and (ema_spread / atr) < config.RANGING_EMA_BAND_ATR_MULTIPLIER:
        return "ranging"

    # --- Parabolic check: is price extended far beyond EMA20 relative to ATR? ---
    extension = abs(close - ema_fast)
    if atr > 0 and (extension / atr) >= config.STRONG_TREND_ATR_MULTIPLIER:
        return "parabolic"

    return "normal_trend"
