"""
Main loop. Order of operations on every cycle, deliberately in this sequence:

  1. Circuit breakers checked FIRST - can halt the day before anything else runs
  2. Cooldown checked - blocks entries even if a signal would otherwise qualify
  3. Daily trade limit checked
  4. Trading hours checked - no NEW entries outside 8am-8pm local time
  5. Only then: fetch data, evaluate signal
  6. If signal passes ALL of the above: size the position, place the trade
     WITH stop-loss/take-profit attached directly on the entry order
  7. Monitor open position until it closes (stop/target hit OR max hold time
     exceeded - see MAX_POSITION_HOLD_HOURS), record the result, start cooldown
  8. Once per day, shortly after the trading window closes, send a Telegram summary

This ordering matters: risk controls are checked before signal quality, never after -
a great signal never overrides a circuit breaker, an active cooldown, or trading hours.
"""

import time
import traceback
from datetime import datetime
from zoneinfo import ZoneInfo

import config
import exchange_client
import signal_engine
import risk_manager
import state_manager
import notifier


POLL_INTERVAL_SECONDS = 60  # how often the main loop checks conditions when flat (no open position)
_TZ = ZoneInfo(config.TIMEZONE)


def is_within_trading_hours() -> bool:
    local_now = datetime.now(_TZ)
    return config.TRADING_START_HOUR <= local_now.hour < config.TRADING_END_HOUR


def maybe_send_daily_summary(current_capital):
    """
    Sends the daily summary exactly once, after the trading window closes each day.
    Runs regardless of whether a position is open, so the summary still fires even
    if the day happened to end mid-trade.
    """
    local_now = datetime.now(_TZ)
    past_close = local_now.hour >= config.TRADING_END_HOUR

    if past_close and not state_manager.summary_already_sent_today():
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
        # Open positions are monitored and allowed to close even outside trading hours -
        # only NEW entries are blocked by the hours check below.
        manage_open_position(exchange, open_position)
        return

    if not is_within_trading_hours():
        print(f"[main] Outside trading hours ({config.TRADING_START_HOUR}:00-{config.TRADING_END_HOUR}:00 "
              f"{config.TIMEZONE}), no new entries.")
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

    # --- 4. Fetch data + evaluate signal ---
    timeframes = exchange_client.get_all_timeframes(exchange)
    signal = signal_engine.evaluate_setup(
        timeframes["4h"], timeframes["1h"], timeframes["15m"], timeframes["5m"]
    )

    if signal is None:
        print("[main] No qualifying signal this cycle.")
        return

    # --- 5. Size and place the trade ---
    sizing = risk_manager.position_size(current_capital, signal.stop_distance_pct, config.LEVERAGE, signal.entry_price)
    if not sizing.approved:
        print(f"[main] Position sizing rejected: {sizing.reason}")
        return

    fee_estimate = risk_manager.estimate_fee_cost(sizing.notional_size)
    print(f"[main] Signal approved: {signal.reasoning}")
    print(f"[main] Sizing: margin=${sizing.margin_to_use} notional=${sizing.notional_size} "
          f"amount={sizing.amount_in_base} BTC ({sizing.reason}) est_fees=${fee_estimate:.3f}")

    execute_trade(exchange, signal, sizing)


def execute_trade(exchange, signal, sizing):
    exchange_client.set_leverage(exchange, config.LEVERAGE)

    side = "buy" if signal.direction == "long" else "sell"

    entry_order = exchange_client.place_market_order_with_protection(
        exchange,
        side,
        sizing.amount_in_base,
        signal.stop_loss,
        signal.take_profit_1,
    )

    position = {
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
    notifier.notify_trade_entry(signal, sizing.margin_to_use, sizing.notional_size)


def manage_open_position(exchange, position):
    """
    Checks whether the open position has closed (stop or target hit) by checking
    current exchange position state. If closed, records the result and clears state.

    Also enforces MAX_POSITION_HOLD_HOURS: if the position has been open too long
    without hitting stop or target, the 5m/volume trigger that justified entry has
    effectively expired - we force-close at market rather than hold indefinitely.
    """
    try:
        positions = exchange.fetch_positions([config.SYMBOL])
        live_position = next((p for p in positions if float(p.get("contracts", 0)) != 0), None)
    except Exception as e:
        print(f"[main] Error fetching position state: {e}")
        return

    if live_position is not None:
        # Still open on the exchange - check if it's been open too long
        hours_open = (time.time() - position["opened_at"]) / 3600
        if hours_open >= config.MAX_POSITION_HOLD_HOURS:
            print(f"[main] Position open {hours_open:.1f}h, exceeds {config.MAX_POSITION_HOLD_HOURS}h limit - force closing.")
            try:
                exchange_client.close_position_at_market(exchange, position["direction"], position["amount_in_base"])
                notifier.send_message(
                    f"⏱️ Force-closing {position['direction'].upper()} BTC position - "
                    f"open {hours_open:.1f}h with no stop/target hit (max hold time reached)."
                )
            except Exception as e:
                print(f"[main] Error force-closing position: {e}")
                notifier.send_message(f"⚠️ Failed to force-close stuck position: {e}")
        return

    # Position is closed on the exchange - pull real fill data to compute actual PnL
    try:
        pnl = exchange_client.get_realized_pnl(exchange, position)
    except ValueError:
        # Closing trade not indexed by the exchange API yet - try again next cycle
        # rather than record a wrong result now.
        print("[main] Position closed but closing trade not yet indexed, retrying next cycle.")
        return

    current_capital = exchange_client.get_available_capital(exchange)
    was_win = pnl > 0

    state_manager.record_trade_result(was_win, pnl)
    state_manager.clear_open_position()
    notifier.notify_trade_close(
        position["direction"],
        "win" if was_win else "loss",
        pnl,
        current_capital,
    )


def run_forever():
    """
    The actual bot loop, extracted so it can be run in a background thread
    (see app.py) when hosted behind Render's free Web Service tier, which
    requires something bound to a port - a plain script like this alone
    won't satisfy Render's health check.
    """
    exchange = exchange_client.get_exchange()
    print(f"[main] BTC Scalper starting. Demo mode: {config.USE_DEMO}")
    notifier.send_message(f"🤖 BTC Scalper started (demo={config.USE_DEMO}, leverage={config.LEVERAGE}x)")

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
