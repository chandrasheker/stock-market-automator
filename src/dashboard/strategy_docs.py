"""Dashboard helpers for strategy explanation and option chain."""

STRATEGY_LOGIC_MD = """
## Trade Pipeline (13 Steps)

Every live trade follows this order:

1. **Fetch live data** — spot, option chain, VIX, news
2. **Trading hours** — skip if market closed
3. **Time filter** — no entries 9:15–9:30 or after 3:15 PM IST
4. **Liquidity** — LTP ≥ ₹15, OI ≥ 10k (NIFTY), spread ≤ 1%
5. **Trend / range** — ADX, EMAs, Bollinger bands
6. **Volatility** — VIX level + IV percentile ≥ 50% for sells
7. **News / events** — block sells near RBI, Fed, budget, expiry headlines
8. **Strategy** — sell OTM premium in range; buy only in strong trends
9. **Strike** — delta-based (sell 0.15–0.25δ, buy 0.30–0.50δ)
10. **Position size** — max 2% risk per trade
11. **Costs** — gross profit must be ≥ 2× STT/GST/brokerage
12. **Manage** — monitor every 30s
13. **Exit** — target, stop-loss, trailing stop, EOD force close at 3:20 PM

---

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

### Step 4 — Pick the strike (delta-based)
1. **SELL** → target delta **0.15–0.25** (OTM, safer premium collection)
2. **BUY** → target delta **0.30–0.50** (directional with reasonable premium)
3. Delta is computed with the **real days-to-expiry** from the live chain, so
   strike choice adapts as expiry approaches
4. Fallback: fixed OTM distance if IV/delta unavailable
5. **Liquidity gate** — reject if spread > 1%, OI too low, or LTP < ₹15

> **Crude Oil (MCX):** more volatile than indices, so it uses looser ADX and an
> ATR-based volatility gate (India VIX is ignored for commodities). MCX hours are
> 9 AM–11:30 PM IST and require the MCX segment enabled on your Zerodha account.

### Step 5 — Cost gate
Trade only runs if **gross profit at target ≥ 2× all charges** (STT, GST, brokerage, etc.)

### Step 6 — Rank by edge (the "magic")
Each candidate gets a **0–100 edge score** and a **verdict** (STRONG / GOOD / FAIR / AVOID):

- **Probability of Profit (POP)** — from option delta (sold OTM ≈ 1 − delta)
- **Expected Value (₹)** — `POP × net_win + (1−POP) × net_loss`, already after all costs
- **IV percentile** — theta edge for sellers (higher = better)
- **OI/PCR + news alignment**, risk/reward, and liquidity

Only **positive-EV** ideas are shown on the **Trade Ideas** page, best first. If nothing
clears the bar, the bot **stays in cash** — that's the core loss-protection rule.

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
