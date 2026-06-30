"""Streamlit dashboard for personal trading automator."""

from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
import streamlit.components.v1 as components

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from src.analysis.signal_engine import SignalEngine
from src.auth.kite_auth import KiteAuth
from src.backtest.engine import BacktestEngine
from src.config import get_env, get_yaml_config
from src.toggles import (
    apply_sell_only_defaults,
    get_buy_warning_text,
    get_sell_recommendation_text,
    is_any_buy_enabled,
    is_instrument_enabled,
    load_toggles,
    save_toggles,
)
from src.dashboard.strategy_docs import BACKTEST_METHODOLOGY_MD, STRATEGY_LOGIC_MD
from src.integrations.tradingview import (
    build_chart_html,
    get_pine_script_example,
    get_tv_symbols,
    get_webhook_message_template,
)
from src.data.database import DailyPnL, Trade, TradeSignal, get_session, init_db
from src.data.historical import HistoricalDataFetcher
from src.data.news_fetcher import NewsFetcher
from src.data.option_chain_live import LiveOptionChainService
from src.risk.manager import RiskManager

# Singleton chain service for live updates across reruns
if "chain_service" not in st.session_state:
    st.session_state.chain_service = LiveOptionChainService()

st.set_page_config(
    page_title="Options Trading Automator",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="expanded",
)

init_db()


def main():
    env = get_env()
    config = get_yaml_config()

    st.sidebar.title("Trading Automator")
    st.sidebar.markdown("**Personal Use Only**")

    page = st.sidebar.radio(
        "Navigation",
        [
            "Dashboard", "Trade Ideas", "Option Chain", "TradingView", "Live Signals",
            "Strategy Logic", "Positions", "News", "Backtest", "Daily Report", "Settings",
        ],
    )

    st.sidebar.divider()
    from src.utils.clock import ist_now
    from src.filters.time_window import TimeWindowFilter
    _now = ist_now()
    _tw = TimeWindowFilter()
    _open = _tw.is_market_open("nifty50")
    st.sidebar.markdown(
        f"**IST:** {_now.strftime('%a %H:%M:%S')} · "
        f"{'🟢 Market OPEN' if _open else '🔴 Market closed'}"
    )

    profit_mode = config.get("profit_mode", {}).get("enabled", False)
    if profit_mode:
        st.sidebar.success("Profit Mode: SELL premium only")
    mode_color = "🟢" if env.trading_mode == "paper" else "🔴"
    st.sidebar.markdown(f"**Mode:** {mode_color} {env.trading_mode.upper()}")
    st.sidebar.markdown(f"**Capital:** ₹{env.capital:,.0f}")
    st.sidebar.markdown(f"**Profit Target:** {env.profit_target_pct}%")

    if page == "Dashboard":
        show_dashboard(env)
    elif page == "Trade Ideas":
        show_trade_ideas(env, config)
    elif page == "Option Chain":
        show_option_chain(config)
    elif page == "TradingView":
        show_tradingview(env, config)
    elif page == "Live Signals":
        show_signals()
    elif page == "Strategy Logic":
        show_strategy_logic()
    elif page == "Positions":
        show_positions()
    elif page == "News":
        show_news()
    elif page == "Backtest":
        show_backtest()
    elif page == "Daily Report":
        show_daily_report()
    elif page == "Settings":
        show_settings(env, config)


