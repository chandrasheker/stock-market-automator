"""Indian F&O transaction cost calculator (Zerodha-style)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from src.config import get_yaml_config

TradeSide = Literal["BUY", "SELL"]
Exchange = Literal["NFO", "BFO", "MCX"]


@dataclass
class LegCosts:
    turnover: float = 0.0
    brokerage: float = 0.0
    stt: float = 0.0
    exchange_charges: float = 0.0
    stamp_duty: float = 0.0
    sebi_charges: float = 0.0
    gst: float = 0.0

    @property
    def total(self) -> float:
        return (
            self.brokerage
            + self.stt
            + self.exchange_charges
            + self.stamp_duty
            + self.sebi_charges
            + self.gst
        )


@dataclass
class RoundTripCosts:
    entry_leg: LegCosts = field(default_factory=LegCosts)
    exit_leg: LegCosts = field(default_factory=LegCosts)
    trade_type: str = "BUY_OPTION"

    @property
    def total(self) -> float:
        return self.entry_leg.total + self.exit_leg.total

    @property
    def breakdown(self) -> dict:
        return {
            "brokerage": self.entry_leg.brokerage + self.exit_leg.brokerage,
            "stt": self.entry_leg.stt + self.exit_leg.stt,
            "exchange_charges": self.entry_leg.exchange_charges + self.exit_leg.exchange_charges,
            "stamp_duty": self.entry_leg.stamp_duty + self.exit_leg.stamp_duty,
            "sebi_charges": self.entry_leg.sebi_charges + self.exit_leg.sebi_charges,
            "gst": self.entry_leg.gst + self.exit_leg.gst,
            "total": self.total,
        }


class CostCalculator:
    """Computes all-in F&O costs for NSE/BSE/MCX options."""

    EXCHANGE_RATES = {
        "NFO": 0.0000297,   # NSE F&O transaction charge
        "BFO": 0.00005,     # BSE F&O (approx)
        "MCX": 0.000021,    # MCX commodity options (approx)
    }
    STT_OPTIONS_SELL = 0.000625  # 0.0625% on sell side premium
    STAMP_DUTY_BUY = 0.00003     # 0.003% on buy side
    SEBI_PER_CRORE = 10.0
    GST_RATE = 0.18

    def __init__(self):
        cfg = get_yaml_config().get("costs", {})
        self.brokerage_flat = cfg.get("brokerage_flat", 20.0)
        self.brokerage_pct = cfg.get("brokerage_pct", 0.0003)
        self.min_net_profit_multiplier = cfg.get("min_net_profit_multiplier", 2.0)

    def leg_cost(
        self,
        premium: float,
        quantity: int,
        exchange: Exchange,
        side: TradeSide,
    ) -> LegCosts:
        turnover = premium * quantity
        brokerage = min(self.brokerage_flat, turnover * self.brokerage_pct)

        stt = 0.0
        if side == "SELL":
            stt = turnover * self.STT_OPTIONS_SELL

        exchange_rate = self.EXCHANGE_RATES.get(exchange, self.EXCHANGE_RATES["NFO"])
        exchange_charges = turnover * exchange_rate

        stamp_duty = turnover * self.STAMP_DUTY_BUY if side == "BUY" else 0.0
        sebi_charges = turnover * (self.SEBI_PER_CRORE / 1e7)

        taxable = brokerage + exchange_charges + sebi_charges
        gst = taxable * self.GST_RATE

        return LegCosts(
            turnover=turnover,
            brokerage=round(brokerage, 2),
            stt=round(stt, 2),
            exchange_charges=round(exchange_charges, 2),
            stamp_duty=round(stamp_duty, 2),
            sebi_charges=round(sebi_charges, 4),
            gst=round(gst, 2),
        )

    def round_trip(
        self,
        entry_premium: float,
        exit_premium: float,
        quantity: int,
        exchange: Exchange,
        trade_type: Literal["BUY_OPTION", "SELL_OPTION"] = "BUY_OPTION",
    ) -> RoundTripCosts:
        if trade_type == "BUY_OPTION":
            entry_leg = self.leg_cost(entry_premium, quantity, exchange, "BUY")
            exit_leg = self.leg_cost(exit_premium, quantity, exchange, "SELL")
        else:
            entry_leg = self.leg_cost(entry_premium, quantity, exchange, "SELL")
            exit_leg = self.leg_cost(exit_premium, quantity, exchange, "BUY")

        return RoundTripCosts(
            entry_leg=entry_leg,
            exit_leg=exit_leg,
            trade_type=trade_type,
        )

    def estimate_round_trip_cost(
        self,
        premium: float,
        quantity: int,
        exchange: Exchange,
        trade_type: Literal["BUY_OPTION", "SELL_OPTION"] = "BUY_OPTION",
        profit_pct: float = 20.0,
    ) -> float:
        """Estimate costs for a planned trade before execution."""
        if trade_type == "BUY_OPTION":
            exit_premium = premium * (1 + profit_pct / 100)
        else:
            exit_premium = premium * (1 - profit_pct / 100)

        return self.round_trip(
            premium, exit_premium, quantity, exchange, trade_type
        ).total

    def gross_pnl(
        self,
        entry_premium: float,
        exit_premium: float,
        quantity: int,
        trade_type: Literal["BUY_OPTION", "SELL_OPTION"] = "BUY_OPTION",
    ) -> float:
        if trade_type == "BUY_OPTION":
            return (exit_premium - entry_premium) * quantity
        return (entry_premium - exit_premium) * quantity

    def net_pnl(
        self,
        entry_premium: float,
        exit_premium: float,
        quantity: int,
        exchange: Exchange,
        trade_type: Literal["BUY_OPTION", "SELL_OPTION"] = "BUY_OPTION",
    ) -> tuple[float, RoundTripCosts]:
        gross = self.gross_pnl(entry_premium, exit_premium, quantity, trade_type)
        costs = self.round_trip(entry_premium, exit_premium, quantity, exchange, trade_type)
        return round(gross - costs.total, 2), costs

    def is_trade_worth_it(
        self,
        entry_premium: float,
        target_premium: float,
        quantity: int,
        exchange: Exchange,
        trade_type: Literal["BUY_OPTION", "SELL_OPTION"] = "BUY_OPTION",
    ) -> tuple[bool, dict]:
        """Only approve trades where net profit exceeds costs by a safety margin."""
        gross = self.gross_pnl(entry_premium, target_premium, quantity, trade_type)
        costs = self.round_trip(entry_premium, target_premium, quantity, exchange, trade_type)
        net = gross - costs.total
        min_required = costs.total * self.min_net_profit_multiplier

        approved = net > 0 and gross >= min_required
        return approved, {
            "gross_pnl": round(gross, 2),
            "total_costs": round(costs.total, 2),
            "net_pnl": round(net, 2),
            "min_required_gross": round(min_required, 2),
            "cost_breakdown": costs.breakdown,
            "approved": approved,
        }
