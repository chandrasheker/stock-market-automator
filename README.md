# Indian Options Trading Automator

A personal, private algorithmic trading system for Indian F&O markets. Scans NIFTY 50, SENSEX, and Crude Oil options using technical analysis, open-interest data, news sentiment, and IV analysis — then executes trades via Zerodha Kite Connect when confidence is high enough for a 20% profit target per lot.

> **Disclaimer:** This software is for personal educational use only. Options trading carries substantial risk of loss. Past performance does not guarantee future results. No trading system can guarantee profits. Always start with paper trading and only risk capital you can afford to lose.

## Features

- **Multi-instrument scanning** — NIFTY 50, SENSEX, and Crude Oil options
- **Composite signal engine** — Combines 4 strategies with weighted scoring:
  - Momentum & breakout (RSI, MACD, EMA, volume spikes)
  - Open Interest analysis (PCR, max pain, OI changes)
  - News sentiment (RSS feeds + optional NewsAPI)
  - IV/VIX regime detection
- **Options buying AND selling** — Buys directional options (20% target) or sells OTM premium in range-bound markets
- **Full cost modeling** — STT (0.0625%), GST (18%), brokerage (₹20/order), exchange charges, stamp duty, SEBI fees
- **Cost-aware trade gate** — Skips trades where charges would eat the profit
- **Daily auto-backtest** — Runs at 8:00 AM IST, blocks trading if net P&L is negative
- **News-aligned trading** — Matches live signals against latest RSS/news sentiment per instrument
- **Paper trading** — Default mode for safe testing before going live
- **Zerodha Kite Connect** — Full integration for live order execution
- **Backtesting** — Validate strategies on historical data
- **Streamlit dashboard** — User-friendly web UI for monitoring and control
- **TradingView integration** — Embedded charts + webhook alerts that trigger the sell-only signal engine
- **Private & local** — All data stored locally on your Oracle Cloud VM

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│                   Streamlit Dashboard                    │
├─────────────────────────────────────────────────────────┤
│                    Trading Bot (main.py)                 │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐            │
│  │ Scanner  │→ │ Signal   │→ │ Risk     │→ Execute   │
│  │ (60s)    │  │ Engine   │  │ Manager  │            │
│  └──────────┘  └──────────┘  └──────────┘            │
├─────────────────────────────────────────────────────────┤
│  Data Layer          │  Analysis          │  Execution  │
│  ├─ Historical (NSE) │  ├─ Technical       │  ├─ Paper  │
│  ├─ Option Chain     │  ├─ OI/PCR/IV      │  └─ Live   │
│  ├─ News/RSS         │  └─ Sentiment      │  (Kite)    │
│  └─ Live WebSocket   │                    │            │
├─────────────────────────────────────────────────────────┤
│              SQLite Database (local, private)            │
└─────────────────────────────────────────────────────────┘
```

## Quick Start (Oracle Cloud VM)

### 1. Setup

```bash
git clone <your-repo-url>
cd stock-market-automator
chmod +x scripts/setup.sh
./scripts/setup.sh
```

### 2. Configure Zerodha API

1. Go to [Kite Connect Developer Console](https://kite.trade/connect/login)
2. Create an app and get your `api_key` and `api_secret`
3. Subscribe to Kite Connect (₹500/month for historical + WebSocket data)
4. Edit `.env`:

```env
KITE_API_KEY=your_api_key
KITE_API_SECRET=your_api_secret
TRADING_MODE=paper
CAPITAL=500000
PROFIT_TARGET_PCT=20.0
STOP_LOSS_PCT=15.0
```

### 3. Authenticate with Zerodha

```bash
source venv/bin/activate
python -m src.main login
# Opens browser → login → paste request_token
```

### 4. Download Historical Data

```bash
python -m src.main download
```

### 5. Scan for Opportunities

```bash
python -m src.main scan
```

### 6. Launch Dashboard

```bash
streamlit run src/dashboard/app.py --server.port 8501 --server.address 0.0.0.0
```

Access at `http://<your-vm-ip>:8501`

### 7. Start the Trading Bot

```bash
# Paper trading (recommended first)
python -m src.main run

# For live trading, set TRADING_MODE=live in .env
```

