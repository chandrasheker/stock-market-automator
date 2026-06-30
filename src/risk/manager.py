"""Risk management: position sizing, daily limits, kill switch."""

from __future__ import annotations

from datetime import datetime, date
from typing import Optional

from loguru import logger

from src.analysis.signal_engine import TradeOpportunity
from src.toggles import is_any_buy_enabled
from src.config import get_env, get_yaml_config
from src.costs.calculator import CostCalculator
from src.data.database import DailyPnL, Trade, get_session, init_db


class RiskManager:
    """Enforces risk limits before every trade."""

    def __init__(self):
        self.env = get_env()
        self.config = get_yaml_config()
        self.costs = CostCalculator()
        init_db()
        self._kill_switch = False

    def activate_kill_switch(self, reason: str = "Manual"):
        self._kill_switch = True
        logger.warning(f"KILL SWITCH ACTIVATED: {reason}")

    def deactivate_kill_switch(self):
        self._kill_switch = False
        logger.info("Kill switch deactivated")

    @property
    def is_killed(self) -> bool:
        return self._kill_switch

    def can_trade(self, opportunity: TradeOpportunity) -> tuple[bool, str]:
        if self._kill_switch:
            return False, "Kill switch is active"

        # Daily backtest gate
        bt_cfg = self.config.get("daily_backtest", {})
        if bt_cfg.get("block_trading_if_negative", True):
            from src.backtest.daily_runner import DailyBacktestRunner
            allowed, reason = DailyBacktestRunner().is_trading_allowed()
            if not allowed:
                return False, reason

        if not opportunity.is_recommended:
            return False, "Buy trades disabled — app recommends SELL only"

        if opportunity.trade_mode == "BUY_OPTION" and not is_any_buy_enabled(opportunity.instrument):
            return False, "Buy CE/PE is off — enable in Settings if you accept the risk"

        profit_cfg = self.config.get("profit_mode", {})
        min_conf = profit_cfg.get("min_sell_confidence", 0.70) if opportunity.trade_mode == "SELL_OPTION" else 0.65
        if opportunity.confidence < min_conf:
            return False, f"Confidence too low: {opportunity.confidence:.2f}"

        if profit_cfg.get("enabled"):
            max_daily = profit_cfg.get("max_trades_per_day", 2)
            today_trades = self._count_today_trades()
            if today_trades >= max_daily:
                return False, f"Daily trade limit reached ({today_trades}/{max_daily})"

        exchange = self.config["instruments"][opportunity.instrument].get("exchange", "NFO")
        approved, cost_info = self.costs.is_trade_worth_it(
            opportunity.entry_price,
            opportunity.target_price,
            opportunity.lot_size,
            exchange,
            opportunity.trade_mode,
        )
        if not approved:
            return False, (
                f"Net profit after STT/GST/brokerage too low "
                f"(costs ₹{cost_info['total_costs']:.0f}, gross ₹{cost_info['gross_pnl']:.0f})"
            )

        open_positions = self._count_open_positions()
        if open_positions >= self.env.max_open_positions:
            return False, f"Max open positions reached ({open_positions})"

        daily_loss = self._get_daily_pnl()
        max_loss = self.env.capital * (self.env.max_daily_loss_pct / 100)
        if daily_loss < -max_loss:
            return False, f"Daily loss limit hit: ₹{daily_loss:,.0f}"

        return True, "Approved"

    def calculate_position_size(self, opportunity: TradeOpportunity) -> int:
        max_risk_amount = self.env.capital * (self.env.max_risk_per_trade_pct / 100)
        risk_per_lot = (opportunity.entry_price - opportunity.stop_loss) * opportunity.lot_size

        if risk_per_lot <= 0:
            return opportunity.lot_size

        max_lots = int(max_risk_amount / risk_per_lot)
        return max(opportunity.lot_size, max_lots * opportunity.lot_size) if max_lots >= 1 else opportunity.lot_size

    def check_exit_conditions(self, trade: Trade, current_price: float) -> Optional[str]:
        if trade.status != "OPEN":
            return None

        is_sell = trade.direction and trade.direction.startswith("SELL")

        if is_sell:
            # Sold premium — profit when price falls, loss when it rises
            if current_price <= trade.target_price:
                return "TARGET_HIT"
            if current_price >= trade.stop_loss:
                return "STOP_LOSS"
            pnl_pct = ((trade.entry_price - current_price) / trade.entry_price) * 100
            sell_target = self.config.get("option_selling", {}).get("profit_target_pct", 50.0)
            if pnl_pct >= sell_target:
                return "PROFIT_TARGET"
        else:
            if current_price >= trade.target_price:
                return "TARGET_HIT"
            if current_price <= trade.stop_loss:
                return "STOP_LOSS"
            pnl_pct = ((current_price - trade.entry_price) / trade.entry_price) * 100
            if pnl_pct >= self.env.profit_target_pct:
                return "PROFIT_TARGET"

        if trade.entry_time:
            holding_minutes = (datetime.utcnow() - trade.entry_time).total_seconds() / 60
            if holding_minutes > 240:
                return "TIME_EXIT"

        return None

    def record_trade_close(self, trade: Trade, exit_price: float, reason: str):
        db = get_session()
        try:
            exchange = self.config["instruments"].get(trade.instrument, {}).get("exchange", "NFO")
            trade_mode = "SELL_OPTION" if trade.direction and trade.direction.startswith("SELL") else "BUY_OPTION"
            net_pnl, cost_obj = self.costs.net_pnl(
                trade.entry_price, exit_price, trade.quantity, exchange, trade_mode
            )

            trade.exit_price = exit_price
            trade.exit_time = datetime.utcnow()
            trade.exit_reason = reason
            trade.status = "CLOSED"
            trade.pnl = net_pnl
            gross = self.costs.gross_pnl(trade.entry_price, exit_price, trade.quantity, trade_mode)
            trade.pnl_pct = (gross / (trade.entry_price * trade.quantity)) * 100 if trade.entry_price else 0

            db.merge(trade)
            self._update_daily_pnl(trade.pnl, trade.pnl > 0)
            db.commit()
        except Exception as e:
            db.rollback()
            logger.error(f"Failed to record trade close: {e}")
        finally:
            db.close()

    def _count_open_positions(self) -> int:
        db = get_session()
        try:
            return db.query(Trade).filter_by(status="OPEN").count()
        finally:
            db.close()

    def _count_today_trades(self) -> int:
        db = get_session()
        try:
            today = date.today()
            return db.query(Trade).filter(
                Trade.entry_time >= datetime(today.year, today.month, today.day)
            ).count()
        finally:
            db.close()

    def _get_daily_pnl(self) -> float:
        db = get_session()
        try:
            today = date.today()
            record = db.query(DailyPnL).filter(
                DailyPnL.date >= datetime(today.year, today.month, today.day)
            ).first()
            return record.realized_pnl if record else 0.0
        finally:
            db.close()

    def _update_daily_pnl(self, pnl: float, is_win: bool):
        db = get_session()
        try:
            today = datetime(date.today().year, date.today().month, date.today().day)
            record = db.query(DailyPnL).filter_by(date=today).first()
            if not record:
                record = DailyPnL(date=today)
                db.add(record)
            record.realized_pnl += pnl
            record.trades_count += 1
            if is_win:
                record.win_count += 1
            db.commit()
        finally:
            db.close()

    def get_risk_summary(self) -> dict:
        return {
            "kill_switch": self._kill_switch,
            "open_positions": self._count_open_positions(),
            "max_positions": self.env.max_open_positions,
            "daily_pnl": self._get_daily_pnl(),
            "daily_loss_limit": self.env.capital * (self.env.max_daily_loss_pct / 100),
            "capital": self.env.capital,
            "profit_target_pct": self.env.profit_target_pct,
            "stop_loss_pct": self.env.stop_loss_pct,
        }