def show_tradingview(env, config):
    st.title("TradingView Charts & Alerts")
    tv_cfg = config.get("tradingview", {})
    if not tv_cfg.get("enabled", True):
        st.warning("TradingView integration is disabled in settings.yaml")
        return

    st.markdown(
        "Embedded **TradingView** charts for NIFTY, SENSEX, and Crude Oil. "
        "Use Pine Script alerts to send webhooks into this automator — "
        "alerts trigger the same **sell-only** signal engine and risk checks as the bot."
    )

    symbols = get_tv_symbols()
    inst_names = {
        "nifty50": "NIFTY 50",
        "sensex": "SENSEX",
        "crude_oil": "CRUDE OIL",
    }
    theme = tv_cfg.get("chart_theme", "dark")
    interval = str(tv_cfg.get("chart_interval", "15"))

    tab_labels = [inst_names.get(k, k) for k in symbols if is_instrument_enabled(k) or k != "crude_oil"]
    if not tab_labels:
        tab_labels = list(inst_names.values())

    tabs = st.tabs(tab_labels)
    enabled_keys = [k for k in symbols if k in inst_names]
    for tab, inst in zip(tabs, enabled_keys):
        with tab:
            sym = symbols[inst]
            st.caption(f"TradingView symbol: `{sym}`")
            html = build_chart_html(
                sym, interval=interval, theme=theme, height=520, container_id=f"tv_{inst}"
            )
            components.html(html, height=540, scrolling=True)

    st.divider()
    st.subheader("Webhook Setup")

    secret = env.tradingview_webhook_secret
    if not secret:
        st.error("Set `TRADINGVIEW_WEBHOOK_SECRET` in your `.env` file before enabling alerts.")
    else:
        base = env.public_webhook_url.strip()
        if base:
            webhook_url = base if base.endswith("/webhook/tradingview") else f"{base.rstrip('/')}/webhook/tradingview"
        else:
            webhook_url = f"http://YOUR_VM_IP:{env.webhook_port}/webhook/tradingview"

        st.code(webhook_url, language=None)
        st.markdown(
            f"1. Start the webhook server: `python -m src.main webhook` (port **{env.webhook_port}**)\n"
            "2. Open Oracle Cloud / firewall port for inbound TCP on that port (restrict to your IP)\n"
            "3. In TradingView → create alert → **Webhook URL** → paste URL above\n"
            "4. Alert message (JSON) — include your secret:"
        )
        st.code(get_webhook_message_template("YOUR_SECRET"), language="json")
        st.info(
            "Supported actions: `SCAN` (run sell scan), `SELL_CE`, `SELL_PE`, `EXIT`. "
            "Buy actions (`BUY_CE`/`BUY_PE`) are rejected in profit mode."
        )

        with st.expander("Example Pine Script (RSI alerts)"):
            st.code(get_pine_script_example(), language="javascript")

        st.markdown("**systemd service (optional):**")
        st.code(
            f"""[Unit]
Description=TradingView Webhook Server
After=network.target

[Service]
Type=simple
User=ubuntu
WorkingDirectory=/home/ubuntu/stock-market-automator
ExecStart=/home/ubuntu/stock-market-automator/venv/bin/python -m src.main webhook
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target""",
            language="ini",
        )


def show_option_chain(config):
    st.title("Live Option Chain")

    from streamlit_autorefresh import st_autorefresh

    refresh_sec = config.get("option_chain", {}).get("refresh_seconds", 3)
    st.caption(f"Auto-refresh every {refresh_sec}s during market hours")

    inst_labels = {
        "nifty50": "NIFTY 50",
        "sensex": "SENSEX",
        "crude_oil": "CRUDE OIL",
    }
    toggles = load_toggles()
    # Viewing a chain is always allowed (independent of the trading toggle)
    all_insts = [k for k in inst_labels if k in get_yaml_config().get("instruments", {})]

    instrument = st.selectbox(
        "Instrument",
        all_insts,
        format_func=lambda x: inst_labels.get(x, x)
        + ("" if toggles.get(x, {}).get("enabled", True) else "  (trading OFF)"),
    )
    if not toggles.get(instrument, {}).get("enabled", True):
        st.info(f"{inst_labels[instrument]} is OFF for trading — you can still view its chain. "
                "Enable it in Settings to let the bot trade it.")

    # Auto-refresh (no manual click needed)
    count = st_autorefresh(interval=refresh_sec * 1000, key=f"chain_{instrument}")

    service: LiveOptionChainService = st.session_state.chain_service

    # Try Kite for live chain (required on cloud VMs — NSE blocks Oracle Cloud IPs)
    use_ws = config.get("option_chain", {}).get("use_websocket", False)
    try:
        auth = KiteAuth()
        if auth.is_authenticated():
            service.set_kite_client(auth.get_client())
            # WebSocket is optional — REST quotes work everywhere and avoid 403 spam
            if use_ws and "kite_feed" not in st.session_state:
                from src.data.live_feed import LiveFeedManager
                feed = LiveFeedManager(auth.get_client())
                if feed.start():
                    st.session_state.kite_feed = feed
            if st.session_state.get("kite_feed"):
                service.set_kite_feed(st.session_state.kite_feed)
            st.success("Kite connected — live option chain via Zerodha REST API", icon="⚡")
        else:
            st.warning(
                "**Login to Zerodha Kite** (Settings page) to load the option chain. "
                "NSE's public API is blocked from cloud servers like Oracle Cloud."
            )
    except Exception as e:
        st.info(f"Kite overlay unavailable: {e}")

    chain = service.fetch_chain(instrument)

    if not chain.get("valid"):
        error = chain.get("error", "unknown")
        detail = chain.get("error_detail", "Could not load option chain.")
        if error == "market_closed":
            st.warning(detail)
            if chain.get("stale"):
                st.caption("Showing last cached chain from earlier today.")
        elif error == "kite_login_required":
            st.error(detail)
            st.markdown("Go to **Settings → Zerodha Kite Connect** and authenticate.")
        else:
            st.warning(detail)
        if instrument == "sensex":
            st.caption("SENSEX options trade on BSE (BFO) — needs Kite login + BFO segment.")
        elif instrument == "crude_oil":
            st.caption(
                "Crude Oil options are on **MCX** (9 AM–11:30 PM IST). This needs Kite login "
                "**and the MCX (commodity) segment enabled** on your Zerodha account. "
                "If you just enabled MCX, it can take a day to activate."
            )
        return

    m1, m2, m3, m4, m5, m6 = st.columns(6)
    m1.metric("Spot", f"₹{chain.get('underlying', 0):,.2f}")
    m2.metric("PCR", f"{chain.get('pcr', 0):.2f}")
    m3.metric("Max Pain", f"₹{chain.get('max_pain', 0):,.0f}")
    m4.metric("Expiry", chain.get("expiry", "—"))
    m5.metric("Updated", chain.get("timestamp", "—"))
    m6.metric("Source", chain.get("source", "—").upper())

    view = chain.get("chain_view", pd.DataFrame())
    if view.empty:
        return

    # Highlight ATM row
    strikes_side = config.get("option_chain", {}).get("strikes_each_side", 10)
    if chain.get("underlying"):
        spot = chain["underlying"]
        view = view.iloc[(view["Strike"] - spot).abs().argsort()[: strikes_side * 2 + 1]]

    def highlight_atm(row):
        return ["background-color: #1a3a5c" if row.get("ATM") else "" for _ in row]

    styled = view.style.apply(highlight_atm, axis=1).format({
        "CE LTP": "{:.2f}", "PE LTP": "{:.2f}",
        "CE IV": "{:.1f}", "PE IV": "{:.1f}",
    })
    st.dataframe(styled, use_container_width=True, height=500)

    st.caption(f"Refresh #{count} | ATM row highlighted | CE=Call, PE=Put")


