"""
Bybit exchange wrapper via ccxt.

Bybit's demo trading environment is a real, separate account with fake funds that
behaves like the live exchange (real price feed, real order matching simulation) -
this is what USE_DEMO routes to. It is NOT the same as Bybit's public testnet
(different price feed, less realistic). Demo trading is the correct choice for the
agreed 3-5 day evaluation because it uses live market data.

Setup note: generate demo trading API keys from Bybit's demo trading dashboard
(separate from your real API keys) and set them as BYBIT_API_KEY / BYBIT_API_SECRET.

IMPORTANT: stop-loss and take-profit are attached DIRECTLY on the entry order
via Bybit's stopLoss/takeProfit params - NOT as separate follow-up orders. An
earlier version tried placing them as separate market orders, which Bybit's API
does not support the way that code assumed, and they were silently failing to
attach, leaving live positions completely unprotected.
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


def place_market_order_with_protection(exchange, side: str, amount_in_base: float, stop_loss: float, take_profit: float):
    """
    Places the entry order WITH stop-loss and take-profit attached directly,
    via Bybit's native stopLoss/takeProfit order params. This is the correct
    way to guarantee protection is in place the instant the position opens -
    not as a race-condition-prone follow-up order.

    side: 'buy' for long entry, 'sell' for short entry.
    amount_in_base: position size in BTC (notional_size / entry_price), not USDT.
    """
    return exchange.create_order(
        symbol=config.SYMBOL,
        type="market",
        side=side,
        amount=amount_in_base,
        params={
            "stopLoss": str(stop_loss),
            "takeProfit": str(take_profit),
            "slTriggerBy": "LastPrice",
            "tpTriggerBy": "LastPrice",
        },
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


def close_position_at_market(exchange, direction: str, amount_in_base: float):
    """
    Force-closes an open position at market price. Used for the max-hold-time
    exit - if neither stop nor target has been hit within MAX_POSITION_HOLD_HOURS,
    the trigger condition that justified entry has expired, so we exit cleanly
    rather than continue holding an undefined, unplanned position.
    """
    close_side = "sell" if direction == "long" else "buy"
    return exchange.create_order(
        symbol=config.SYMBOL,
        type="market",
        side=close_side,
        amount=amount_in_base,
        params={"reduceOnly": True},
    )
