"""
Central configuration for the BTC Scalper bot.
Every tunable knob lives here so behavior can be changed without touching logic files.
"""

import os

# ============================================================
# EXCHANGE
# ============================================================
EXCHANGE_ID = "bybit"
# Multi-pair scanning (like Phoenix) - instead of one fixed symbol, the bot dynamically
# pulls the top N pairs by 24h volume each day. High volume naturally filters out thin,
# illiquid, or scam-adjacent tokens without needing a manual blocklist.
TOP_PAIRS_COUNT = 50                # how many top-volume pairs to scan each cycle
PAIRS_REFRESH_HOURS = 24            # how often to refresh the top-pairs list
EXCLUDE_SYMBOL_MARKERS = ["UP/", "DOWN/", "BULL/", "BEAR/", "3L/", "3S/"]  # leveraged tokens - avoid, different risk profile
USE_DEMO = True                    # Bybit has a real demo trading environment (not just testnet)
                                    # Flip to False only after the 5-day demo evaluation passes.

API_KEY = os.getenv("BYBIT_API_KEY", "")
API_SECRET = os.getenv("BYBIT_API_SECRET", "")

# ============================================================
# CAPITAL & RISK MANAGEMENT
# ============================================================
STARTING_CAPITAL_USDT = 50.0       # informational only - real balance is always pulled live from exchange

LEVERAGE = 7                       # 5-10x range agreed on. 7x is the middle ground.
RISK_PER_TRADE_PCT = 0.02          # max % of CURRENT capital lost if stop-loss is hit (2% = conservative default)
MAX_MARGIN_PCT_OF_CAPITAL = 1.0    # cap on how much of capital can be used as margin on a single trade (1.0 = 100%)

ROI_TARGET_MIN_PCT = 0.05          # 5% ROI on margin used = minimum take-profit target
ROI_TARGET_MAX_PCT = 0.10          # 10% ROI on margin used = stretch take-profit target (TP2)

# Circuit breakers - these override everything else, including a "good" signal
MAX_DAILY_LOSS_PCT = 0.15          # stop trading for the day if cumulative daily loss hits 15% of day-start capital
MAX_CONSECUTIVE_LOSSES = 4         # stop trading for the day after N consecutive losing trades (regime-change guard)

# ============================================================
# COOLDOWN
# ============================================================
COOLDOWN_MINUTES_AFTER_WIN = 10    # minimum wait after a winning trade before next entry is considered
COOLDOWN_MINUTES_AFTER_LOSS = 20   # longer cooldown after a loss - reduces revenge-trading risk, forces fresh analysis
COOLDOWN_IS_FIXED = True           # NEVER make this adaptive to recent P&L - see notes in risk_manager.py

# ============================================================
# TRADE FREQUENCY GUARDRAILS
# ============================================================
MAX_TRADES_PER_DAY = 10            # hard ceiling even though "unlimited" was the original ask -
                                    # this exists purely to bound worst-case fee drag and force discipline;
                                    # raise it later once live data justifies it.

# ============================================================
# TRADING HOURS
# ============================================================
# Bot only opens NEW positions within this window, in the timezone below.
# Open positions are still monitored and can still close (stop/target hit) outside
# this window - only new entries are blocked.
TIMEZONE = "Africa/Lagos"          # UTC+1, no DST - matches your local time
TRADING_START_HOUR = 0             # removed for evaluation phase - bot is unattended,
TRADING_END_HOUR = 24              # gather signals across all hours to speed up data collection

# ============================================================
# TIMEFRAMES
# ============================================================
TREND_TIMEFRAME_HIGH = "4h"        # macro trend direction
TREND_TIMEFRAME_MID = "1h"         # momentum alignment with macro trend
ENTRY_TIMEFRAME = "15m"            # structure / pullback level
TRIGGER_TIMEFRAME = "5m"           # precise entry trigger

CANDLE_LOOKBACK = 200              # candles to fetch per timeframe for indicator calculation

# ============================================================
# SIGNAL / INDICATOR SETTINGS
# ============================================================
EMA_FAST = 20
EMA_SLOW = 50
EMA_MACRO = 100

RSI_PERIOD = 14
RSI_PULLBACK_ZONE = (40, 55)       # RSI range considered a healthy pullback (not overbought/oversold extreme)

ATR_PERIOD = 14
ATR_STOP_MULTIPLIER = 1.5          # stop-loss = structure level minus (ATR * multiplier), never tighter than this

VOLUME_MA_PERIOD = 20
VOLUME_CONFIRMATION_MULTIPLIER = 1.2   # entry candle volume must exceed X * 20-period volume average

MIN_RISK_REWARD_RATIO = 2.0        # target distance must be at least 2x the stop distance to qualify as a signal
MIN_BTC_ORDER_SIZE = 0.001         # Bybit's minimum tradeable BTC perpetual amount
MAX_POSITION_HOLD_HOURS = 3        # force-close a position if neither stop nor target hit within this window -
                                    # the 5m/volume trigger that justified entry is time-bound; if the move
                                    # hasn't happened by now, the original setup has effectively expired

# ============================================================
# FEES (Bybit perpetual taker fee, adjust if your account tier differs)
# ============================================================
TAKER_FEE_PCT = 0.00055            # 0.055% per side, standard Bybit perpetual taker fee (verify against your account)

# ============================================================
# STATE STORAGE (Redis - reusing Phoenix conventions)
# ============================================================
REDIS_URL = os.getenv("REDIS_URL", "")   # Upstash Redis URL, same pattern as phoenix-bot-2
REDIS_KEY_PREFIX = "btcscalper"

# ============================================================
# TELEGRAM NOTIFICATIONS
# ============================================================
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

# ============================================================
# EVALUATION MODE
# ============================================================
# During the agreed 3-5 day demo evaluation, every trade decision AND every rejected
# signal gets logged with full reasoning - this is what makes the demo results meaningful
# rather than anecdotal.
EVALUATION_MODE = True
EVALUATION_LOG_PATH = "logs/evaluation_log.jsonl"