def show_strategy_logic():
    st.title("Strategy & Backtest Logic")
    st.markdown(STRATEGY_LOGIC_MD)
    st.divider()
    st.subheader("Backtest Methodology")
    st.markdown(BACKTEST_METHODOLOGY_MD)


def _trading_guard_status(env):
    """Return live capital-protection status: is it safe to trade right now?"""
    from src.backtest.daily_runner import DailyBacktestRunner

    risk = RiskManager()
    summary = risk.get_risk_summary()
    reasons = []
    allowed = True

    if summary["kill_switch"]:
        allowed = False
        reasons.append("Kill switch is ON")

    try:
        bt_allowed, bt_reason = DailyBacktestRunner().is_trading_allowed()
        if not bt_allowed:
            allowed = False
            reasons.append(bt_reason)
    except Exception:
        pass

    daily_pnl = summary["daily_pnl"]
    loss_limit = summary["daily_loss_limit"]
    if daily_pnl <= -loss_limit:
        allowed = False
        reasons.append(f"Daily loss limit hit (₹{daily_pnl:,.0f})")

    consec = summary.get("consecutive_losses", 0)
    max_consec = summary.get("max_consecutive_losses", 3)
    if consec >= max_consec:
        allowed = False
        reasons.append(f"{consec} losses in a row — cooling off")

    if summary["open_positions"] >= summary["max_positions"]:
        reasons.append("Max open positions reached")

    loss_budget_left = max(0, loss_limit + daily_pnl)
    return {
        "allowed": allowed,
        "reasons": reasons,
        "daily_pnl": daily_pnl,
        "loss_budget_left": loss_budget_left,
        "loss_limit": loss_limit,
        "open_positions": summary["open_positions"],
        "max_positions": summary["max_positions"],
        "consecutive_losses": consec,
    }


def _render_capital_banner(env):
    status = _trading_guard_status(env)
    if status["allowed"]:
        st.success(
            f"🟢 **Safe to trade** · Loss budget left today: ₹{status['loss_budget_left']:,.0f} "
            f"· Open: {status['open_positions']}/{status['max_positions']}"
            + (f" · ⚠️ {'; '.join(status['reasons'])}" if status["reasons"] else "")
        )
    else:
        st.error(
            f"🔴 **Trading paused to protect capital** — {'; '.join(status['reasons'])}. "
            f"Loss budget left: ₹{status['loss_budget_left']:,.0f}"
        )
    return status


def _verdict_style(verdict: str) -> tuple[str, str]:
    return {
        "STRONG": ("🟢", "#0f5132"),
        "GOOD": ("🟢", "#146c43"),
        "FAIR": ("🟡", "#664d03"),
        "AVOID": ("🔴", "#842029"),
    }.get(verdict, ("⚪", "#333"))


