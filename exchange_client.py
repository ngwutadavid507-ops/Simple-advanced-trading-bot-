"""
Bybit exchange wrapper via ccxt.

Bybit's demo trading environment is a real, separate account with fake funds that
behaves like the live exchange (real price feed, real order matching simulation) -
this is what USE_DEMO routes to. It is NOT the same as Bybit's public testnet
(different price feed, less realistic). Demo trading is the correct choice for the
agreed 3-5 day evaluation because it uses live market data.

Setup note: generate demo trading API keys from Bybit's demo trading dashboard
(separate from your real API keys) and set them as BYBIT_API_KEY / BYBIT_API_SECRET.
"""

import ccxt
import pandas as pd
import config


def get_exchange():
    exchange = ccxt.bybit({
        "apiKey": config.API_KEY,
        "secret": config.API_SECRET,
        "enableRateLimit": True,
        "options": {"defaultType": "swap"},
    })

    if config.USE_DEMO:
        # ccxt's Bybit implementation supports demo trading mode via this flag
        exchange.enable_demo_trading(True)

    return exchange


def fetch_ohlcv_df(exchange, timeframe: str, limit: int = config.CANDLE_LOOKBACK) -> pd.DataFrame:
    raw = exchange.fetch_ohlcv(config.SYMBOL, timeframe=timeframe, limit=limit)
    df = pd.DataFrame(raw, columns=["timestamp", "open", "high", "low", "close", "volume"])
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
    return df


def get_all_timeframes(exchange) -> dict:
    """Fetches OHLCV for all timeframes the signal engine needs, in one call."""
    return {
        "4h": fetch_ohlcv_df(exchange, config.TREND_TIMEFRAME_HIGH),
        "1h": fetch_ohlcv_df(exchange, config.TREND_TIMEFRAME_MID),
        "15m": fetch_ohlcv_df(exchange, config.ENTRY_TIMEFRAME),
        "5m": fetch_ohlcv_df(exchange, config.TRIGGER_TIMEFRAME),
    }


def get_available_capital(exchange) -> float:
    balance = exchange.fetch_balance()
    return float(balance["USDT"]["free"])


def set_leverage(exchange, leverage: int):
    try:
        exchange.set_leverage(leverage, config.SYMBOL)
    except Exception as e:
        # Some accounts/symbols throw if leverage is already set to this value - non-fatal
        print(f"[exchange] set_leverage note: {e}")


def place_market_order(exchange, side: str, amount_in_base: float):
    """
    side: 'buy' for long entry, 'sell' for short entry.
    amount_in_base: position size in BTC (notional_size / entry_price), not USDT.
    """
    return exchange.create_order(
        symbol=config.SYMBOL,
        type="market",
        side=side,
        amount=amount_in_base,
    )


def place_stop_loss(exchange, side: str, amount_in_base: float, stop_price: float):
    """side here is the CLOSING side - opposite of the entry side."""
    return exchange.create_order(
        symbol=config.SYMBOL,
        type="market",
        side=side,
        amount=amount_in_base,
        params={"stopLoss": stop_price, "reduceOnly": True},
    )


def place_take_profit(exchange, side: str, amount_in_base: float, tp_price: float):
    """side here is the CLOSING side - opposite of the entry side."""
    return exchange.create_order(
        symbol=config.SYMBOL,
        type="limit",
        side=side,
        amount=amount_in_base,
        price=tp_price,
        params={"reduceOnly": True},
    )


def get_realized_pnl(exchange, position: dict) -> float:
    """
    Computes actual realized PnL (in USDT, net of fees) for a closed position by
    pulling real fill data since the position was opened - replaces any estimate
    based on before/after account balance, which can be thrown off by funding
    payments or other balance changes unrelated to this specific trade.

    direction: 'long' or 'short' as stored on the position dict
    entry_price: the price recorded when the position was opened
    """
    since_ms = int(position["opened_at"] * 1000)
    close_side = "sell" if position["direction"] == "long" else "buy"

    trades = exchange.fetch_my_trades(config.SYMBOL, since=since_ms, limit=50)

    # Only trades that closed this position (opposite side of entry, after it opened)
    closing_trades = [t for t in trades if t.get("side") == close_side and t.get("timestamp", 0) >= since_ms]

    if not closing_trades:
        # Position shows closed on the exchange but no matching closing trade found yet -
        # likely an API/indexing delay. Caller should retry next cycle rather than trust a zero.
        raise ValueError("no_closing_trades_found_yet")

    entry_price = position["entry_price"]
    direction = position["direction"]

    gross_pnl = 0.0
    total_fees = 0.0

    for t in closing_trades:
        fill_price = float(t["price"])
        fill_amount = float(t["amount"])

        if direction == "long":
            gross_pnl += (fill_price - entry_price) * fill_amount
        else:
            gross_pnl += (entry_price - fill_price) * fill_amount

        fee = t.get("fee") or {}
        total_fees += float(fee.get("cost", 0) or 0)

    # Also account for the entry fee, which was paid but isn't in these closing trades
    entry_notional = position["notional_size"]
    entry_fee_estimate = entry_notional * config.TAKER_FEE_PCT

    net_pnl = gross_pnl - total_fees - entry_fee_estimate
    return round(net_pnl, 4)
