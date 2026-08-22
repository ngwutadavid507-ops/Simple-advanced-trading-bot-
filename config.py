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
MAX_MARGIN_PCT_OF_CAPITAL = 0.95

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
ATR_STOP_MULTIPLIER = 1.5
BREAKOUT_ATR_STOP_MULTIPLIER = 2.5

VOLUME_MA_PERIOD = 20
VOLUME_CONFIRMATION_MULTIPLIER = 1.0

PULLBACK_ATR_MULTIPLIER = 0.5
HOLD_ATR_MULTIPLIER = 0.2

MIN_RISK_REWARD_RATIO = 1.5        # lowered slightly from 2.0 - low-confidence tiers target smaller ROI,
                                    # so the ratio needed to still make sense is naturally a bit lower too

# ============================================================
# MARKET REGIME DETECTION (still HARD requirements - not scored)
# ============================================================
# Trend alignment and "not ranging/not extreme volatility" remain non-negotiable -
# these are the guardrails that keep this from becoming a coin flip. Everything
# else below (volume, RSI quality, pullback tightness, hold strength) is SCORED
# instead of gated, so a partial match can still trade at a lower confidence tier.
ATR_AVG_PERIOD = 50
EXTREME_VOLATILITY_RATIO = 2.0
RANGING_EMA_BAND_ATR_MULTIPLIER = 0.5
STRONG_TREND_ATR_MULTIPLIER = 2.5
PARABOLIC_RSI_EXHAUSTION_MAX = 85

# ============================================================
# CONFIDENCE SCORING SYSTEM
# ============================================================
# Each factor below contributes points toward a total confidence score (0-100).
# The total score determines which tier a signal falls into, which in turn sets
# the ROI target and position size - NOT whether the trade happens at all.
# Stop-loss risk management is identical across all tiers - only target/size scale.

SCORE_WEIGHTS = {
    "volume_strength": 25,      # how far above the volume MA the breakout candle is
    "rsi_quality": 20,          # how close RSI is to the ideal middle of the healthy zone
    "pullback_tightness": 20,   # (normal_trend only) how close the pullback was to EMA20
    "hold_strength": 20,        # how cleanly the confirmation candle held (little to no give-back)
    "risk_reward": 15,          # actual R:R achieved vs. the minimum required
}

# Tier cutoffs (score out of 100) and what each tier trades as
CONFIDENCE_TIERS = {
    "high": {
        "min_score": 70,
        "roi_target_min_pct": 0.05,
        "roi_target_max_pct": 0.10,
        "position_size_multiplier": 1.0,   # full risk-based size
    },
    "medium": {
        "min_score": 45,
        "roi_target_min_pct": 0.02,
        "roi_target_max_pct": 0.03,
        "position_size_multiplier": 0.5,   # half size
    },
    "low": {
        "min_score": 0,                    # catches anything that passed the hard gates at all
        "roi_target_min_pct": 0.005,
        "roi_target_max_pct": 0.01,
        "position_size_multiplier": 0.25,  # quarter size - even a tiny profit after fees counts as a win here
    },
}

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