def _render_trade_idea_card(opp, rank: int, can_trade: bool):
    icon, color = _verdict_style(opp.verdict)
    action = "SELL" if opp.trade_mode == "SELL_OPTION" else "BUY"
    opt = opp.direction.split("_")[-1]
    name = opp.instrument.upper().replace("NIFTY50", "NIFTY").replace("CRUDE_OIL", "CRUDE")

    st.markdown(
        f"<div style='border-left:6px solid {color};padding:10px 16px;margin:8px 0;"
        f"background:rgba(255,255,255,0.03);border-radius:6px;'>"
        f"<span style='font-size:1.1rem;font-weight:700;'>{icon} #{rank} {action} "
        f"{name} {int(opp.strike)} {opt}</span> "
        f"<span style='float:right;font-weight:700;color:{color};'>{opp.verdict} · "
        f"edge {opp.edge_score:.0f}/100</span></div>",
        unsafe_allow_html=True,
    )

    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Premium", f"₹{opp.entry_price:.1f}")
    c2.metric("Prob. of Profit", f"{opp.pop:.0%}")
    c3.metric("Net if target", f"₹{opp.expected_net_pnl:,.0f}")
    c4.metric("Risk (SL)", f"₹{opp.max_loss:,.0f}")
    c5.metric("Expected Value", f"₹{opp.expected_value:,.0f}")

    c6, c7, c8, c9 = st.columns(4)
    c6.metric("Confidence", f"{opp.confidence:.0%}")
    c7.metric("Delta", f"{opp.delta:.2f}" if opp.delta else "—")
    c8.metric("IV %ile", f"{opp.iv_percentile:.0f}")
    c9.metric("Margin", f"₹{opp.estimated_margin:,.0f}")

    st.markdown(f"**👉 {opp.headline}**")
    st.caption(opp.reasoning)
    if action == "BUY":
        st.warning("Buy idea is informational — it will NOT auto-execute while buy is disabled.")
    if not can_trade:
        st.caption("⚠️ Capital protection is active — the bot will not place this automatically right now.")
    st.divider()


def show_trade_ideas(env, config):
    st.title("Live Trade Ideas")
    st.caption("Ranked, profit-positive suggestions — best edge first. Updates during market hours.")

    from streamlit_autorefresh import st_autorefresh

    auto = st.toggle("Auto-refresh (15s)", value=False, key="ideas_auto")
    if auto:
        st_autorefresh(interval=15000, key="ideas_refresh")

    status = _render_capital_banner(env)

    show_buy = st.toggle("Also show BUY ideas (info only)", value=False, key="ideas_show_buy")
    min_edge = st.slider("Minimum edge score", 30, 80, 45, 5, key="ideas_min_edge")

    with st.spinner("Analyzing live market for the best edges..."):
        try:
            engine = SignalEngine()
            ideas = engine.get_trade_ideas(include_buy=show_buy, min_edge=float(min_edge))
        except Exception as e:
            st.error(f"Could not scan: {e}")
            return

    if not ideas:
        st.info(
            "No high-quality trade ideas right now. The bot stays in cash rather than "
            "forcing a low-edge trade — that's how it protects you from losses."
        )
        return

    strong = [o for o in ideas if o.verdict == "STRONG"]
    if strong:
        st.subheader(f"🔥 {len(strong)} high-conviction setup(s)")

    for i, opp in enumerate(ideas, 1):
        _render_trade_idea_card(opp, i, status["allowed"])

    st.caption(
        "POP = probability of profit (from option delta). Expected Value already "
        "accounts for STT/GST/brokerage and the stop-loss scenario. Only positive-EV "
        "ideas are shown."
    )


