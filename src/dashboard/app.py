"""Streamlit dashboard for personal trading automator."""

from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from src.analysis.signal_engine import SignalEngine
from src.auth.kite_auth import KiteAuth
from src.backtest.engine import BacktestEngine
from src.config import get_env, get_yaml_config
from src.data.database import DailyPnL, Trade, TradeSignal, get_session, init_db
from src.data.historical import HistoricalDataFetcher
from src.data.news_fetcher import NewsFetcher
from src.risk.manager import RiskManager

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
        ["Dashboard", "Live Signals", "Positions", "News", "Backtest", "Settings"],
    )

    st.sidebar.divider()
    mode_color = "🟢" if env.trading_mode == "paper" else "🔴"
    st.sidebar.markdown(f"**Mode:** {mode_color} {env.trading_mode.upper()}")
    st.sidebar.markdown(f"**Capital:** ₹{env.capital:,.0f}")
    st.sidebar.markdown(f"**Profit Target:** {env.profit_target_pct}%")

    if page == "Dashboard":
        show_dashboard(env)
    elif page == "Live Signals":
        show_signals()
    elif page == "Positions":
        show_positions()
    elif page == "News":
        show_news()
    elif page == "Backtest":
        show_backtest()
    elif page == "Settings":
        show_settings(env, config)


def show_dashboard(env):
    st.title("Trading Dashboard")
    risk = RiskManager()
    summary = risk.get_risk_summary()

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Daily P&L", f"₹{summary['daily_pnl']:,.0f}")
    col2.metric("Open Positions", f"{summary['open_positions']}/{summary['max_positions']}")
    col3.metric("Profit Target", f"{summary['profit_target_pct']}%")
    col4.metric("Kill Switch", "ACTIVE" if summary["kill_switch"] else "OFF")

    st.divider()

    col_left, col_right = st.columns(2)

    with col_left:
        st.subheader("Market Overview")
        fetcher = HistoricalDataFetcher()
        for key, cfg in get_yaml_config()["instruments"].items():
            if cfg.get("enabled"):
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

    if st.button("Scan Now", type="primary"):
        with st.spinner("Analyzing markets..."):
            engine = SignalEngine()
            opportunities = engine.scan_all()

            if not opportunities:
                st.warning("No high-confidence opportunities found right now.")
                return

            for opp in opportunities:
                with st.expander(
                    f"{opp.instrument.upper()} — {opp.direction} "
                    f"(Confidence: {opp.confidence:.0%})",
                    expanded=True,
                ):
                    c1, c2, c3 = st.columns(3)
                    c1.metric("Entry Price", f"₹{opp.entry_price}")
                    c2.metric("Target (+20%)", f"₹{opp.target_price}")
                    c3.metric("Stop Loss", f"₹{opp.stop_loss}")

                    st.markdown(f"**Strike:** {opp.strike} | **Lot Size:** {opp.lot_size}")
                    st.markdown(f"**Reasoning:** {opp.reasoning}")

                    if opp.strategy_scores:
                        st.markdown("**Strategy Breakdown:**")
                        for name, scores in opp.strategy_scores.items():
                            direction = scores.get("direction", "N/A")
                            score = scores.get("score", 0)
                            st.progress(min(1.0, abs(score)), text=f"{name}: {direction} ({score:+.2f})")


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


def show_backtest():
    st.title("Strategy Backtest")
    instrument = st.selectbox("Instrument", ["nifty50", "sensex", "crude_oil"])

    if st.button("Run Backtest"):
        with st.spinner("Running backtest on historical data..."):
            fetcher = HistoricalDataFetcher()
            df = fetcher.fetch_index_history(instrument, days=365)
            result = BacktestEngine().run(df)

            c1, c2, c3, c4 = st.columns(4)
            c1.metric("Total Trades", result.total_trades)
            c2.metric("Win Rate", f"{result.win_rate:.0%}")
            c3.metric("Total P&L", f"₹{result.total_pnl:,.0f}")
            c4.metric("Max Drawdown", f"{result.max_drawdown:.1f}%")

            c5, c6, c7 = st.columns(3)
            c5.metric("Sharpe Ratio", f"{result.sharpe_ratio:.2f}")
            c6.metric("Profit Factor", f"{result.profit_factor:.2f}")
            c7.metric("Avg Profit", f"₹{result.avg_profit:,.0f}")

            if result.trades:
                st.subheader("Trade Log")
                st.dataframe(pd.DataFrame(result.trades), use_container_width=True)


def show_settings(env, config):
    st.title("Settings & Kite Login")

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
    st.subheader("Risk Parameters")
    st.markdown(f"- Capital: ₹{env.capital:,.0f}")
    st.markdown(f"- Max risk per trade: {env.max_risk_per_trade_pct}%")
    st.markdown(f"- Max daily loss: {env.max_daily_loss_pct}%")
    st.markdown(f"- Profit target: {env.profit_target_pct}%")
    st.markdown(f"- Stop loss: {env.stop_loss_pct}%")
    st.markdown(f"- Max open positions: {env.max_open_positions}")

    st.divider()
    risk = RiskManager()
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
