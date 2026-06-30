"""Main trading bot orchestrator."""

from __future__ import annotations

import signal
import sys
import time
from datetime import datetime, time as dt_time

from apscheduler.schedulers.background import BackgroundScheduler
from loguru import logger

from src.analysis.signal_engine import SignalEngine
from src.auth.kite_auth import KiteAuth
from src.config import ROOT_DIR, get_env, get_yaml_config
from src.data.database import init_db
from src.data.historical import HistoricalDataFetcher
from src.data.live_feed import LiveFeedManager
from src.data.news_fetcher import NewsFetcher
from src.execution.order_manager import OrderManager
from src.execution.paper_trader import PaperTrader
from src.risk.manager import RiskManager

logger.remove()
logger.add(sys.stderr, level="INFO", format="{time:HH:mm:ss} | {level} | {message}")
logger.add(ROOT_DIR / "logs" / "trading_{time:YYYY-MM-DD}.log", rotation="1 day", retention=30)


class TradingBot:
    """Orchestrates scanning, signal generation, and trade execution."""

    def __init__(self):
        self.env = get_env()
        self.config = get_yaml_config()
        self.signal_engine = SignalEngine()
        self.risk_manager = RiskManager()
        self.data_fetcher = HistoricalDataFetcher()
        self.news_fetcher = NewsFetcher()
        self.paper_trader = PaperTrader(self.risk_manager)
        self.order_manager = None
        self.live_feed = LiveFeedManager()
        self.kite_auth = KiteAuth()
        self.scheduler = BackgroundScheduler()
        self._running = False

        init_db()
        (ROOT_DIR / "logs").mkdir(exist_ok=True)

        if self.env.trading_mode == "live":
            self._setup_live_trading()

    def _setup_live_trading(self):
        if self.kite_auth.is_authenticated():
            kite = self.kite_auth.get_client()
            self.order_manager = OrderManager(kite, self.risk_manager)
            self.live_feed.set_kite_client(kite)
            logger.info("Live trading mode enabled")
        else:
            logger.warning("Live mode requested but not authenticated. Falling back to paper.")
            self.env.trading_mode = "paper"

    def start(self):
        logger.info("=" * 60)
        logger.info("INDIAN OPTIONS TRADING AUTOMATOR")
        logger.info(f"Mode: {self.env.trading_mode.upper()}")
        logger.info(f"Capital: ₹{self.env.capital:,.0f}")
        logger.info(f"Profit Target: {self.env.profit_target_pct}% per lot")
        logger.info(f"Stop Loss: {self.env.stop_loss_pct}%")
        logger.info("=" * 60)

        self._running = True
        scan_interval = self.config["trading"]["scan_interval_seconds"]

        self.scheduler.add_job(self.scan_and_trade, "interval", seconds=scan_interval)
        self.scheduler.add_job(self.monitor_positions, "interval", seconds=30)
        self.scheduler.add_job(self.fetch_news, "interval", minutes=15)
        self.scheduler.start()

        if self.env.trading_mode == "live":
            self.live_feed.start()

        self.scan_and_trade()

        try:
            while self._running:
                time.sleep(1)
        except KeyboardInterrupt:
            self.stop()

    def stop(self):
        logger.info("Shutting down trading bot...")
        self._running = False
        self.scheduler.shutdown(wait=False)
        self.live_feed.stop()
        sys.exit(0)

    def scan_and_trade(self):
        if not self._is_market_hours():
            return

        logger.info("Scanning for opportunities...")
        opportunities = self.signal_engine.scan_all()

        if not opportunities:
            logger.info("No high-confidence opportunities found")
            return

        for opp in opportunities:
            logger.info(
                f"SIGNAL: {opp.instrument} {opp.direction} @ ₹{opp.entry_price} "
                f"| Confidence: {opp.confidence:.0%} | Target: ₹{opp.target_price} (+20%)"
            )

            if self.env.trading_mode == "live" and self.order_manager:
                self.order_manager.execute(opp)
            else:
                self.paper_trader.execute(opp)

    def monitor_positions(self):
        if self.env.trading_mode == "live" and self.order_manager:
            self._monitor_live_positions()
        else:
            self._monitor_paper_positions()

    def _monitor_paper_positions(self):
        open_trades = self.paper_trader.get_open_trades()
        for trade in open_trades:
            current_price = self._get_current_price(trade)
            if current_price:
                self.paper_trader.check_and_exit(trade, current_price)

    def _monitor_live_positions(self):
        from src.data.database import Trade, get_session
        db = get_session()
        try:
            open_trades = db.query(Trade).filter_by(status="OPEN", is_paper=False).all()
            for trade in open_trades:
                current_price = self._get_current_price(trade)
                if current_price:
                    reason = self.risk_manager.check_exit_conditions(trade, current_price)
                    if reason:
                        self.order_manager.exit_position(trade, reason)
        finally:
            db.close()

    def _get_current_price(self, trade) -> float | None:
        try:
            if self.live_feed.latest_ticks:
                for tick in self.live_feed.latest_ticks.values():
                    if tick.get("last_price"):
                        return tick["last_price"]

            chain = self.data_fetcher.fetch_option_chain_snapshot(
                self.config["instruments"][trade.instrument]["underlying"]
            )
            if chain:
                from src.analysis.options_analyzer import OptionsAnalyzer
                df = OptionsAnalyzer().parse_option_chain(chain)
                opt_type = "CE" if "CE" in trade.direction else "PE"
                match = df[(df["type"] == opt_type)]
                if not match.empty:
                    return float(match.iloc[0]["ltp"])
        except Exception as e:
            logger.debug(f"Price fetch failed: {e}")
        return None

    def fetch_news(self):
        try:
            articles = self.news_fetcher.fetch_all_news()
            logger.info(f"Fetched {len(articles)} news articles")
        except Exception as e:
            logger.warning(f"News fetch failed: {e}")

    def _is_market_hours(self) -> bool:
        now = datetime.now()
        if now.weekday() >= 5:
            return False
        market_open = dt_time(9, 15)
        market_close = dt_time(15, 30)
        return market_open <= now.time() <= market_close

    def download_history(self):
        logger.info("Downloading historical data for all instruments...")
        results = self.data_fetcher.download_all_instruments()
        for key, df in results.items():
            logger.info(f"  {key}: {len(df)} candles downloaded")
        return results


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Indian Options Trading Automator")
    parser.add_argument("command", choices=["run", "login", "scan", "download", "backtest"])
    parser.add_argument("--instrument", default="nifty50")
    args = parser.parse_args()

    bot = TradingBot()

    if args.command == "login":
        bot.kite_auth.authenticate_interactive()
    elif args.command == "download":
        bot.download_history()
    elif args.command == "scan":
        opps = bot.signal_engine.scan_all()
        for o in opps:
            print(f"\n{o.instrument} | {o.direction} | Confidence: {o.confidence:.0%}")
            print(f"  Entry: ₹{o.entry_price} → Target: ₹{o.target_price} | SL: ₹{o.stop_loss}")
            print(f"  {o.reasoning}")
    elif args.command == "backtest":
        from src.backtest.engine import BacktestEngine
        df = bot.data_fetcher.fetch_index_history(args.instrument)
        result = BacktestEngine().run(df)
        print(f"\nBacktest Results for {args.instrument}:")
        print(f"  Trades: {result.total_trades} | Win Rate: {result.win_rate:.0%}")
        print(f"  Total PnL: ₹{result.total_pnl:,.0f} | Max DD: {result.max_drawdown:.1f}%")
        print(f"  Sharpe: {result.sharpe_ratio:.2f} | Profit Factor: {result.profit_factor:.2f}")
    elif args.command == "run":
        signal.signal(signal.SIGINT, lambda s, f: bot.stop())
        signal.signal(signal.SIGTERM, lambda s, f: bot.stop())
        bot.start()


if __name__ == "__main__":
    main()