def show_dashboard(env):
    st.title("Trading Dashboard")
    _render_capital_banner(env)
    risk = RiskManager()
    summary = risk.get_risk_summary()

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Daily P&L", f"₹{summary['daily_pnl']:,.0f}")
    col2.metric("Open Positions", f"{summary['open_positions']}/{summary['max_positions']}")
    col3.metric("Profit Target", f"{summary['profit_target_pct']}%")
    col4.metric("Kill Switch", "ACTIVE" if summary["kill_switch"] else "OFF")

    st.divider()

    st.subheader("Best Idea Right Now")
    try:
        engine = SignalEngine()
        ideas = engine.get_trade_ideas(include_buy=False)
        if ideas:
            top = ideas[0]
            icon, color = _verdict_style(top.verdict)
            st.markdown(f"### {icon} {top.headline}")
            cc1, cc2, cc3, cc4 = st.columns(4)
            cc1.metric("Verdict", top.verdict)
            cc2.metric("Edge", f"{top.edge_score:.0f}/100")
            cc3.metric("Prob. of Profit", f"{top.pop:.0%}")
            cc4.metric("Expected Value", f"₹{top.expected_value:,.0f}")
            st.caption("See the **Trade Ideas** page for the full ranked list.")
        else:
            st.info("No high-edge setups right now — staying in cash protects your capital.")
    except Exception as e:
        st.caption(f"Idea scan unavailable: {e}")

    st.divider()

    col_left, col_right = st.columns(2)

    with col_left:
        st.subheader("Market Overview")
        fetcher = HistoricalDataFetcher()
        for key, cfg in get_yaml_config()["instruments"].items():
            if not load_toggles().get(key, {}).get("enabled", True):
                continue
            try:
                df = fetcher.fetch_index_history(key, days=30)
                if not df.empty:
                    latest = df.iloc[-1]
                    prev = df.iloc[-2] if len(df) > 1 else latest
                    change = ((latest["close"] - prev["close"]) / prev["close"]) * 100
                    st.metric(cfg["index_name"], f"₹{latest['close']:,.2f}", f"{change:+.2f}%")
            except Exception:
                st.warning(f"Could not load {cfg['index_name']}")

        vix = fetcher.fetch_india_vix()
        if vix:
            st.metric("India VIX", f"{vix:.2f}")

    with col_right:
        st.subheader("Recent Trades")
        db = get_session()
        try:
            trades = db.query(Trade).order_by(Trade.entry_time.desc()).limit(10).all()
            if trades:
                trade_data = [{
                    "Time": t.entry_time.strftime("%H:%M") if t.entry_time else "",
                    "Instrument": t.instrument,
                    "Symbol": t.tradingsymbol,
                    "Entry": f"₹{t.entry_price:.2f}",
                    "PnL": f"₹{t.pnl:,.0f}" if t.status == "CLOSED" else "Open",
                    "Status": t.status,
                } for t in trades]
                st.dataframe(pd.DataFrame(trade_data), use_container_width=True)
            else:
                st.info("No trades yet. Run a scan to find opportunities.")
        finally:
            db.close()

    show_equity_chart()


def show_signals():
    st.title("Live Signal Scanner")
    st.success(get_sell_recommendation_text())
    if not is_any_buy_enabled():
        st.caption("Buy CE/PE is **disabled**. The app will only suggest and execute **SELL** trades.")

    if st.button("Scan Now", type="primary"):
        with st.spinner("Scanning for SELL opportunities..."):
            engine = SignalEngine()
            opportunities = engine.scan_all(include_buy_suggestions=True)

            sells = [o for o in opportunities if o.is_recommended]
            buys = [o for o in opportunities if not o.is_recommended]

            if not sells and not buys:
                st.warning("No opportunities found right now.")
                return

            if sells:
                st.subheader("✅ Recommended — SELL options")
                for opp in sells:
                    _render_opportunity(opp, recommended=True)

            if buys:
                st.subheader("⚠️ Buy alternative (not recommended, will NOT execute)")
                st.caption(get_buy_warning_text())
                for opp in buys:
                    _render_opportunity(opp, recommended=False)


def _render_opportunity(opp, recommended: bool):
    icon, color = _verdict_style(getattr(opp, "verdict", "FAIR"))
    with st.expander(
        f"{icon} {opp.instrument.upper()} — {opp.direction} "
        f"({getattr(opp, 'verdict', '')} · edge {getattr(opp, 'edge_score', 0):.0f} · "
        f"POP {getattr(opp, 'pop', 0):.0%})",
        expanded=recommended,
    ):
        if getattr(opp, "headline", ""):
            st.markdown(f"**👉 {opp.headline}**")
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Premium", f"₹{opp.entry_price}")
        c2.metric("Buy-back target" if opp.trade_mode == "SELL_OPTION" else "Target", f"₹{opp.target_price}")
        c3.metric("Stop Loss", f"₹{opp.stop_loss}")
        c4.metric("Est. Margin", f"₹{opp.estimated_margin:,.0f}")

        c5, c6, c7, c8 = st.columns(4)
        c5.metric("Prob. of Profit", f"{getattr(opp, 'pop', 0):.0%}")
        c6.metric("Net if target", f"₹{opp.expected_net_pnl:,.0f}")
        c7.metric("Risk (SL)", f"₹{getattr(opp, 'max_loss', 0):,.0f}")
        c8.metric("Expected Value", f"₹{getattr(opp, 'expected_value', 0):,.0f}")

        st.markdown(
            f"**Strike:** {opp.strike} | **Lot:** {opp.lot_size} | "
            f"**Delta:** {getattr(opp, 'delta', 0):.2f} | "
            f"**Costs:** ₹{opp.estimated_costs:.0f}"
        )
        st.markdown(f"**{opp.recommendation_note}**")
        st.markdown(f"**Reasoning:** {opp.reasoning}")
        if not recommended:
            st.warning("This buy trade will NOT be executed while buy is disabled.")


