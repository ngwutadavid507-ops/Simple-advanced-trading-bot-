"""
Central configuration for the BTC Scalper bot.
Every tunable knob lives here so behavior can be changed without touching logic files.
"""

import os

# ============================================================
# EXCHANGE
# ============================================================
EXCHANGE_ID = "bybit"
USE_DEMO = True

API_KEY = os.getenv("BYBIT_API_KEY", "")
API_SECRET = os.getenv("BYBIT_API_SECRET", "")

# ============================================================
# CAPITAL & RISK MANAGEMENT
# ============================================================
STARTING_CAPITAL_USDT = 50.0

LEVERAGE = 7
RISK_PER_TRADE_PCT = 0.02
MAX_MARGIN_PCT_OF_CAPITAL = 0.95   # leave a 5% buffer - using literally 100% of "free" balance as margin
                                    # was getting rejected by Bybit ("insufficient available balance")

ROI_TARGET_MIN_PCT = 0.05
ROI_TARGET_MAX_PCT = 0.10

MIN_BTC_ORDER_SIZE = 0.001

MAX_DAILY_LOSS_PCT = 0.15
MAX_CONSECUTIVE_LOSSES = 4

# ============================================================
# COOLDOWN
# ============================================================
COOLDOWN_MINUTES_AFTER_WIN = 10
COOLDOWN_MINUTES_AFTER_LOSS = 20
COOLDOWN_IS_FIXED = True

# ============================================================
# TRADE FREQUENCY GUARDRAILS
# ============================================================
MAX_TRADES_PER_DAY = 10
MAX_POSITION_HOLD_HOURS = 3

# ============================================================
# TRADING HOURS
# ============================================================
TIMEZONE = "Africa/Lagos"
TRADING_START_HOUR = 0
TRADING_END_HOUR = 24

# ============================================================
# MULTI-PAIR SCANNING
# ============================================================
TOP_PAIRS_COUNT = 50
PAIRS_REFRESH_HOURS = 24

EXCLUDE_SYMBOL_MARKERS = ["UP/", "DOWN/", "BULL/", "BEAR/", "3L/", "3S/"]
EXCLUDE_SYMBOLS = []

# ============================================================
# TIMEFRAMES
# ============================================================
TREND_TIMEFRAME_HIGH = "4h"
TREND_TIMEFRAME_MID = "1h"
ENTRY_TIMEFRAME = "15m"
TRIGGER_TIMEFRAME = "5m"

CANDLE_LOOKBACK = 200

# ============================================================
# SIGNAL / INDICATOR SETTINGS
# ============================================================
EMA_FAST = 20
EMA_SLOW = 50
EMA_MACRO = 100

RSI_PERIOD = 14
RSI_PULLBACK_ZONE = (40, 55)

ATR_PERIOD = 14
ATR_STOP_MULTIPLIER = 1.5          # stop multiplier for NORMAL TREND (pullback) entries
BREAKOUT_ATR_STOP_MULTIPLIER = 2.5 # wider stop multiplier for PARABOLIC (continuation) entries -
                                    # chasing momentum is inherently riskier than entering on a pullback,
                                    # so the stop needs more room to avoid being clipped by normal noise

VOLUME_MA_PERIOD = 20
VOLUME_CONFIRMATION_MULTIPLIER = 1.2

MIN_RISK_REWARD_RATIO = 2.0

PULLBACK_ATR_MULTIPLIER = 0.5
HOLD_ATR_MULTIPLIER = 0.2

# ============================================================
# MARKET REGIME DETECTION
# ============================================================
# Four regimes, detected per pair per cycle using indicators already computed:
#   - extreme_volatility: skip entirely, conditions too erratic to trust any entry logic
#   - ranging: skip entirely, no directional edge without a fundamentally different (untested) approach
#   - parabolic (strong trend): continuation entry, no pullback required, wider stop
#   - normal_trend: existing pullback + hold-confirmation entry (unchanged)
ATR_AVG_PERIOD = 50                     # baseline period for measuring "is current volatility unusual"
EXTREME_VOLATILITY_RATIO = 2.0          # current ATR vs its own 50-period average - above this, skip entirely
RANGING_EMA_BAND_ATR_MULTIPLIER = 0.5   # if EMA20/50/100 are bunched within this many ATRs of each other, skip (no real trend)
STRONG_TREND_ATR_MULTIPLIER = 2.5       # price extended this many ATRs beyond EMA20 = parabolic regime
PARABOLIC_RSI_EXHAUSTION_MAX = 85       # in parabolic mode, only reject if RSI is at extreme exhaustion (not the
                                         # normal 65 pullback-zone ceiling, since parabolic moves run RSI hot by design)

# ============================================================
# FEES
# ============================================================
TAKER_FEE_PCT = 0.00055

# ============================================================
# STATE STORAGE (Redis)
# ============================================================
REDIS_URL = os.getenv("REDIS_URL", "")
REDIS_KEY_PREFIX = "btcscalper"

# ============================================================
# TELEGRAM NOTIFICATIONS
# ============================================================
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

# ============================================================
# EVALUATION MODE
# ============================================================
EVALUATION_MODE = True
