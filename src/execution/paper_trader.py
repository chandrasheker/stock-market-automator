"""Paper trading simulator for risk-free testing."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from loguru import logger

from src.analysis.signal_engine import TradeOpportunity
from src.data.database import Trade, TradeSignal, get_session, init_db
from src.risk.manager import RiskManager


class PaperTrader:
    """Simulates order execution without real money."""

    def __init__(self, risk_manager: RiskManager):
        self.risk = risk_manager
        init_db()
        self.virtual_balance = risk_manager.env.capital

    def execute(self, opportunity: TradeOpportunity) -> Optional[Trade]:
        approved, reason = self.risk.can_trade(opportunity)
        if not approved:
            logger.info(f"Trade rejected: {reason}")
            return None

        quantity = self.risk.calculate_position_size(opportunity)
        margin = opportunity.entry_price * quantity

        if margin > self.virtual_balance * 0.3:
            logger.warning(f"Insufficient virtual balance for {opportunity.instrument}")
            return None

        db = get_session()
        try:
            signal = TradeSignal(
                instrument=opportunity.instrument,
                strategy="composite",
                direction=opportunity.direction,
                strike=opportunity.strike,
                expiry=opportunity.expiry,
                confidence=opportunity.confidence,
                entry_price=opportunity.entry_price,
                target_price=opportunity.target_price,
                stop_loss=opportunity.stop_loss,
                reasoning=opportunity.reasoning,
                executed=True,
            )
            db.add(signal)
            db.flush()

            opt_type = "CE" if "CE" in opportunity.direction else "PE"
            tradingsymbol = self._build_symbol(
                opportunity.instrument, opportunity.strike, opt_type, opportunity.expiry
            )

            trade = Trade(
                signal_id=signal.id,
                order_id=f"PAPER-{uuid.uuid4().hex[:8]}",
                instrument=opportunity.instrument,
                tradingsymbol=tradingsymbol,
                exchange=self._get_exchange(opportunity.instrument),
                direction=opportunity.direction,
                quantity=quantity,
                entry_price=opportunity.entry_price,
                target_price=opportunity.target_price,
                stop_loss=opportunity.stop_loss,
                status="OPEN",
                is_paper=True,
                entry_time=datetime.utcnow(),
            )
            db.add(trade)
            db.commit()

            self.virtual_balance -= margin
            logger.info(
                f"PAPER TRADE [{opportunity.trade_mode}]: {tradingsymbol} @ ₹{opportunity.entry_price} "
                f"x{quantity} | Target: ₹{opportunity.target_price} | SL: ₹{opportunity.stop_loss} "
                f"| Est. costs: ₹{opportunity.estimated_costs:.0f} | Est. net: ₹{opportunity.expected_net_pnl:.0f}"
            )
            return trade
        except Exception as e:
            db.rollback()
            logger.error(f"Paper trade failed: {e}")
            return None
        finally:
            db.close()

    def check_and_exit(self, trade: Trade, current_price: float) -> bool:
        reason = self.risk.check_exit_conditions(trade, current_price)
        if reason:
            self.risk.record_trade_close(trade, current_price, reason)
            self.virtual_balance += trade.pnl + (trade.entry_price * trade.quantity)
            logger.info(
                f"PAPER EXIT ({reason}): {trade.tradingsymbol} @ ₹{current_price} "
                f"| Net PnL: ₹{trade.pnl:,.0f} (after costs)"
            )
            return True
        return False

    def get_open_trades(self) -> list[Trade]:
        db = get_session()
        try:
            return db.query(Trade).filter_by(status="OPEN", is_paper=True).all()
        finally:
            db.close()

    @staticmethod
    def _build_symbol(instrument: str, strike: float, opt_type: str, expiry: str) -> str:
        prefix_map = {"nifty50": "NIFTY", "sensex": "SENSEX", "crude_oil": "CRUDEOIL"}
        prefix = prefix_map.get(instrument, instrument.upper())
        return f"{prefix}{strike:.0f}{opt_type}"

    @staticmethod
    def _get_exchange(instrument: str) -> str:
        exchange_map = {"nifty50": "NFO", "sensex": "BFO", "crude_oil": "MCX"}
        return exchange_map.get(instrument, "NFO")
