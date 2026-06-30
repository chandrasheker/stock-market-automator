"""Live order execution via Zerodha Kite Connect."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from kiteconnect import KiteConnect
from loguru import logger

from src.analysis.signal_engine import TradeOpportunity
from src.config import get_yaml_config
from src.data.database import Trade, TradeSignal, get_session, init_db
from src.risk.manager import RiskManager


class OrderManager:
    """Executes real orders through Kite Connect API."""

    def __init__(self, kite: KiteConnect, risk_manager: RiskManager):
        self.kite = kite
        self.risk = risk_manager
        self.config = get_yaml_config()
        init_db()
        self._instruments_cache: dict = {}

    def execute(self, opportunity: TradeOpportunity) -> Optional[Trade]:
        approved, reason = self.risk.can_trade(opportunity)
        if not approved:
            logger.info(f"Live trade rejected: {reason}")
            return None

        quantity = self.risk.calculate_position_size(opportunity)
        exchange = self._get_exchange(opportunity.instrument)

        tradingsymbol = self._find_tradingsymbol(
            opportunity.instrument,
            opportunity.strike,
            opportunity.direction,
            opportunity.expiry,
        )
        if not tradingsymbol:
            logger.error(f"Could not find tradingsymbol for {opportunity.instrument}")
            return None

        is_sell = opportunity.direction.startswith("SELL")
        txn_type = self.kite.TRANSACTION_TYPE_SELL if is_sell else self.kite.TRANSACTION_TYPE_BUY

        try:
            order_id = self.kite.place_order(
                variety=self.kite.VARIETY_REGULAR,
                exchange=exchange,
                tradingsymbol=tradingsymbol,
                transaction_type=txn_type,
                quantity=quantity,
                product=self.kite.PRODUCT_MIS,
                order_type=self.kite.ORDER_TYPE_LIMIT,
                price=opportunity.entry_price,
                validity=self.kite.VALIDITY_DAY,
                tag="auto-trader",
            )
            logger.info(f"LIVE ORDER [{opportunity.trade_mode}]: {order_id} - {tradingsymbol}")

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

                trade = Trade(
                    signal_id=signal.id,
                    order_id=str(order_id),
                    instrument=opportunity.instrument,
                    tradingsymbol=tradingsymbol,
                    exchange=exchange,
                    direction=opportunity.direction,
                    quantity=quantity,
                    entry_price=opportunity.entry_price,
                    target_price=opportunity.target_price,
                    stop_loss=opportunity.stop_loss,
                    status="OPEN",
                    is_paper=False,
                    entry_time=datetime.utcnow(),
                )
                db.add(trade)
                db.commit()
                return trade
            except Exception as e:
                db.rollback()
                logger.error(f"Failed to record live trade: {e}")
                return None
            finally:
                db.close()

        except Exception as e:
            logger.error(f"Order placement failed: {e}")
            return None

    def exit_position(self, trade: Trade, reason: str = "MANUAL") -> bool:
        is_sell = trade.direction and trade.direction.startswith("SELL")
        # Close short by buying back, close long by selling
        close_txn = self.kite.TRANSACTION_TYPE_BUY if is_sell else self.kite.TRANSACTION_TYPE_SELL
        try:
            order_id = self.kite.place_order(
                variety=self.kite.VARIETY_REGULAR,
                exchange=trade.exchange,
                tradingsymbol=trade.tradingsymbol,
                transaction_type=close_txn,
                quantity=trade.quantity,
                product=self.kite.PRODUCT_MIS,
                order_type=self.kite.ORDER_TYPE_MARKET,
                validity=self.kite.VALIDITY_DAY,
                tag=f"exit-{reason}",
            )

            quote = self.kite.ltp([f"{trade.exchange}:{trade.tradingsymbol}"])
            key = f"{trade.exchange}:{trade.tradingsymbol}"
            exit_price = quote[key]["last_price"]

            self.risk.record_trade_close(trade, exit_price, reason)
            logger.info(f"EXIT ORDER: {order_id} - {trade.tradingsymbol} @ ₹{exit_price}")
            return True
        except Exception as e:
            logger.error(f"Exit order failed: {e}")
            return False

    def _find_tradingsymbol(
        self, instrument: str, strike: float, direction: str, expiry: str
    ) -> Optional[str]:
        cfg = self.config["instruments"][instrument]
        exchange = cfg["exchange"]
        underlying = cfg["underlying"]
        opt_type = "CE" if "CE" in direction else "PE"

        if exchange not in self._instruments_cache:
            try:
                instruments = self.kite.instruments(exchange)
                self._instruments_cache[exchange] = instruments
            except Exception as e:
                logger.error(f"Failed to fetch instruments: {e}")
                return None

        matches = [
            inst for inst in self._instruments_cache[exchange]
            if inst["name"] == underlying
            and inst["instrument_type"] == opt_type
            and inst["strike"] == strike
        ]

        if not matches:
            return None

        if expiry:
            for inst in matches:
                inst_expiry = inst.get("expiry", "")
                if hasattr(inst_expiry, "strftime"):
                    inst_expiry = inst_expiry.strftime("%d-%b-%Y")
                if expiry in str(inst_expiry):
                    return inst["tradingsymbol"]

        return matches[0]["tradingsymbol"]

        return None

    @staticmethod
    def _get_exchange(instrument: str) -> str:
        exchange_map = {"nifty50": "NFO", "sensex": "BFO", "crude_oil": "MCX"}
        return exchange_map.get(instrument, "NFO")
