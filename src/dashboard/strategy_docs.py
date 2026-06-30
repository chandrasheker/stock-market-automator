"""Dashboard helpers for strategy explanation and option chain."""

STRATEGY_LOGIC_MD = """
## How the Bot Chooses CE vs PE, Buy vs Sell

### Step 1 — Pick the instrument
Only instruments **enabled** in Settings are scanned (NIFTY 50, SENSEX, Crude Oil).

### Step 2 — Decide BUY vs SELL

| Market condition | Action | Why |
|-----------------|--------|-----|
| Strong trend (ADX > 20, EMAs aligned) | **BUY** option | Ride directional move |
| Range-bound (ADX < 22, price between Bollinger bands) | **SELL** OTM option | Collect premium + theta decay |
| High VIX / volatility | Favors **selling** premium | Higher premiums to collect |
| News strongly against direction | **Skip** | Avoid fighting the headline |

### Step 3 — Pick CE or PE

| Signal | Buy | Sell |
|--------|-----|------|
| **Bullish** (uptrend, breakout up, positive news) | **BUY CE** | **SELL PE** (bullish put sell) |
| **Bearish** (downtrend, breakout down, negative news) | **BUY PE** | **SELL CE** (bearish call sell) |

### Step 4 — Pick the strike
1. Find **ATM strike** = round(spot / strike_interval) × interval  
   - NIFTY: ₹50 steps | SENSEX: ₹100 | Crude: ₹50
2. **BUY** → 1 strike **OTM** (cheaper, higher % return on 20% target)  
   - Bullish → ATM + 1 interval **CE**  
   - Bearish → ATM − 1 interval **PE**
3. **SELL** → 2 strikes **OTM** (safer, more room before ITM)  
   - Sell CE above spot | Sell PE below spot
4. Pick the row from **live option chain** with valid LTP > 0

### Step 5 — Cost gate
Trade only runs if **gross profit at target ≥ 2× all charges** (STT, GST, brokerage, etc.)

---

## How Backtesting Works

Backtest does **not** use real historical option tick data (that data is expensive/unavailable for free).  
Instead it uses a **simulation model** on 1 year of **index daily candles**:

1. **Entry signals** — same rules as live (EMA, ADX, RSI, breakout, news filter)
2. **Premium estimate** — `spot × 0.5%` as base option price  
3. **Premium movement** — scales with underlying % move × leverage (3.5–5×)  
   - BUY CE gains when spot rises | BUY PE gains when spot falls  
   - SELL CE hurt when spot rises | SELL PE hurt when spot falls  
4. **Time decay** — ~2%/day theta proxy for sellers  
5. **Exit rules** — 20% target (buy) / 50% target (sell), stop loss, max hold days  
6. **Costs deducted** — STT 0.0625%, GST 18%, ₹20/order brokerage, exchange fees  

**Limitation:** Simulated premiums ≠ real option prices. Use paper trading to validate before going live.

**Daily auto-backtest** runs at 8:00 AM IST on fresh data + news. If **net P&L after costs is negative**, live trading is blocked.
"""

BACKTEST_METHODOLOGY_MD = """
### What data is used?
- **NIFTY/SENSEX/Crude** — 1 year daily OHLCV from Yahoo Finance / NSE cache
- **News** — live RSS at backtest run time (not historical news replay)
- **Option chain** — live snapshot when scanning; simulated premiums in historical walk-forward

### What is measured?
- Gross P&L per trade (before tax)
- Net P&L per trade (after STT, GST, brokerage, stamp duty, SEBI, exchange)
- Win rate, profit factor, max drawdown, Sharpe ratio
- Per-instrument and combined results

### How to interpret?
- **Net P&L > 0** → strategy approved for the day  
- **Crude oil** often shows low ₹ P&L because lot premiums are small in the model — you can **turn it off** in Settings  
- Always confirm with **paper trading** using real option LTP from the Option Chain page
"""