def show_positions():
    st.title("Open Positions")
    db = get_session()
    try:
        open_trades = db.query(Trade).filter_by(status="OPEN").all()
        if open_trades:
            for t in open_trades:
                mode = "PAPER" if t.is_paper else "LIVE"
                st.markdown(f"### {t.tradingsymbol} [{mode}]")
                c1, c2, c3, c4 = st.columns(4)
                c1.metric("Entry", f"₹{t.entry_price}")
                c2.metric("Target", f"₹{t.target_price}")
                c3.metric("Stop Loss", f"₹{t.stop_loss}")
                c4.metric("Qty", t.quantity)
        else:
            st.info("No open positions")

        st.divider()
        st.subheader("Trade History")
        closed = db.query(Trade).filter_by(status="CLOSED").order_by(Trade.exit_time.desc()).limit(50).all()
        if closed:
            hist = [{
                "Date": t.exit_time.strftime("%Y-%m-%d %H:%M") if t.exit_time else "",
                "Symbol": t.tradingsymbol,
                "Entry": t.entry_price,
                "Exit": t.exit_price,
                "PnL": t.pnl,
                "PnL%": f"{t.pnl_pct:.1f}%",
                "Reason": t.exit_reason,
                "Mode": "Paper" if t.is_paper else "Live",
            } for t in closed]
            st.dataframe(pd.DataFrame(hist), use_container_width=True)
    finally:
        db.close()


def show_news():
    st.title("Market News & Sentiment")
    fetcher = NewsFetcher()

    if st.button("Refresh News"):
        with st.spinner("Fetching latest news..."):
            articles = fetcher.fetch_all_news()

            for instrument in ["nifty", "sensex", "crude"]:
                sentiment = fetcher.get_instrument_sentiment(instrument)
                col1, col2, col3 = st.columns(3)
                emoji = "🟢" if sentiment["score"] > 0.1 else "🔴" if sentiment["score"] < -0.1 else "🟡"
                col1.metric(f"{instrument.upper()} Sentiment", f"{emoji} {sentiment['score']:+.3f}")
                col2.metric("Articles", sentiment["article_count"])
                col3.metric("Bullish/Bearish", f"{sentiment['bullish_count']}/{sentiment['bearish_count']}")

            st.divider()
            relevant = [a for a in articles if a.get("instruments")]
            for article in relevant[:20]:
                sent = article.get("sentiment", 0)
                icon = "📈" if sent > 0.1 else "📉" if sent < -0.1 else "➡️"
                st.markdown(f"{icon} **{article['title']}**")
                st.caption(f"{article['source']} | {article.get('instruments', [])}")


def show_daily_report():
    st.title("Daily Backtest Report")
    from src.backtest.daily_runner import DailyBacktestRunner

    runner = DailyBacktestRunner()
    col1, col2 = st.columns([1, 3])
    with col1:
        if st.button("Run Daily Backtest Now", type="primary"):
            with st.spinner("Running backtest with costs & news..."):
                report = runner.run()
                st.success("Daily backtest complete!")
                st.rerun()

    report = runner.get_latest_report()
    if not report:
        st.info("No daily backtest yet. Click 'Run Daily Backtest Now' or wait for 8:00 AM auto-run.")
        return

    approved = report.get("strategy_approved", False)
    st.metric(
        "Strategy Status",
        "APPROVED" if approved else "CAUTION — Net Negative",
        delta=f"Net ₹{report.get('combined_net_pnl', 0):,.0f}",
    )

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Net Win Rate", f"{report.get('combined_net_win_rate', 0):.1%}")
    c2.metric("Total Trades", report.get("combined_trades", 0))
    c3.metric("Gross P&L", f"₹{report.get('combined_gross_pnl', 0):,.0f}")
    c4.metric("Costs (STT+GST+etc)", f"₹{report.get('combined_costs', 0):,.0f}")

    st.subheader("Today's News Bias")
    bias = report.get("today_bias", {})
    for inst, b in bias.items():
        st.markdown(f"**{inst}**: {b.get('direction', 'N/A')} — prefer {b.get('prefer', '')}")
        if b.get("caution"):
            st.warning(b["caution"])

    st.subheader("Per-Instrument Results")
    for inst, data in report.get("instruments", {}).items():
        with st.expander(f"{inst} — Net ₹{data.get('net_pnl', 0):,.0f}"):
            st.write(data)


