"""Execute trades triggered by TradingView webhooks."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time as dt_time
from typing import Optional

from loguru import logger

from src.analysis.signal_engine import SignalEngine, TradeOpportunity
from src.auth.kite_auth import KiteAuth
from src.config import get_env, get_yaml_config
from src.execution.order_manager import OrderManager
from src.execution.paper_trader import PaperTrader
from src.integrations.tradingview import TradingViewAlert
from src.risk.manager import RiskManager
from src.toggles import is_instrument_enabled


@dataclass
class WebhookResult:
    ok: bool
    message: str
    action: str = ""
    instrument: str = ""
    direction: str = ""
    executed: bool = False
    trade_id: Optional[int] = None


class WebhookExecutor:
    """Bridge TradingView alerts into the existing signal + risk pipeline."""

    def __init__(self):
        self.env = get_env()
        self.config = get_yaml_config()
        self.signal_engine = SignalEngine()
        self.risk_manager = RiskManager()
        self.paper_trader = PaperTrader(self.risk_manager)
        self.order_manager: Optional[OrderManager] = None

        if self.env.trading_mode == "live":
            auth = KiteAuth()
            if auth.is_authenticated():
                self.order_manager = OrderManager(auth.get_client(), self.risk_manager)
            else:
                logger.warning("Live mode but Kite not authenticated — webhook uses paper")

    def handle(self, alert: TradingViewAlert) -> WebhookResult:
        if not is_instrument_enabled(alert.instrument):
            return WebhookResult(
                ok=False,
                message=f"Instrument {alert.instrument} is disabled",
                action=alert.action,
                instrument=alert.instrument,
            )

        if not self._is_market_hours(alert.instrument):
            return WebhookResult(
                ok=False,
                message="Outside market hours for this instrument",
                action=alert.action,
                instrument=alert.instrument,
            )

        if alert.action == "EXIT":
            return self._handle_exit(alert)

        opportunity = self._resolve_opportunity(alert)
        if not opportunity:
            return WebhookResult(
                ok=True,
                message="No matching sell opportunity — alert acknowledged, no trade",
                action=alert.action,
                instrument=alert.instrument,
            )

        if alert.action.startswith("BUY"):
            return WebhookResult(
                ok=False,
                message="Buy alerts blocked — profit mode recommends SELL only",
                action=alert.action,
                instrument=alert.instrument,
                direction=opportunity.direction,
            )

        trade = self._execute(opportunity)
        if trade:
            return WebhookResult(
                ok=True,
                message=f"Executed {opportunity.direction} @ ₹{opportunity.entry_price}",
                action=alert.action,
                instrument=alert.instrument,
                direction=opportunity.direction,
                executed=True,
                trade_id=trade.id,
            )

        return WebhookResult(
            ok=True,
            message="Signal found but rejected by risk/cost gate",
            action=alert.action,
            instrument=alert.instrument,
            direction=opportunity.direction,
        )

    def _resolve_opportunity(self, alert: TradingViewAlert) -> Optional[TradeOpportunity]:
        if alert.action == "SCAN":
            return self.signal_engine.scan_instrument(alert.instrument)

        wanted = alert.direction_filter
        if not wanted:
            return None

        sells = self.signal_engine._scan_sell_opportunities(alert.instrument)
        for opp in sells:
            if opp.direction == wanted:
                return opp
        return None

    def _execute(self, opportunity: TradeOpportunity):
        if self.env.trading_mode == "live" and self.order_manager:
            return self.order_manager.execute(opportunity)
        return self.paper_trader.execute(opportunity)

    def _handle_exit(self, alert: TradingViewAlert) -> WebhookResult:
        from src.data.database import Trade, get_session
        from src.data.historical import HistoricalDataFetcher

        fetcher = HistoricalDataFetcher()
        closed = 0
        db = get_session()
        try:
            open_trades = db.query(Trade).filter_by(
                status="OPEN", instrument=alert.instrument
            ).all()
            for trade in open_trades:
                if self.env.trading_mode == "live" and self.order_manager:
                    if self.order_manager.exit_position(trade, "TRADINGVIEW_EXIT"):
                        closed += 1
                    continue

                exit_price = trade.entry_price
                try:
                    hist = fetcher.fetch_index_history(alert.instrument)
                    if not hist.empty:
                        exit_price = float(hist["close"].iloc[-1])
                except Exception:
                    pass
                self.risk_manager.record_trade_close(trade, exit_price, "TRADINGVIEW_EXIT")
                closed += 1
        finally:
            db.close()

        return WebhookResult(
            ok=True,
            message=f"Exit requested — closed {closed} position(s)",
            action=alert.action,
            instrument=alert.instrument,
            executed=closed > 0,
        )

    def _is_market_hours(self, instrument: str) -> bool:
        now = datetime.now()
        if now.weekday() >= 5:
            return False

        trading = self.config.get("trading", {})
        if instrument == "crude_oil":
            market_open = dt_time(9, 0)
            market_close = dt_time(23, 30)
        else:
            open_str = trading.get("market_open", "09:15")
            close_str = trading.get("market_close", "15:30")
            h, m = map(int, open_str.split(":"))
            market_open = dt_time(h, m)
            h, m = map(int, close_str.split(":"))
            market_close = dt_time(h, m)

        return market_open <= now.time() <= market_close
