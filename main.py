"""
Main loop. Order of operations on every cycle, deliberately in this sequence:

  1. Circuit breakers checked FIRST - can halt trading before anything else runs
  2. Cooldown checked - blocks entries even if a signal would otherwise qualify
  3. Daily trade limit checked
  4. Only then: refresh/reuse the top-volume pairs list, scan each pair in order,
     trade the FIRST one that produces a qualifying signal (one position at a time)
  5. If a signal passes ALL of the above: size the position, place the trade
     WITH stop-loss/take-profit attached directly on the entry order
  6. Monitor the open position (on its specific symbol) until it closes (stop/target
     hit OR max hold time exceeded), record the result, start cooldown
  7. Once per day, send a Telegram summary

MULTI-PAIR: while a position is open, the bot does NOT scan other pairs for new
entries (one trade at a time, as agreed) - but it resumes scanning the full list
again as soon as it's flat, so it's never "stuck" watching only one pair.

Trading hours restriction has been removed - the bot is unattended and automated,
so it evaluates signals across all pairs, around the clock.
"""

import time
import traceback

import config
import exchange_client
import signal_engine
import risk_manager
import state_manager
import notifier


POLL_INTERVAL_SECONDS = 60  # how often the main loop checks conditions when flat (no open position)


def get_pairs_to_scan(exchange) -> list:
    """
    Returns the current top-volume pairs list, using a cached version if it's
    still fresh (per config.PAIRS_REFRESH_HOURS) to avoid re-fetching the full
    market ticker list every single cycle.
    """
    cached_pairs, _ = state_manager.get_cached_top_pairs()
    if cached_pairs:
        return cached_pairs

    fresh_pairs = exchange_client.get_top_volume_pairs(exchange)
    state_manager.cache_top_pairs(fresh_pairs)
    print(f"[main] Refreshed top-volume pairs list: {len(fresh_pairs)} pairs.")
    return fresh_pairs


def maybe_send_daily_summary(current_capital):
    """
    Sends the daily summary once per UTC day. Runs regardless of whether a
    position is open, so the summary still fires even if the day happened
    to end mid-trade.
    """
    if not state_manager.summary_already_sent_today():
        stats = state_manager.get_day_stats()
        trades_today = state_manager.get_trades_today()
        notifier.notify_daily_summary(trades_today, stats["wins"], stats["losses"], stats["pnl"], current_capital)
        state_manager.mark_summary_sent()


def run_cycle(exchange):
    current_capital = exchange_client.get_available_capital(exchange)
    state_manager.ensure_daily_reset(current_capital)
    maybe_send_daily_summary(current_capital)

    open_position = state_manager.get_open_position()
    if open_position:
        manage_open_position(exchange, open_position)
        return

    # --- 1. Circuit breakers ---
    day_start_capital = state_manager.get_day_start_capital()
    consecutive_losses = state_manager.get_consecutive_losses()
    breaker = risk_manager.check_circuit_breakers(day_start_capital, current_capital, consecutive_losses)
    if not breaker.trading_allowed:
        print(f"[main] Circuit breaker active: {breaker.reason}")
        return

    # --- 2. Cooldown ---
    last_close_time, last_was_win = state_manager.get_last_trade_info()
    cooldown_active, remaining = risk_manager.check_cooldown(last_close_time, last_was_win)
    if cooldown_active:
        print(f"[main] Cooldown active, {remaining/60:.1f} min remaining")
        return

    # --- 3. Daily trade limit ---
    trades_today = state_manager.get_trades_today()
    if not risk_manager.check_daily_trade_limit(trades_today):
        print(f"[main] Daily trade limit reached ({trades_today})")
        return

    # --- 4. Scan pairs, trade the first qualifying signal ---
    pairs = get_pairs_to_scan(exchange)
    for symbol in pairs:
        try:
            timeframes = exchange_client.get_all_timeframes(exchange, symbol)
        except Exception as e:
            print(f"[main] Skipping {symbol}, data fetch failed: {e}")
            continue

        signal = signal_engine.evaluate_setup(
            timeframes["4h"], timeframes["1h"], timeframes["15m"], timeframes["5m"], symbol
        )

        if signal is None:
            continue

        sizing = risk_manager.position_size(current_capital, signal.stop_distance_pct, config.LEVERAGE, signal.entry_price)
        if not sizing.approved:
            print(f"[main] {symbol} signal found but sizing rejected: {sizing.reason}")
            continue

        fee_estimate = risk_manager.estimate_fee_cost(sizing.notional_size)
        print(f"[main] Signal approved on {symbol}: {signal.reasoning}")
        print(f"[main] Sizing: margin=${sizing.margin_to_use} notional=${sizing.notional_size} "
              f"amount={sizing.amount_in_base} ({sizing.reason}) est_fees=${fee_estimate:.3f}")

        execute_trade(exchange, symbol, signal, sizing)
        return  # one trade per cycle - stop scanning once we've entered

    print(f"[main] No qualifying signal this cycle across {len(pairs)} pairs scanned.")