## How the 20% Profit Logic Works

1. **Scan** all enabled instruments every 60 seconds during market hours
2. **Score** each instrument using 4 weighted strategies (confidence 0-100%)
3. **Filter** only signals with ≥65% confidence
4. **Select** optimal OTM strike based on direction and OI
5. **Calculate** entry price, target (+20% premium), and stop loss (-15%)
6. **Risk check** — position size, daily loss limit, max positions
7. **Execute** paper or live order via Kite Connect
8. **Monitor** positions every 30 seconds for target/SL/time exit

## Strategy Details

| Strategy | Weight | Signals Used |
|----------|--------|-------------|
| Momentum Breakout | 30% | EMA crossover, RSI, MACD, volume spikes, price breakouts |
| OI Analysis | 35% | Put-Call Ratio, max pain, OI change direction, support/resistance strikes |
| News Sentiment | 20% | RSS news sentiment (VADER + TextBlob), keyword matching per instrument |
| IV/VIX Rank | 15% | India VIX levels, implied volatility regime |

## Commands

| Command | Description |
|---------|-------------|
| `python -m src.main login` | Authenticate with Zerodha |
| `python -m src.main download` | Download historical data |
| `python -m src.main scan` | One-time market scan |
| `python -m src.main backtest` | Backtest all instruments (net of taxes) |
| `python -m src.main daily-backtest` | Run daily backtest + news alignment now |
| `python -m src.main run` | Start bot (auto daily backtest at 8:00 AM) |
| `python -m src.main webhook` | Start TradingView webhook server (port 8765) |
| `streamlit run src/dashboard/app.py` | Launch web dashboard |

## TradingView Integration

The dashboard includes a **TradingView** page with embedded NIFTY / SENSEX / Crude charts. You can route Pine Script alerts into the bot via webhooks:

1. Set in `.env`:
   ```env
   TRADINGVIEW_WEBHOOK_SECRET=your-long-random-secret
   WEBHOOK_PORT=8765
   PUBLIC_WEBHOOK_URL=http://YOUR_VM_IP:8765
   ```
2. Start the webhook server: `python -m src.main webhook`
3. In TradingView, create an alert with webhook URL: `http://YOUR_VM_IP:8765/webhook/tradingview`
4. Alert message JSON (example):
   ```json
   {"secret":"your-long-random-secret","instrument":"nifty50","action":"SCAN","source":"tradingview"}
   ```

**Actions:** `SCAN` runs the sell scanner; `SELL_CE` / `SELL_PE` only execute if the bot agrees; `EXIT` closes open positions. Buy actions are blocked in profit mode.

See `examples/tradingview_alert.pine` for a ready-made Pine Script template.

## Running as a Service (systemd)

Create `/etc/systemd/system/trading-bot.service`:

```ini
[Unit]
Description=Indian Options Trading Automator
After=network.target

[Service]
Type=simple
User=ubuntu
WorkingDirectory=/home/ubuntu/stock-market-automator
ExecStart=/home/ubuntu/stock-market-automator/venv/bin/python -m src.main run
Restart=always
RestartSec=10
Environment=PATH=/home/ubuntu/stock-market-automator/venv/bin

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable trading-bot
sudo systemctl start trading-bot
```

## Security Notes

- Never commit `.env` or `data/.access_token` to git
- Kite access tokens expire daily — re-login each morning before market open
- Keep your VM firewall configured to restrict dashboard access
- This is designed for single-user personal use only

## Risk Management Defaults

| Parameter | Default | Description |
|-----------|---------|-------------|
| Profit Target | 20% | Exit when premium gains 20% |
| Stop Loss | 15% | Exit when premium drops 15% |
| Max Risk/Trade | 2% | Of total capital |
| Max Daily Loss | 5% | Kill trading for the day |
| Max Positions | 3 | Concurrent open trades |
| Time Exit | 4 hours | Close stale positions |

## Open Firewall Port (Oracle Cloud)

In Oracle Cloud Console → Networking → Security Lists → Add Ingress Rule:
- Source: your IP
- Port: 8501 (dashboard)
- Port: 8765 (TradingView webhook, optional)
- Protocol: TCP

## License

Private personal use only. Not for redistribution.