def show_backtest():
    st.title("Strategy Backtest")
    with st.expander("How is backtest run?", expanded=False):
        st.markdown(BACKTEST_METHODOLOGY_MD)

    instrument = st.selectbox(
        "Instrument", ["all", "nifty50", "sensex", "crude_oil"]
    )

    if st.button("Run Backtest", type="primary"):
        with st.spinner("Running backtest on 1 year of historical data..."):
            from src.backtest.engine import BacktestEngine

            engine = BacktestEngine()
            fetcher = HistoricalDataFetcher()

            if instrument == "all":
                combined = engine.run_all()
                st.success(
                    f"Net Win Rate: **{combined.combined_net_win_rate:.1%}** | "
                    f"Net P&L: **₹{combined.combined_pnl:,.0f}** (after STT/GST/brokerage)"
                )

                cols = st.columns(4)
                cols[0].metric("Net Win Rate", f"{combined.combined_net_win_rate:.1%}")
                cols[1].metric("Net P&L", f"₹{combined.combined_pnl:,.0f}")
                cols[2].metric("Total Costs", f"₹{combined.combined_costs:,.0f}")
                cols[3].metric("Strategy", "OK" if combined.strategy_approved else "CAUTION")

                for inst, result in combined.instruments.items():
                    name = get_yaml_config()["instruments"][inst]["index_name"]
                    with st.expander(f"{name} — Net {result.net_win_rate:.1%} ({result.total_trades} trades)"):
                        c1, c2, c3, c4 = st.columns(4)
                        c1.metric("Net Win Rate", f"{result.net_win_rate:.1%}")
                        c2.metric("Net P&L", f"₹{result.total_pnl:,.0f}")
                        c3.metric("Costs", f"₹{result.total_costs:,.0f}")
                        c4.metric("Buy/Sell", f"{result.buy_trades}/{result.sell_trades}")
                        if result.trades:
                            trade_rows = [{
                                "Entry": t.entry_date[:10],
                                "Direction": t.direction,
                                "Mode": t.trade_mode,
                                "Gross": t.gross_pnl,
                                "Costs": t.costs,
                                "Net": t.net_pnl,
                                "Exit": t.exit_reason,
                                "Result": "WIN" if t.win else "LOSS",
                            } for t in result.trades]
                            st.dataframe(pd.DataFrame(trade_rows), use_container_width=True)
            else:
                df = fetcher.fetch_index_history(instrument)
                result = engine.run(df, instrument)

                c1, c2, c3, c4 = st.columns(4)
                c1.metric("Total Trades", result.total_trades)
                c2.metric("Win Rate", f"{result.win_rate:.1%}")
                c3.metric("Total P&L", f"₹{result.total_pnl:,.0f}")
                c4.metric("Max Drawdown", f"{result.max_drawdown:.1f}%")

                c5, c6, c7 = st.columns(3)
                c5.metric("Sharpe Ratio", f"{result.sharpe_ratio:.2f}")
                c6.metric("Profit Factor", f"{result.profit_factor:.2f}")
                c7.metric("20% Targets Hit", result.target_hits)

                if result.trades:
                    st.subheader("Trade Log")
                    st.dataframe(
                        pd.DataFrame([{
                            "Entry": t.entry_date[:10],
                            "Exit": t.exit_date[:10],
                            "Direction": t.direction,
                            "Entry ₹": t.entry_price,
                            "Exit ₹": t.exit_price,
                            "PnL": t.pnl,
                            "PnL%": f"{t.pnl_pct:+.1f}%",
                            "Exit Reason": t.exit_reason,
                            "Result": "WIN" if t.win else "LOSS",
                        } for t in result.trades]),
                        use_container_width=True,
                    )


