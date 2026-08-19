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

    df["volume_ma"] = df["volume"].rolling(config.VOLUME_MA_PERIOD).mean()

    # Recent swing high/low over a short lookback - used for structure-based stop placement
    swing_lookback = 10
    df["swing_low"] = df["low"].rolling(swing_lookback).min()
    df["swing_high"] = df["high"].rolling(swing_lookback).max()

    return df


def get_trend_direction(df_high_tf: pd.DataFrame, df_mid_tf: pd.DataFrame) -> str:
    """
    Determines macro trend direction using 4h + 1h EMA alignment.
    Returns 'long', 'short', or 'none' (no clear/aligned trend, OR insufficient
    history to calculate the 200-period EMA yet - some newer-listed pairs in the
    multi-pair scan won't have 200 candles of 4h history, which previously crashed
    the whole cycle instead of just skipping that one pair).
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
