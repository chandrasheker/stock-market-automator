"""Backtesting engine with per-instrument optimized entry rules."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import pandas as pd
from loguru import logger

from src.analysis.technical import TechnicalAnalyzer
from src.backtest.entry_rules import EntryRuleEngine
from src.config import get_env, get_yaml_config


@dataclass
class BacktestTrade:
    instrument: str
    entry_date: str
    exit_date: str
    direction: str
    entry_price: float
    exit_price: float
    pnl: float
    pnl_pct: float
    exit_reason: str
    win: bool


@dataclass
class BacktestResult:
    instrument: str = ""
    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    total_pnl: float = 0.0
    max_drawdown: float = 0.0
    win_rate: float = 0.0
    avg_profit: float = 0.0
    avg_loss: float = 0.0
    sharpe_ratio: float = 0.0
    profit_factor: float = 0.0
    target_hits: int = 0
    stop_loss_hits: int = 0
    time_exits: int = 0
    trades: list = field(default_factory=list)


@dataclass
class CombinedBacktestResult:
    instruments: dict = field(default_factory=dict)
    combined_win_rate: float = 0.0
    combined_trades: int = 0
    combined_pnl: float = 0.0
    combined_sharpe: float = 0.0


class BacktestEngine:
    """Simulates options strategy on historical index data."""

    def __init__(
        self,
        profit_target_pct: Optional[float] = None,
        stop_loss_pct: Optional[float] = None,
    ):
        self.env = get_env()
        self.config = get_yaml_config()
        self.technical = TechnicalAnalyzer()
        self.entry_rules = EntryRuleEngine()
        bt_defaults = self.config.get("backtest", {}).get("defaults", {})
        self.profit_target = (profit_target_pct or bt_defaults.get(
            "profit_target_pct", self.env.profit_target_pct
        )) / 100
        self.stop_loss_default = (stop_loss_pct or bt_defaults.get(
            "stop_loss_pct", self.env.stop_loss_pct
        )) / 100

    def run(self, df: pd.DataFrame, instrument: str = "nifty50") -> BacktestResult:
        inst_cfg = self.config["instruments"].get(instrument, {})
        lot_size = inst_cfg.get("lot_size", 25)
        bt_cfg = self.entry_rules.get_instrument_config(instrument)
        stop_loss = bt_cfg.get("stop_loss_pct", self.stop_loss_default * 100) / 100
        max_hold = bt_cfg.get("max_hold_days", 5)

        if len(df) < 60:
            logger.warning(f"Insufficient data for {instrument} (need 60+ days)")
            return BacktestResult(instrument=instrument)

        df = self.technical.add_all_indicators(df)
        trades: list[BacktestTrade] = []
        equity_curve = [self.env.capital]

        in_trade = False
        entry_idx = 0
        entry_premium = 0.0
        direction = 0
        trade_direction = ""

        for i in range(60, len(df)):
            row = df.iloc[i]
            window = df.iloc[max(0, i - 50) : i + 1]
            latest = window.iloc[-1]
            atr_pct = latest["atr"] / latest["close"] if latest["close"] > 0 else 0.01

            if in_trade:
                current_premium = self._simulate_premium(
                    df.iloc[entry_idx]["close"],
                    row["close"],
                    direction,
                    entry_premium,
                    atr_pct,
                )
                pnl_pct = (current_premium - entry_premium) / entry_premium

                exit_reason = None
                if pnl_pct >= self.profit_target:
                    exit_reason = "TARGET"
                elif pnl_pct <= -stop_loss:
                    exit_reason = "STOP_LOSS"
                elif i - entry_idx >= max_hold:
                    exit_reason = "TIME_EXIT"

                if exit_reason:
                    pnl = (current_premium - entry_premium) * lot_size
                    trades.append(BacktestTrade(
                        instrument=instrument,
                        entry_date=str(df.iloc[entry_idx]["timestamp"]),
                        exit_date=str(row["timestamp"]),
                        direction=trade_direction,
                        entry_price=round(entry_premium, 2),
                        exit_price=round(current_premium, 2),
                        pnl=round(pnl, 2),
                        pnl_pct=round(pnl_pct * 100, 2),
                        exit_reason=exit_reason,
                        win=pnl > 0,
                    ))
                    equity_curve.append(equity_curve[-1] + pnl)
                    in_trade = False
            else:
                trend = self.technical.get_trend_signal(window)
                breakout = self.technical.detect_breakout(window)
                signal = self.entry_rules.evaluate(
                    instrument, trend, breakout, latest, window
                )
                if signal:
                    direction = 1 if signal == "BULLISH" else -1
                    trade_direction = f"BUY_{'CE' if direction == 1 else 'PE'}"
                    entry_premium = row["close"] * 0.005
                    entry_idx = i
                    in_trade = True

        return self._build_result(instrument, trades, equity_curve)

    def run_all(self, data: Optional[dict[str, pd.DataFrame]] = None) -> CombinedBacktestResult:
        from src.data.historical import HistoricalDataFetcher

        fetcher = HistoricalDataFetcher()
        combined = CombinedBacktestResult()
        all_trades: list[BacktestTrade] = []
        all_equity = [self.env.capital]

        for instrument, cfg in self.config["instruments"].items():
            if not cfg.get("enabled", True):
                continue

            df = data[instrument] if data and instrument in data else fetcher.fetch_index_history(instrument)
            result = self.run(df, instrument)
            combined.instruments[instrument] = result
            all_trades.extend(result.trades)
            for t in result.trades:
                all_equity.append(all_equity[-1] + t.pnl)

        if all_trades:
            combined.combined_trades = len(all_trades)
            combined.combined_win_rate = sum(1 for t in all_trades if t.win) / len(all_trades)
            combined.combined_pnl = sum(t.pnl for t in all_trades)
            returns = np.diff(np.array(all_equity)) / np.array(all_equity[:-1])
            if len(returns) > 1 and returns.std() > 0:
                combined.combined_sharpe = float(
                    (returns.mean() / returns.std()) * np.sqrt(252)
                )

        return combined

    @staticmethod
    def _simulate_premium(
        entry_close: float,
        current_close: float,
        direction: int,
        entry_premium: float,
        atr_pct: float,
    ) -> float:
        """Simulate option premium movement based on underlying price change."""
        price_change = (current_close - entry_close) / entry_close
        leverage = 3.5 + atr_pct * 50
        if direction == 1:  # CE
            return max(0.05, entry_premium * (1 + price_change * leverage))
        return max(0.05, entry_premium * (1 - price_change * leverage))

    def _build_result(
        self, instrument: str, trades: list[BacktestTrade], equity_curve: list
    ) -> BacktestResult:
        result = BacktestResult(instrument=instrument, trades=trades)
        if not trades:
            return result

        result.total_trades = len(trades)
        result.winning_trades = sum(1 for t in trades if t.win)
        result.losing_trades = result.total_trades - result.winning_trades
        result.win_rate = result.winning_trades / result.total_trades
        result.total_pnl = sum(t.pnl for t in trades)
        result.target_hits = sum(1 for t in trades if t.exit_reason == "TARGET")
        result.stop_loss_hits = sum(1 for t in trades if t.exit_reason == "STOP_LOSS")
        result.time_exits = sum(1 for t in trades if t.exit_reason == "TIME_EXIT")

        profits = [t.pnl for t in trades if t.pnl > 0]
        losses = [t.pnl for t in trades if t.pnl <= 0]
        result.avg_profit = np.mean(profits) if profits else 0
        result.avg_loss = np.mean(losses) if losses else 0

        gross_profit = sum(profits) if profits else 0
        gross_loss = abs(sum(losses)) if losses else 1
        result.profit_factor = gross_profit / gross_loss if gross_loss > 0 else 0

        equity = np.array(equity_curve)
        peak = np.maximum.accumulate(equity)
        drawdown = (peak - equity) / peak
        result.max_drawdown = float(drawdown.max()) * 100

        returns = np.diff(equity) / equity[:-1]
        if len(returns) > 1 and returns.std() > 0:
            result.sharpe_ratio = float((returns.mean() / returns.std()) * np.sqrt(252))

        return result

    def print_report(self, combined: CombinedBacktestResult):
        print("\n" + "=" * 65)
        print("  BACKTEST REPORT — Per-Instrument Optimized Strategy")
        print("=" * 65)

        for inst, result in combined.instruments.items():
            name = self.config["instruments"][inst]["index_name"]
            print(f"\n  {name} ({inst})")
            print(f"  {'─' * 40}")
            print(f"  Trades:         {result.total_trades}")
            print(f"  Win Rate:       {result.win_rate:.1%}")
            print(f"  Total P&L:      ₹{result.total_pnl:,.0f}")
            print(f"  Profit Factor:  {result.profit_factor:.2f}")
            print(f"  Max Drawdown:   {result.max_drawdown:.1f}%")
            print(f"  Target Hits:    {result.target_hits}")
            print(f"  Stop Losses:    {result.stop_loss_hits}")
            print(f"  Time Exits:     {result.time_exits}")

        print(f"\n  {'═' * 40}")
        print(f"  COMBINED RESULTS")
        print(f"  {'═' * 40}")
        print(f"  Total Trades:   {combined.combined_trades}")
        print(f"  Win Rate:       {combined.combined_win_rate:.1%}")
        print(f"  Total P&L:      ₹{combined.combined_pnl:,.0f}")
        print(f"  Sharpe Ratio:   {combined.combined_sharpe:.2f}")
        print("=" * 65 + "\n")
