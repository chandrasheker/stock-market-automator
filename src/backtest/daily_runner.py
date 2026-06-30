"""Daily automated backtest runner with news alignment check."""

from __future__ import annotations

import json
from datetime import datetime

from loguru import logger

from src.backtest.engine import BacktestEngine
from src.config import ROOT_DIR, get_yaml_config
from src.data.database import DailyBacktestReport, get_session, init_db
from src.data.historical import HistoricalDataFetcher
from src.data.news_fetcher import NewsFetcher


class DailyBacktestRunner:
    """Runs full backtest daily, stores results, and advises trading mode."""

    INSTRUMENT_NEWS = {
        "nifty50": "nifty",
        "sensex": "sensex",
        "crude_oil": "crude",
    }

    def __init__(self):
        self.config = get_yaml_config()
        self.engine = BacktestEngine()
        self.fetcher = HistoricalDataFetcher()
        self.news = NewsFetcher()
        init_db()

    def run(self) -> dict:
        logger.info("Starting daily backtest run...")

        # Refresh historical data
        data = self.fetcher.download_all_instruments()

        # Fetch latest news
        articles = self.news.fetch_all_news()
        news_by_inst = {}
        for inst, news_key in self.INSTRUMENT_NEWS.items():
            news_by_inst[inst] = self.news.get_instrument_sentiment(news_key)

        # Run backtest with costs
        combined = self.engine.run_all(data)

        # Build per-instrument report
        report = {
            "date": datetime.now().isoformat(),
            "combined_trades": combined.combined_trades,
            "combined_net_win_rate": combined.combined_net_win_rate,
            "combined_gross_pnl": combined.combined_gross_pnl,
            "combined_costs": combined.combined_costs,
            "combined_net_pnl": combined.combined_pnl,
            "combined_sharpe": combined.combined_sharpe,
            "strategy_approved": combined.strategy_approved,
            "news": news_by_inst,
            "instruments": {},
        }

        for inst, result in combined.instruments.items():
            report["instruments"][inst] = {
                "trades": result.total_trades,
                "net_win_rate": result.net_win_rate,
                "gross_pnl": result.gross_pnl,
                "costs": result.total_costs,
                "net_pnl": result.total_pnl,
                "buy_trades": result.buy_trades,
                "sell_trades": result.sell_trades,
            }

        # News alignment recommendations for today
        report["today_bias"] = self._compute_today_bias(news_by_inst, combined)

        self._save_report(report)
        self._write_summary_file(report)
        self.engine.print_report(combined)

        logger.info(
            f"Daily backtest complete: Net P&L ₹{combined.combined_pnl:,.0f} "
            f"| Win rate {combined.combined_net_win_rate:.1%} "
            f"| Strategy {'APPROVED' if combined.strategy_approved else 'CAUTION'}"
        )
        return report

    def _compute_today_bias(self, news_by_inst: dict, combined) -> dict:
        bias = {}
        for inst, news in news_by_inst.items():
            score = news.get("score", 0)
            if score > 0.15:
                bias[inst] = {"direction": "BULLISH", "prefer": "BUY_CE or SELL_PE"}
            elif score < -0.15:
                bias[inst] = {"direction": "BEARISH", "prefer": "BUY_PE or SELL_CE"}
            else:
                bias[inst] = {"direction": "NEUTRAL", "prefer": "SELL_OTM premium"}

            result = combined.instruments.get(inst)
            if result and result.total_pnl < 0:
                bias[inst]["caution"] = "Backtest net negative — reduce size or skip"
        return bias

    def _save_report(self, report: dict):
        db = get_session()
        try:
            today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
            existing = db.query(DailyBacktestReport).filter_by(date=today).first()
            if existing:
                db.delete(existing)

            record = DailyBacktestReport(
                date=today,
                combined_trades=report["combined_trades"],
                combined_net_win_rate=report["combined_net_win_rate"],
                combined_gross_pnl=report["combined_gross_pnl"],
                combined_costs=report["combined_costs"],
                combined_net_pnl=report["combined_net_pnl"],
                combined_sharpe=report["combined_sharpe"],
                strategy_approved=report["strategy_approved"],
                news_json=json.dumps(report["news"]),
                instruments_json=json.dumps(report["instruments"]),
                today_bias_json=json.dumps(report["today_bias"]),
            )
            db.add(record)
            db.commit()
        except Exception as e:
            db.rollback()
            logger.error(f"Failed to save daily backtest report: {e}")
        finally:
            db.close()

    def _write_summary_file(self, report: dict):
        summary_dir = ROOT_DIR / "logs" / "daily_backtest"
        summary_dir.mkdir(parents=True, exist_ok=True)
        date_str = datetime.now().strftime("%Y-%m-%d")
        path = summary_dir / f"backtest_{date_str}.json"
        path.write_text(json.dumps(report, indent=2, default=str))

    def is_trading_allowed(self) -> tuple[bool, str]:
        """Check latest daily backtest before allowing live trades."""
        db = get_session()
        try:
            latest = (
                db.query(DailyBacktestReport)
                .order_by(DailyBacktestReport.date.desc())
                .first()
            )
            if not latest:
                return True, "No daily backtest yet — run daily-backtest first"

            if not latest.strategy_approved:
                return False, (
                    f"Daily backtest net P&L negative (₹{latest.combined_net_pnl:,.0f}) "
                    "— trading paused for capital protection"
                )
            if latest.combined_net_win_rate < 0.5:
                return False, f"Net win rate too low ({latest.combined_net_win_rate:.1%})"
            return True, (
                f"Backtest OK: net ₹{latest.combined_net_pnl:,.0f}, "
                f"win rate {latest.combined_net_win_rate:.1%}"
            )
        finally:
            db.close()

    def get_latest_report(self) -> dict | None:
        db = get_session()
        try:
            latest = (
                db.query(DailyBacktestReport)
                .order_by(DailyBacktestReport.date.desc())
                .first()
            )
            if not latest:
                return None
            return {
                "date": str(latest.date),
                "combined_trades": latest.combined_trades,
                "combined_net_win_rate": latest.combined_net_win_rate,
                "combined_gross_pnl": latest.combined_gross_pnl,
                "combined_costs": latest.combined_costs,
                "combined_net_pnl": latest.combined_net_pnl,
                "strategy_approved": latest.strategy_approved,
                "news": json.loads(latest.news_json or "{}"),
                "instruments": json.loads(latest.instruments_json or "{}"),
                "today_bias": json.loads(latest.today_bias_json or "{}"),
            }
        finally:
            db.close()