def execute_trade(exchange, symbol, signal, sizing):
    exchange_client.set_leverage(exchange, symbol, config.LEVERAGE)

    side = "buy" if signal.direction == "long" else "sell"

    entry_order = exchange_client.place_market_order_with_protection(
        exchange,
        symbol,
        side,
        sizing.amount_in_base,
        signal.stop_loss,
        signal.take_profit_1,
    )

    position = {
        "symbol": symbol,
        "direction": signal.direction,
        "entry_price": signal.entry_price,
        "stop_loss": signal.stop_loss,
        "take_profit_1": signal.take_profit_1,
        "take_profit_2": signal.take_profit_2,
        "amount_in_base": sizing.amount_in_base,
        "margin_used": sizing.margin_to_use,
        "notional_size": sizing.notional_size,
        "entry_order_id": entry_order.get("id"),
        "opened_at": time.time(),
    }
    state_manager.set_open_position(position)
    state_manager.increment_trades_today()
    notifier.notify_trade_entry(signal, symbol, sizing.margin_to_use, sizing.notional_size)


def manage_open_position(exchange, position):
    """
    Checks whether the open position has closed (stop or target hit) by checking
    current exchange position state, on the position's specific symbol.

    Also enforces MAX_POSITION_HOLD_HOURS: if the position has been open too long
    without hitting stop or target, force-close at market rather than hold indefinitely.
    """
    symbol = position["symbol"]

    try:
        positions = exchange.fetch_positions([symbol])
        live_position = next((p for p in positions if float(p.get("contracts", 0)) != 0), None)
    except Exception as e:
        print(f"[main] Error fetching position state for {symbol}: {e}")
        return

    if live_position is not None:
        hours_open = (time.time() - position["opened_at"]) / 3600
        if hours_open >= config.MAX_POSITION_HOLD_HOURS:
            print(f"[main] {symbol} position open {hours_open:.1f}h, exceeds {config.MAX_POSITION_HOLD_HOURS}h limit - force closing.")
            try:
                exchange_client.close_position_at_market(exchange, symbol, position["direction"], position["amount_in_base"])
                notifier.send_message(
                    f"⏱️ Force-closing {position['direction'].upper()} {symbol} position - "
                    f"open {hours_open:.1f}h with no stop/target hit (max hold time reached)."
                )
            except Exception as e:
                print(f"[main] Error force-closing {symbol} position: {e}")
                notifier.send_message(f"⚠️ Failed to force-close stuck {symbol} position: {e}")
        return

    try:
        pnl = exchange_client.get_realized_pnl(exchange, position)
    except ValueError:
        print(f"[main] {symbol} position closed but closing trade not yet indexed, retrying next cycle.")
        return

    current_capital = exchange_client.get_available_capital(exchange)
    was_win = pnl > 0

    state_manager.record_trade_result(was_win, pnl)
    state_manager.clear_open_position()
    notifier.notify_trade_close(
        symbol,
        position["direction"],
        "win" if was_win else "loss",
        pnl,
        current_capital,
    )


def run_forever():
    """
    The actual bot loop, extracted so it can be run in a background thread
    (see app.py) when hosted behind Render's free Web Service tier.
    """
    exchange = exchange_client.get_exchange()
    print(f"[main] BTC Scalper (multi-pair) starting. Demo mode: {config.USE_DEMO}")
    notifier.send_message(f"🤖 Multi-pair scalper started (demo={config.USE_DEMO}, leverage={config.LEVERAGE}x)")

    while True:
        try:
            run_cycle(exchange)
        except Exception as e:
            print(f"[main] ERROR in cycle: {e}")
            traceback.print_exc()
            notifier.send_message(f"⚠️ Bot error: {e}")

        time.sleep(POLL_INTERVAL_SECONDS)


if __name__ == "__main__":
    run_forever()
