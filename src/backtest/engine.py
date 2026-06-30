"""Backtesting engine with costs, option selling, and news-aware filters."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import pandas as pd
from loguru import logger

from src.analysis.technical import TechnicalAnalyzer
from src.backtest.entry_rules import EntryRuleEngine
from src.config import get_env, get_yaml_config
from src.costs.calculator import CostCalculator


@dataclass
class BacktestTrade:
    instrument: str
    entry_date: str
    exit_date: str
    direction: str
    trade_mode: str
    entry_price: float
    exit_price: float
    gross_pnl: float
    costs: float
    net_pnl: float
    pnl_pct: float
    exit_reason: str
    win: bool


@dataclass
class BacktestResult:
    instrument: str = ""
    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    gross_pnl: float = 0.0
    total_costs: float = 0.0
    total_pnl: float = 0.0
    max_drawdown: float = 0.0
    win_rate: float = 0.0
    net_win_rate: float = 0.0
    avg_profit: float = 0.0
    avg_loss: float = 0.0
    sharpe_ratio: float = 0.0
    profit_factor: float = 0.0
    target_hits: int = 0
    stop_loss_hits: int = 0
    time_exits: int = 0
    buy_trades: int = 0
    sell_trades: int = 0
    trades: list = field(default_factory=list)


@dataclass
class CombinedBacktestResult:
    instruments: dict = field(default_factory=dict)
    combined_win_rate: float = 0.0
    combined_net_win_rate: float = 0.0
    combined_trades: int = 0
    combined_gross_pnl: float = 0.0
    combined_costs: float = 0.0
    combined_pnl: float = 0.0
    combined_sharpe: float = 0.0
    news_summary: dict = field(default_factory=dict)
    strategy_approved: bool = True


class BacktestEngine:
    """Simulates options buying and selling with full Indian tax/charge modeling."""

    def __init__(
        self,
        profit_target_pct: Optional[float] = None,
        stop_loss_pct: Optional[float] = None,
    ):
        self.env = get_env()
        self.config = get_yaml_config()
        self.technical = TechnicalAnalyzer()
        self.entry_rules = EntryRuleEngine()
        self.costs = CostCalculator()
        bt_defaults = self.config.get("backtest", {}).get("defaults", {})
        self.profit_target = (profit_target_pct or bt_defaults.get(
            "profit_target_pct", self.env.profit_target_pct
        )) / 100
        self.stop_loss_default = (stop_loss_pct or bt_defaults.get(
            "stop_loss_pct", self.env.stop_loss_pct
        )) / 100
        sell_cfg = self.config.get("option_selling", {})
        self.sell_profit_target = sell_cfg.get("profit_target_pct", 50.0) / 100
        self.sell_stop_loss = sell_cfg.get("stop_loss_pct", 80.0) / 100

    def run(self, df: pd.DataFrame, instrument: str = "nifty50") -> BacktestResult:
        inst_cfg = self.config["instruments"].get(instrument, {})
        lot_size = inst_cfg.get("lot_size", 25)
        exchange = inst_cfg.get("exchange", "NFO")
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
        trade_mode = "BUY_OPTION"
        trade_direction = ""

        # Simulated VIX proxy from ATR history
        df["atr_pct"] = df["atr"] / df["close"]

        for i in range(60, len(df)):
            row = df.iloc[i]
            window = df.iloc[max(0, i - 50) : i + 1]
            latest = window.iloc[-1]
            atr_pct = float(latest["atr_pct"])
            vix_proxy = 12 + atr_pct * 400  # maps ~0.015 ATR% → VIX ~18

            if in_trade:
                current_premium = self._simulate_premium(
                    df.iloc[entry_idx]["close"],
                    row["close"],
                    direction,
                    entry_premium,
                    atr_pct,
                    trade_mode,
                    i - entry_idx,
                )
                pnl_pct = self._pnl_pct(entry_premium, current_premium, trade_mode)
                target = self.sell_profit_target if trade_mode == "SELL_OPTION" else self.profit_target
                sl = self.sell_stop_loss if trade_mode == "SELL_OPTION" else stop_loss

                exit_reason = None
                if pnl_pct >= target:
                    exit_reason = "TARGET"
                elif pnl_pct <= -sl:
                    exit_reason = "STOP_LOSS"
                elif i - entry_idx >= max_hold:
                    exit_reason = "TIME_EXIT"

                if exit_reason:
                    gross, cost_obj = self.costs.net_pnl(
                        entry_premium, current_premium, lot_size, exchange, trade_mode
                    )
                    gross_only = self.costs.gross_pnl(
                        entry_premium, current_premium, lot_size, trade_mode
                    )
                    net = gross
                    costs_total = cost_obj.total

                    trades.append(BacktestTrade(
                        instrument=instrument,
                        entry_date=str(df.iloc[entry_idx]["timestamp"]),
                        exit_date=str(row["timestamp"]),
                        direction=trade_direction,
                        trade_mode=trade_mode,
                        entry_price=round(entry_premium, 2),
                        exit_price=round(current_premium, 2),
                        gross_pnl=round(gross_only, 2),
                        costs=round(costs_total, 2),
                        net_pnl=round(net, 2),
                        pnl_pct=round(pnl_pct * 100, 2),
                        exit_reason=exit_reason,
                        win=net > 0,
                    ))
                    equity_curve.append(equity_curve[-1] + net)
                    in_trade = False
            else:
                trend = self.technical.get_trend_signal(window)
                breakout = self.technical.detect_breakout(window)
                setup = self.entry_rules.evaluate(
                    instrument, trend, breakout, latest, window,
                    vix=vix_proxy, news_sentiment={"score": 0},
                )
                if not setup:
                    continue

                trade_mode = setup["mode"]
                opt_type = setup["opt_type"]
                trade_direction = (
                    f"{'SELL' if trade_mode == 'SELL_OPTION' else 'BUY'}_{opt_type}"
                )
                direction = 1 if setup["direction"] == "BULLISH" else -1
                entry_premium = row["close"] * 0.005

                # Cost gate: skip if expected target won't cover charges
                if trade_mode == "BUY_OPTION":
                    target_p = entry_premium * (1 + self.profit_target)
                else:
                    target_p = entry_premium * (1 - self.sell_profit_target)

                approved, _ = self.costs.is_trade_worth_it(
                    entry_premium, target_p, lot_size, exchange, trade_mode
                )
                if not approved:
                    continue

                entry_idx = i
                in_trade = True

        return self._build_result(instrument, trades, equity_curve)

    def run_all(self, data: Optional[dict[str, pd.DataFrame]] = None) -> CombinedBacktestResult:
        from src.data.historical import HistoricalDataFetcher
        from src.data.news_fetcher import NewsFetcher

        fetcher = HistoricalDataFetcher()
        news_fetcher = NewsFetcher()
        combined = CombinedBacktestResult()
        all_trades: list[BacktestTrade] = []
        all_equity = [self.env.capital]

        news_summary = {}
        for inst, news_key in [("nifty50", "nifty"), ("sensex", "sensex"), ("crude_oil", "crude")]:
            try:
                news_summary[inst] = news_fetcher.get_instrument_sentiment(news_key)
            except Exception:
                news_summary[inst] = {"score": 0, "article_count": 0}
        combined.news_summary = news_summary

        for instrument, cfg in self.config["instruments"].items():
            if not cfg.get("enabled", True):
                continue

            df = data[instrument] if data and instrument in data else fetcher.fetch_index_history(instrument)
            result = self.run(df, instrument)
            combined.instruments[instrument] = result
            all_trades.extend(result.trades)
            for t in result.trades:
                all_equity.append(all_equity[-1] + t.net_pnl)

        if all_trades:
            combined.combined_trades = len(all_trades)
            combined.combined_win_rate = sum(1 for t in all_trades if t.gross_pnl > 0) / len(all_trades)
            combined.combined_net_win_rate = sum(1 for t in all_trades if t.win) / len(all_trades)
            combined.combined_gross_pnl = sum(t.gross_pnl for t in all_trades)
            combined.combined_costs = sum(t.costs for t in all_trades)
            combined.combined_pnl = sum(t.net_pnl for t in all_trades)
            combined.strategy_approved = combined.combined_pnl > 0

            returns = np.diff(np.array(all_equity)) / np.array(all_equity[:-1])
            if len(returns) > 1 and returns.std() > 0:
                combined.combined_sharpe = float(
                    (returns.mean() / returns.std()) * np.sqrt(252)
                )

        return combined

    def _pnl_pct(self, entry: float, current: float, trade_mode: str) -> float:
        if trade_mode == "BUY_OPTION":
            return (current - entry) / entry
        return (entry - current) / entry

    def _simulate_premium(
        self,
        entry_close: float,
        current_close: float,
        direction: int,
        entry_premium: float,
        atr_pct: float,
        trade_mode: str,
        days_held: int,
    ) -> float:
        price_change = (current_close - entry_close) / entry_close
        leverage = 3.5 + atr_pct * 50
        theta_decay = 0.02 * days_held  # daily time decay proxy

        if trade_mode == "BUY_OPTION":
            if direction == 1:
                premium = entry_premium * (1 + price_change * leverage)
            else:
                premium = entry_premium * (1 - price_change * leverage)
            return max(0.05, premium * (1 - theta_decay * 0.3))

        # SELL_OPTION: theta helps seller, adverse move hurts
        if direction == 1:  # sold PE, hurt by down move
            adverse = max(0, -price_change * leverage)
            favorable = theta_decay
            return max(0.05, entry_premium * (1 + adverse - favorable))
        else:  # sold CE, hurt by up move
            adverse = max(0, price_change * leverage)
            favorable = theta_decay
            return max(0.05, entry_premium * (1 + adverse - favorable))

    def _build_result(
        self, instrument: str, trades: list[BacktestTrade], equity_curve: list
    ) -> BacktestResult:
        result = BacktestResult(instrument=instrument, trades=trades)
        if not trades:
            return result

        result.total_trades = len(trades)
        result.winning_trades = sum(1 for t in trades if t.win)
        result.losing_trades = result.total_trades - result.winning_trades
        result.win_rate = sum(1 for t in trades if t.gross_pnl > 0) / result.total_trades
        result.net_win_rate = result.winning_trades / result.total_trades
        result.gross_pnl = sum(t.gross_pnl for t in trades)
        result.total_costs = sum(t.costs for t in trades)
        result.total_pnl = sum(t.net_pnl for t in trades)
        result.buy_trades = sum(1 for t in trades if t.trade_mode == "BUY_OPTION")
        result.sell_trades = sum(1 for t in trades if t.trade_mode == "SELL_OPTION")
        result.target_hits = sum(1 for t in trades if t.exit_reason == "TARGET")
        result.stop_loss_hits = sum(1 for t in trades if t.exit_reason == "STOP_LOSS")
        result.time_exits = sum(1 for t in trades if t.exit_reason == "TIME_EXIT")

        profits = [t.net_pnl for t in trades if t.net_pnl > 0]
        losses = [t.net_pnl for t in trades if t.net_pnl <= 0]
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
        print("\n" + "=" * 70)
        print("  BACKTEST REPORT — Net P&L After STT, GST, Brokerage & Charges")
        print("=" * 70)

        if combined.news_summary:
            print("\n  Today's News Sentiment:")
            for inst, news in combined.news_summary.items():
                score = news.get("score", 0)
                emoji = "+" if score > 0.1 else "-" if score < -0.1 else "~"
                print(f"    {inst}: {emoji} {score:+.3f} ({news.get('article_count', 0)} articles)")

        for inst, result in combined.instruments.items():
            name = self.config["instruments"][inst]["index_name"]
            print(f"\n  {name} ({inst})")
            print(f"  {'─' * 44}")
            print(f"  Trades:         {result.total_trades} (Buy: {result.buy_trades}, Sell: {result.sell_trades})")
            print(f"  Win Rate:       {result.net_win_rate:.1%} net | {result.win_rate:.1%} gross")
            print(f"  Gross P&L:      ₹{result.gross_pnl:,.0f}")
            print(f"  Total Costs:    ₹{result.total_costs:,.0f}")
            print(f"  Net P&L:        ₹{result.total_pnl:,.0f}")
            print(f"  Profit Factor:  {result.profit_factor:.2f}")
            print(f"  Max Drawdown:   {result.max_drawdown:.1f}%")

        print(f"\n  {'═' * 44}")
        print(f"  COMBINED (after all taxes & charges)")
        print(f"  {'═' * 44}")
        print(f"  Total Trades:   {combined.combined_trades}")
        print(f"  Net Win Rate:   {combined.combined_net_win_rate:.1%}")
        print(f"  Gross P&L:      ₹{combined.combined_gross_pnl:,.0f}")
        print(f"  Total Costs:    ₹{combined.combined_costs:,.0f}")
        print(f"  Net P&L:        ₹{combined.combined_pnl:,.0f}")
        print(f"  Sharpe Ratio:   {combined.combined_sharpe:.2f}")
        status = "APPROVED" if combined.strategy_approved else "CAUTION — net negative"
        print(f"  Strategy:       {status}")
        print("=" * 70 + "\n")
