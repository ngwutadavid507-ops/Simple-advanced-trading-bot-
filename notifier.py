"""Telegram notifications - trade alerts, rejections (optional, verbose), daily summaries."""

import requests
import config


def send_message(text: str):
    if not config.TELEGRAM_BOT_TOKEN or not config.TELEGRAM_CHAT_ID:
        print(f"[notifier] Telegram not configured, message was:\n{text}")
        return

    url = f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}/sendMessage"
    try:
        requests.post(url, data={
            "chat_id": config.TELEGRAM_CHAT_ID,
            "text": text,
            "parse_mode": "Markdown",
        }, timeout=10)
    except Exception as e:
        print(f"[notifier] Telegram send failed: {e}")


def notify_trade_entry(signal, symbol, margin_used, notional_size):
    text = (
        f"🎯 *Entry: {signal.direction.upper()} {symbol}*\n"
        f"Entry: `{signal.entry_price}`\n"
        f"Stop: `{signal.stop_loss}` ({signal.stop_distance_pct:.2%})\n"
        f"TP1: `{signal.take_profit_1}`  TP2: `{signal.take_profit_2}`\n"
        f"R:R `{signal.risk_reward_ratio}`\n"
        f"Margin used: `${margin_used}`  Notional: `${notional_size}`\n"
        f"Leverage: `{config.LEVERAGE}x`\n"
        f"_{signal.reasoning}_"
    )
    send_message(text)


def notify_trade_close(symbol, direction, outcome, pnl_usdt, new_capital):
    emoji = "✅" if outcome == "win" else "❌"
    text = (
        f"{emoji} *Trade closed: {direction.upper()} {symbol}*\n"
        f"Outcome: `{outcome}`\n"
        f"PnL: `${pnl_usdt:+.2f}`\n"
        f"Capital now: `${new_capital:.2f}`"
    )
    send_message(text)


def notify_circuit_breaker(reason):
    send_message(f"🛑 *Trading halted*\nReason: `{reason}`")


def notify_daily_summary(trades_today, wins, losses, day_pnl, ending_capital):
    win_rate = (wins / trades_today * 100) if trades_today else 0
    text = (
        f"📊 *Daily Summary*\n"
        f"Trades: `{trades_today}`  W/L: `{wins}/{losses}`  Win rate: `{win_rate:.1f}%`\n"
        f"Day PnL: `${day_pnl:+.2f}`\n"
        f"Capital: `${ending_capital:.2f}`"
    )
    send_message(text)
