"""
Bybit exchange wrapper via ccxt.

Bybit's demo trading environment is a real, separate account with fake funds that
behaves like the live exchange (real price feed, real order matching simulation) -
this is what USE_DEMO routes to. It is NOT the same as Bybit's public testnet
(different price feed, less realistic). Demo trading is the correct choice for the
agreed 3-5 day evaluation because it uses live market data.

Setup note: generate demo trading API keys from Bybit's demo trading dashboard
(separate from your real API keys) and set them as BYBIT_API_KEY / BYBIT_API_SECRET.

MULTI-PAIR: every function now takes a symbol parameter instead of a fixed one,
since the bot scans across the top-volume pairs (like Phoenix did) rather than
trading BTC exclusively. get_top_volume_pairs() is what drives pair selection -
using 24h volume as the quality filter naturally excludes thin/illiquid/scam-
adjacent tokens without needing a manual blocklist.

IMPORTANT: stop-loss and take-profit are attached DIRECTLY on the entry order
via Bybit's stopLoss/takeProfit params - NOT as separate follow-up orders.
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


def get_top_volume_pairs(exchange) -> list:
    """
    Returns the top config.TOP_PAIRS_COUNT USDT perpetual pairs by 24h quote volume.
    High volume is the quality filter here - same principle as Phoenix's multi-pair
    scanning - it naturally excludes thin, illiquid, or scam-adjacent tokens without
    needing a manually maintained blocklist.
    """
    tickers = exchange.fetch_tickers()

    candidates = []
    for symbol, ticker in tickers.items():
        # Only linear USDT perpetuals (ccxt naming convention: "XXX/USDT:USDT")
        if not symbol.endswith("/USDT:USDT"):
            continue
        if any(marker in symbol for marker in config.EXCLUDE_SYMBOL_MARKERS):
            continue

        volume = ticker.get("quoteVolume") or 0
        if volume <= 0:
            continue

        candidates.append((symbol, volume))

    candidates.sort(key=lambda x: x[1], reverse=True)
    top_symbols = [symbol for symbol, _ in candidates[:config.TOP_PAIRS_COUNT]]
    return top_symbols


def fetch_ohlcv_df(exchange, symbol: str, timeframe: str, limit: int = config.CANDLE_LOOKBACK) -> pd.DataFrame:
    raw = exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
    df = pd.DataFrame(raw, columns=["timestamp", "open", "high", "low", "close", "volume"])
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
    return df


def get_all_timeframes(exchange, symbol: str) -> dict:
    """Fetches OHLCV for all timeframes the signal engine needs, for one symbol."""
    return {
        "4h": fetch_ohlcv_df(exchange, symbol, config.TREND_TIMEFRAME_HIGH),
        "1h": fetch_ohlcv_df(exchange, symbol, config.TREND_TIMEFRAME_MID),
        "15m": fetch_ohlcv_df(exchange, symbol, config.ENTRY_TIMEFRAME),
        "5m": fetch_ohlcv_df(exchange, symbol, config.TRIGGER_TIMEFRAME),
    }


def get_available_capital(exchange) -> float:
    balance = exchange.fetch_balance()
    return float(balance["USDT"]["free"])


def set_leverage(exchange, symbol: str, leverage: int):
    try:
        exchange.set_leverage(leverage, symbol)
    except Exception as e:
        # Some accounts/symbols throw if leverage is already set to this value - non-fatal
        print(f"[exchange] set_leverage note for {symbol}: {e}")


def place_market_order_with_protection(exchange, symbol: str, side: str, amount_in_base: float, stop_loss: float, take_profit: float):
    """
    Places the entry order WITH stop-loss and take-profit attached directly,
    via Bybit's native stopLoss/takeProfit order params - guarantees protection
    is in place the instant the position opens.

    side: 'buy' for long entry, 'sell' for short entry.
    amount_in_base: position size in the base asset (e.g. BTC, ETH), not USDT.
    """
    return exchange.create_order(
        symbol=symbol,
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


def close_position_at_market(exchange, symbol: str, direction: str, amount_in_base: float):
    """
    Force-closes an open position at market price. Used for the max-hold-time
    exit - if neither stop nor target has been hit within MAX_POSITION_HOLD_HOURS,
    the trigger condition that justified entry has expired, so we exit cleanly
    rather than continue holding an undefined, unplanned position.
    """
    close_side = "sell" if direction == "long" else "buy"
    return exchange.create_order(
        symbol=symbol,
        type="market",
        side=close_side,
        amount=amount_in_base,
        params={"reduceOnly": True},
    )


def get_realized_pnl(exchange, position: dict) -> float:
    """
    Computes actual realized PnL (in USDT, net of fees) for a closed position by
    pulling real fill data since the position was opened - replaces any estimate
    based on before/after account balance, which can be thrown off by funding
    payments or other balance changes unrelated to this specific trade.

    position dict includes 'symbol' - required now that trades can be on any pair.
    """
    symbol = position["symbol"]
    since_ms = int(position["opened_at"] * 1000)
    close_side = "sell" if position["direction"] == "long" else "buy"

    trades = exchange.fetch_my_trades(symbol, since=since_ms, limit=50)

    closing_trades = [t for t in trades if t.get("side") == close_side and t.get("timestamp", 0) >= since_ms]

    if not closing_trades:
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

    entry_notional = position["notional_size"]
    entry_fee_estimate = entry_notional * config.TAKER_FEE_PCT

    net_pnl = gross_pnl - total_fees - entry_fee_estimate
    return round(net_pnl, 4)