def show_settings(env, config):
    st.title("Settings & Controls")

    st.success(get_sell_recommendation_text())
    st.warning(get_buy_warning_text())

    col_a, col_b = st.columns(2)
    with col_a:
        if st.button("Reset to Sell-Only (Recommended)", type="primary"):
            apply_sell_only_defaults()
            st.success("Set to sell-only — buy disabled.")
            st.rerun()
    with col_b:
        st.caption("Selling needs ~12% of spot × lot as margin in your Zerodha account.")

    st.subheader("Instrument & Trade Type Toggles")
    st.caption("Keep **Sell CE / Sell PE ON**. Buy toggles are optional and not recommended.")

    toggles = load_toggles()
    inst_names = {
        "nifty50": "NIFTY 50",
        "sensex": "SENSEX",
        "crude_oil": "CRUDE OIL",
    }
    changed = False

    for inst, label in inst_names.items():
        with st.expander(f"{label} — {'ON' if toggles[inst].get('enabled') else 'OFF'}", expanded=inst == "nifty50"):
            new_enabled = st.toggle(f"Enable {label}", value=toggles[inst].get("enabled", True), key=f"en_{inst}")

            st.markdown("**✅ Recommended — Sell (collect premium)**")
            c3, c4 = st.columns(2)
            sell_ce = c3.toggle("Sell CE ✅", value=toggles[inst].get("sell_ce", True), key=f"sce_{inst}")
            sell_pe = c4.toggle("Sell PE ✅", value=toggles[inst].get("sell_pe", True), key=f"spe_{inst}")

            st.markdown("**⚠️ Not recommended — Buy (disabled by default)**")
            c1, c2 = st.columns(2)
            buy_ce = c1.toggle("Buy CE ⚠️", value=toggles[inst].get("buy_ce", False), key=f"bce_{inst}")
            buy_pe = c2.toggle("Buy PE ⚠️", value=toggles[inst].get("buy_pe", False), key=f"bpe_{inst}")
            if buy_ce or buy_pe:
                st.warning("Buy is enabled — backtest showed losses. App still prefers SELL.")

            if (
                new_enabled != toggles[inst].get("enabled")
                or buy_ce != toggles[inst].get("buy_ce")
                or buy_pe != toggles[inst].get("buy_pe")
                or sell_ce != toggles[inst].get("sell_ce")
                or sell_pe != toggles[inst].get("sell_pe")
            ):
                toggles[inst] = {
                    "enabled": new_enabled,
                    "buy_ce": buy_ce,
                    "buy_pe": buy_pe,
                    "sell_ce": sell_ce,
                    "sell_pe": sell_pe,
                }
                changed = True

    if changed:
        save_toggles(toggles)
        st.success("Toggles saved!")
        st.rerun()

    st.divider()
    st.subheader("Zerodha Kite Connect")
    auth = KiteAuth()

    if auth.is_authenticated():
        st.success("Connected to Zerodha Kite")
        try:
            profile = auth.get_client().profile()
            st.markdown(f"**User:** {profile.get('user_name', 'N/A')}")
            st.markdown(f"**Email:** {profile.get('email', 'N/A')}")
        except Exception:
            st.warning("Session may have expired. Re-login required.")
    else:
        st.warning("Not connected to Zerodha")
        login_url = auth.get_login_url() if auth.kite else None
        if login_url:
            st.markdown(f"[Click here to login to Zerodha]({login_url})")
            token = st.text_input("Paste request_token from redirect URL:")
            if st.button("Authenticate") and token:
                if auth.generate_session(token):
                    st.success("Authenticated successfully!")
                    st.rerun()

    st.divider()
    risk = RiskManager()
    summary = risk.get_risk_summary()
    st.subheader("Risk Status")
    c1, c2, c3 = st.columns(3)
    c1.metric("Open Positions", f"{summary['open_positions']}/{summary['max_positions']}")
    c2.metric("Daily P&L", f"₹{summary['daily_pnl']:,.0f}")
    c3.metric("Consecutive Losses", f"{summary['consecutive_losses']}/{summary['max_consecutive_losses']}")

    st.subheader("Risk Parameters")
    st.markdown(f"- Capital: ₹{env.capital:,.0f}")
    st.markdown(f"- Max risk per trade: {env.max_risk_per_trade_pct}%")
    st.markdown(f"- Max daily loss: {env.max_daily_loss_pct}%")
    st.markdown(f"- Profit target: {env.profit_target_pct}%")
    st.markdown(f"- Stop loss: {env.stop_loss_pct}%")
    st.markdown(f"- Max open positions: {env.max_open_positions}")
    st.markdown(f"- Trailing stop: {config.get('exit_rules', {}).get('trailing_stop_pct', 10)}% after "
                f"{config.get('exit_rules', {}).get('trailing_activate_after_pct', 15)}% profit")
    st.markdown(f"- No new entries: before {config.get('time_filters', {}).get('no_entry_before', '09:30')} / "
                f"after {config.get('time_filters', {}).get('no_entry_after', '15:15')} IST")

    st.divider()
    if risk.is_killed:
        if st.button("Deactivate Kill Switch"):
            risk.deactivate_kill_switch()
            st.rerun()
    else:
        if st.button("Activate Kill Switch", type="primary"):
            risk.activate_kill_switch("Manual from dashboard")
            st.rerun()


def show_equity_chart():
    st.subheader("Daily P&L")
    db = get_session()
    try:
        records = db.query(DailyPnL).order_by(DailyPnL.date).limit(90).all()
        if records:
            dates = [r.date for r in records]
            pnl = [r.realized_pnl for r in records]
            cumulative = pd.Series(pnl).cumsum()

            fig = go.Figure()
            fig.add_trace(go.Scatter(x=dates, y=cumulative, mode="lines+markers", name="Cumulative P&L"))
            fig.update_layout(height=300, margin=dict(l=0, r=0, t=30, b=0))
            st.plotly_chart(fig, use_container_width=True)
    finally:
        db.close()


if __name__ == "__main__":
    main()
