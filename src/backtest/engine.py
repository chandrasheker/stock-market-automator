"""Backtesting engine for strategy validation."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

import numpy as np
import pandas as pd
from loguru import logger

from src.analysis.technical import TechnicalAnalyzer
from src.config import get_env


@dataclass
class BacktestResult:
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
    trades: list = field(default_factory=list)


class BacktestEngine:
    """Simulates strategy on historical data."""

    def __init__(self, profit_target_pct: float = 20.0, stop_loss_pct: float = 15.0):
        self.profit_target = profit_target_pct / 100
        self.stop_loss = stop_loss_pct / 100
        self.technical = TechnicalAnalyzer()
        self.env = get_env()

    def run(self, df: pd.DataFrame, lot_size: int = 25) -> BacktestResult:
        if len(df) < 60:
            logger.warning("Insufficient data for backtest (need 60+ days)")
            return BacktestResult()

        df = self.technical.add_all_indicators(df)
        result = BacktestResult()
        equity_curve = [self.env.capital]
        in_trade = False
        entry_price = 0.0
        entry_idx = 0

        for i in range(50, len(df)):
            row = df.iloc[i]

            if in_trade:
                current_premium = self._estimate_premium(df, i, entry_idx)
                pnl_pct = (current_premium - entry_price) / entry_price

                exit_reason = None
                if pnl_pct >= self.profit_target:
                    exit_reason = "TARGET"
                elif pnl_pct <= -self.stop_loss:
                    exit_reason = "STOP_LOSS"
                elif i - entry_idx >= 5:
                    exit_reason = "TIME_EXIT"

                if exit_reason:
                    pnl = (current_premium - entry_price) * lot_size
                    result.total_trades += 1
                    result.total_pnl += pnl
                    if pnl > 0:
                        result.winning_trades += 1
                    else:
                        result.losing_trades += 1
                    result.trades.append({
                        "entry_date": str(df.iloc[entry_idx]["timestamp"]),
                        "exit_date": str(row["timestamp"]),
                        "entry_price": entry_price,
                        "exit_price": current_premium,
                        "pnl": pnl,
                        "reason": exit_reason,
                    })
                    equity_curve.append(equity_curve[-1] + pnl)
                    in_trade = False
            else:
                window = df.iloc[max(0, i - 50):i + 1]
                trend = self.technical.get_trend_signal(window)
                breakout = self.technical.detect_breakout(window)

                signal = False
                if trend["direction"] in ("BULLISH", "BEARISH") and trend["strength"] > 0.3:
                    signal = True
                if breakout.get("breakout") and breakout.get("strength", 0) > 0.001:
                    signal = True

                if signal:
                    entry_price = row["close"] * 0.005
                    entry_idx = i
                    in_trade = True

        result = self._compute_metrics(result, equity_curve)
        return result

    def _estimate_premium(self, df: pd.DataFrame, current_idx: int, entry_idx: int) -> float:
        entry_close = df.iloc[entry_idx]["close"]
        current_close = df.iloc[current_idx]["close"]
        base_premium = entry_close * 0.005
        price_change_pct = (current_close - entry_close) / entry_close
        return max(0.05, base_premium * (1 + price_change_pct * 3))

    def _compute_metrics(self, result: BacktestResult, equity_curve: list) -> BacktestResult:
        if result.total_trades == 0:
            return result

        result.win_rate = result.winning_trades / result.total_trades
        profits = [t["pnl"] for t in result.trades if t["pnl"] > 0]
        losses = [t["pnl"] for t in result.trades if t["pnl"] <= 0]
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
