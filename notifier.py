"""Telegram notifications - trade alerts, rejections (optional, verbose), daily/monthly summaries."""

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


def notify_monthly_summary(month_label, trades, wins, losses, total_pnl, start_capital, end_capital):
    """
    Full end-of-month breakdown: every closed trade for the month, aggregate
    win rate, total PnL, and PnL as a percentage of what capital started the
    month at - the actual "what did we achieve" number.
    """
    win_rate = (wins / len(trades) * 100) if trades else 0
    pnl_pct = (total_pnl / start_capital * 100) if start_capital > 0 else 0

    lines = [
        f"📅 *Monthly Summary — {month_label}*",
        f"Total trades: `{len(trades)}`  W/L: `{wins}/{losses}`  Win rate: `{win_rate:.1f}%`",
        f"Starting capital: `${start_capital:.2f}`",
        f"Ending capital: `${end_capital:.2f}`",
        f"Total PnL: `${total_pnl:+.2f}` (`{pnl_pct:+.2f}%`)",
        "",
        "*Trade log:*",
    ]

    for t in trades:
        emoji = "✅" if t["was_win"] else "❌"
        lines.append(f"{emoji} {t['symbol']} {t['direction'].upper()} — `${t['pnl']:+.2f}`")

    text = "\n".join(lines)

    # Telegram caps messages at 4096 characters - split into chunks if the trade
    # log is long enough to exceed that.
    if len(text) <= 4000:
        send_message(text)
    else:
        header = "\n".join(lines[:7])
        send_message(header)
        trade_lines = lines[7:]
        chunk = ""
        for line in trade_lines:
            if len(chunk) + len(line) + 1 > 4000:
                send_message(chunk)
                chunk = ""
            chunk += line + "\n"
        if chunk:
            send_message(chunk)
