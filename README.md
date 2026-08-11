# BTC Scalper Bot

Automated BTC perpetual futures bot. Trend-filtered pullback entries targeting small,
frequent moves (5-10% ROI per trade via leverage) with strict risk-per-trade sizing,
daily circuit breakers, fixed cooldowns, and locked trading hours.

## Strategy summary (agreed design)

- **Asset**: BTC only, perpetual futures (Bybit)
- **Leverage**: 7x default (config: 5-10x range)
- **Position sizing**: risk-based, NOT "full capital always" - a stop-loss hit costs a
  fixed 2% of current capital regardless of how wide the stop needs to be. Full capital
  is used as margin only when the stop is tight enough that this stays within the 2% risk cap.
- **Entry logic**: 4h/1h trend alignment -> 15m pullback to EMA -> 5m momentum trigger
  with volume confirmation -> minimum 2:1 reward:risk required
- **Cooldown**: 10 min after a win, 20 min after a loss - fixed, not adaptive to P&L
- **Circuit breakers**: stop trading for the day at 15% daily loss OR 4 consecutive losses
- **Trade limit**: capped at 10/day initially (raise later based on real results, not before)
- **Trading hours**: only opens new positions 8:00 AM - 8:00 PM (Africa/Lagos time)

## Setup

```bash
pip install -r requirements.txt --break-system-packages
