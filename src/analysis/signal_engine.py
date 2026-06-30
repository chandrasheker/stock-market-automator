"""Composite signal scorer combining all analysis layers."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

import pandas as pd
from loguru import logger

from src.analysis.options_analyzer import OptionsAnalyzer
from src.analysis.technical import TechnicalAnalyzer
from src.backtest.entry_rules import EntryRuleEngine
from src.config import get_env, get_yaml_config
from src.toggles import is_action_enabled, is_any_buy_enabled, is_instrument_enabled
from src.costs.calculator import CostCalculator
from src.data.historical import HistoricalDataFetcher
from src.data.news_fetcher import NewsFetcher
from src.data.option_chain_live import LiveOptionChainService
from src.filters.pipeline import TradePipeline


@dataclass
class TradeOpportunity:
    instrument: str
    direction: str  # BUY_CE, BUY_PE, SELL_CE, SELL_PE
    trade_mode: str  # BUY_OPTION or SELL_OPTION
    confidence: float
    entry_price: float
    target_price: float
    stop_loss: float
    strike: float
    expiry: str
    lot_size: int
    estimated_costs: float = 0.0
    expected_net_pnl: float = 0.0
    strategy_scores: dict = field(default_factory=dict)
    reasoning: str = ""
    is_recommended: bool = True
    recommendation_note: str = ""
    estimated_margin: float = 0.0
    delta: float = 0.0
    pop: float = 0.0                  # probability of profit (0-1)
    expected_value: float = 0.0      # EV in ₹ after costs
    max_loss: float = 0.0            # ₹ loss if stop-loss hit (after costs)
    edge_score: float = 0.0          # composite 0-100
    verdict: str = "FAIR"            # STRONG / GOOD / FAIR / AVOID
    headline: str = ""               # one-line actionable suggestion
    iv_percentile: float = 0.0
    timestamp: datetime = field(default_factory=datetime.now)

    @property
    def expected_profit_pct(self) -> float:
        if self.entry_price <= 0:
            return 0
        if self.trade_mode == "SELL_OPTION":
            return ((self.entry_price - self.target_price) / self.entry_price) * 100
        return ((self.target_price - self.entry_price) / self.entry_price) * 100

    @property
    def risk_reward_ratio(self) -> float:
        if self.trade_mode == "SELL_OPTION":
            risk = self.stop_loss - self.entry_price
            reward = self.entry_price - self.target_price
        else:
            risk = self.entry_price - self.stop_loss
            reward = self.target_price - self.entry_price
        return reward / risk if risk > 0 else 0


class SignalEngine:
    """Combines technical, OI, news, IV analysis into cost-aware trade signals."""

    INSTRUMENT_NEWS_MAP = {
        "nifty50": "nifty",
        "sensex": "sensex",
        "crude_oil": "crude",
    }

    def __init__(self):
        self.config = get_yaml_config()
        self.env = get_env()
        self.technical = TechnicalAnalyzer()
        self.options = OptionsAnalyzer()
        self.entry_rules = EntryRuleEngine()
        self.costs = CostCalculator()
        self.data_fetcher = HistoricalDataFetcher()
        self.news = NewsFetcher()
        self.pipeline = TradePipeline()
        self.chain_service = LiveOptionChainService()
        self.profit_target = self.env.profit_target_pct / 100
        self.stop_loss = self.env.stop_loss_pct / 100
        sell_cfg = self.config.get("option_selling", {})
        self.sell_profit_target = sell_cfg.get("profit_target_pct", 50.0) / 100
        self.sell_stop_loss = sell_cfg.get("stop_loss_pct", 80.0) / 100
        self.min_confidence = self.config.get("backtest", {}).get("defaults", {}).get(
            "min_confidence", 0.65
        )
        profit_cfg = self.config.get("profit_mode", {})
        self.min_sell_confidence = profit_cfg.get("min_sell_confidence", 0.70)

    def scan_all(self, include_buy_suggestions: bool = False) -> list[TradeOpportunity]:
        """Return executable opportunities (sell-first). Optionally include buy as warnings."""
        opportunities = []
        all_insts = list(self.config["instruments"].keys())
        enabled = [k for k in all_insts if is_instrument_enabled(k)]
        disabled = [k for k in all_insts if k not in enabled]
        logger.info(
            f"Scanning {len(enabled)} instrument(s): {enabled}"
            + (f" | skipped (disabled): {disabled}" if disabled else "")
        )

        for instrument_key in all_insts:
            if not is_instrument_enabled(instrument_key):
                logger.info(f"  {instrument_key}: skipped — disabled in Settings")
                continue
            try:
                sell_opps = self._scan_sell_opportunities(instrument_key)
                opportunities.extend(sell_opps)
                if sell_opps:
                    logger.info(f"  {instrument_key}: ✅ {len(sell_opps)} SELL idea(s) found")

                if include_buy_suggestions:
                    buy_shadow = self._scan_buy_shadow(instrument_key)
                    if buy_shadow:
                        opportunities.append(buy_shadow)
            except Exception as e:
                logger.error(f"Scan failed for {instrument_key}: {e}")

        # Recommended (sell) first, then non-recommended suggestions
        return sorted(
            opportunities,
            key=lambda x: (not x.is_recommended, -x.confidence),
        )

    def _scan_sell_opportunities(self, instrument_key: str) -> list[TradeOpportunity]:
        """Scan SELL CE / SELL PE — only recommended trades."""
        vix = self.data_fetcher.fetch_india_vix()
        news_sentiment = self.news.get_instrument_sentiment(
            self.INSTRUMENT_NEWS_MAP.get(instrument_key, instrument_key)
        )

        ok, reason = self.pipeline.run_pre_strategy_checks(
            instrument_key, "SELL_OPTION", vix, news_sentiment.get("headlines")
        )
        if not ok:
            logger.info(f"  {instrument_key}: ⏸ {reason}")
            return []

        cfg = self.config["instruments"][instrument_key]

        hist = self.data_fetcher.fetch_index_history(instrument_key)
        if hist.empty or len(hist) < 30:
            logger.info(f"  {instrument_key}: no historical data to analyze")
            return []

        df = self.technical.add_all_indicators(hist)
        trend = self.technical.get_trend_signal(df)
        breakout = self.technical.detect_breakout(df)
        latest = df.iloc[-1]

        setup = self.entry_rules._evaluate_sell(
            instrument_key, trend, breakout, latest, df,
            self.entry_rules.get_instrument_config(instrument_key),
            vix, news_sentiment,
        )
        if not setup:
            logger.info(
                f"  {instrument_key}: no qualifying sell setup "
                f"(trend={trend['direction']} adx={float(latest['adx']):.0f} — not range-bound enough)"
            )
            return []

        direction = setup["direction"]
        opt_type = setup["opt_type"]
        if not is_action_enabled(instrument_key, "SELL_OPTION", direction):
            logger.info(f"  {instrument_key}: {direction} sell action is OFF in toggles")
            return []

        opp = self._build_opportunity(
            instrument_key,
            trade_mode="SELL_OPTION",
            direction=direction,
            opt_type=opt_type,
            is_recommended=True,
            note="✅ Recommended — sell premium (profitable in backtest)",
        )
        return [opp] if opp else []

    def _scan_buy_shadow(self, instrument_key: str) -> Optional[TradeOpportunity]:
        """Show what a buy trade would look like — for suggestion only, not executed."""
        if is_any_buy_enabled(instrument_key):
            return None

        for direction, opt_type in [("BULLISH", "CE"), ("BEARISH", "PE")]:
            opp = self._build_opportunity(
                instrument_key,
                trade_mode="BUY_OPTION",
                direction=direction,
                opt_type=opt_type,
                is_recommended=False,
                note="⚠️ Buy disabled — not recommended. Use SELL instead.",
                force_evaluate=True,
            )
            if opp:
                return opp
        return None

    def scan_instrument(self, instrument_key: str) -> Optional[TradeOpportunity]:
        """Return best recommended (sell) opportunity for one instrument."""
        sells = self._scan_sell_opportunities(instrument_key)
        return sells[0] if sells else None

    def scan_status(self) -> list[dict]:
        """Fast per-instrument scanner status (no network) for the dashboard."""
        statuses = []
        for inst in self.config.get("instruments", {}):
            label = self.config["instruments"][inst].get("index_name", inst)
            if not is_instrument_enabled(inst):
                statuses.append({"instrument": label, "state": "OFF",
                                 "detail": "Disabled — enable in Settings"})
                continue
            mkt_ok, _ = self.pipeline.check_market_hours(inst)
            if not mkt_ok:
                statuses.append({"instrument": label, "state": "Market closed",
                                 "detail": "Outside this instrument's market hours"})
                continue
            ent_ok, reason = self.pipeline.check_entry_timing(inst)
            if not ent_ok:
                statuses.append({"instrument": label, "state": "Waiting", "detail": reason})
                continue
            statuses.append({"instrument": label, "state": "Scanning",
                             "detail": "Live — evaluating setups & liquidity"})
        return statuses

    @staticmethod
    def _days_to_expiry(chain_df) -> float:
        """Real calendar days to the chain's nearest expiry (min 1, default 7)."""
        from src.utils.clock import ist_today

        if chain_df is None or chain_df.empty or "expiry" not in chain_df.columns:
            return 7.0
        raw = chain_df["expiry"].iloc[0]
        if not raw:
            return 7.0
        for fmt in ("%d-%b-%Y", "%d-%b-%Y %H:%M:%S", "%Y-%m-%d", "%d-%m-%Y"):
            try:
                exp = datetime.strptime(str(raw), fmt).date()
                return max(1.0, float((exp - ist_today()).days))
            except (ValueError, TypeError):
                continue
        return 7.0

    def get_trade_ideas(
        self, include_buy: bool = False, min_edge: float = 45.0
    ) -> list[TradeOpportunity]:
        """Ranked, profit-positive suggestions for live trading.

        Only returns ideas with positive expected value and an acceptable
        edge score, sorted best-first — these are the 'sell this CE/PE' calls.
        """
        opps = self.scan_all(include_buy_suggestions=include_buy)
        ideas = [
            o for o in opps
            if o.verdict != "AVOID"
            and o.expected_value > 0
            and o.edge_score >= min_edge
        ]
        ideas.sort(key=lambda x: (x.verdict != "STRONG", x.verdict != "GOOD", -x.edge_score))
        return ideas

    def _build_opportunity(
        self,
        instrument_key: str,
        trade_mode: str,
        direction: str,
        opt_type: str,
        is_recommended: bool,
        note: str,
        force_evaluate: bool = False,
    ) -> Optional[TradeOpportunity]:
        cfg = self.config["instruments"][instrument_key]
        news_key = self.INSTRUMENT_NEWS_MAP.get(instrument_key, instrument_key)
        exchange = cfg.get("exchange", "NFO")

        vix = self.data_fetcher.fetch_india_vix()
        news_sentiment = self.news.get_instrument_sentiment(news_key)

        if is_recommended:
            ok, reason = self.pipeline.run_pre_strategy_checks(
                instrument_key, trade_mode, vix, news_sentiment.get("headlines")
            )
            if not ok:
                logger.debug(f"Pipeline blocked build: {reason}")
                return None

        hist = self.data_fetcher.fetch_index_history(instrument_key)
        if hist.empty or len(hist) < 30:
            return None

        df = self.technical.add_all_indicators(hist)
        trend = self.technical.get_trend_signal(df)
        breakout = self.technical.detect_breakout(df)
        latest = df.iloc[-1]

        if trade_mode == "SELL_OPTION":
            vix = self.data_fetcher.fetch_india_vix()
            news_sentiment = self.news.get_instrument_sentiment(
                self.INSTRUMENT_NEWS_MAP.get(instrument_key, instrument_key)
            )
            setup = self.entry_rules._evaluate_sell(
                instrument_key, trend, breakout, latest, df,
                self.entry_rules.get_instrument_config(instrument_key),
                vix, news_sentiment,
            )
            if not setup or setup.get("opt_type") != opt_type or setup.get("direction") != direction:
                return None
        else:
            if not force_evaluate:
                return None
            # Shadow buy — check if trend supports this direction
            if trend["direction"] != direction:
                return None
            if float(latest["adx"]) < 20:
                return None

        # Option chain via Kite (works on cloud VMs; NSE fallback for NIFTY only)
        chain_df, chain_analysis = self.chain_service.get_chain_for_scan(instrument_key)

        scores = self._score_strategies(
            trend, breakout, chain_analysis, news_sentiment, vix, df
        )

        confidence = self.entry_rules.compute_confidence(
            instrument_key, trend, breakout, latest,
            chain_analysis, news_sentiment, direction,
        )
        if trade_mode == "SELL_OPTION":
            confidence = min(0.95, confidence + 0.08)
            min_conf = self.min_sell_confidence
        else:
            min_conf = self.min_confidence

        if confidence < min_conf:
            return None

        underlying = chain_analysis.get("underlying", float(df["close"].iloc[-1]))
        if not underlying:
            underlying = float(df["close"].iloc[-1])
        otm = 2 if trade_mode == "SELL_OPTION" else 1
        dte_years = self._days_to_expiry(chain_df) / 365.0
        strike_info = self.options.select_strike(
            chain_df, direction, underlying,
            otm_distance=otm,
            trade_mode=trade_mode,
            days_to_expiry=dte_years,
        )
        if not strike_info:
            return None

        liq_ok, liq_reason = self.pipeline.check_strike_liquidity(strike_info, instrument_key)
        if is_recommended and not liq_ok:
            logger.info(f"Liquidity filter rejected {instrument_key}: {liq_reason}")
            return None

        entry = strike_info["premium"]
        lot_size = cfg["lot_size"]

        if trade_mode == "SELL_OPTION":
            target = round(entry * (1 - self.sell_profit_target), 2)
            sl = round(entry * (1 + self.sell_stop_loss), 2)
            dir_label = f"SELL_{strike_info['type']}"
            # Rough margin estimate for index options (~12% of spot × lot)
            est_margin = underlying * lot_size * 0.12
        else:
            target = round(entry * (1 + self.profit_target), 2)
            sl = round(entry * (1 - self.stop_loss), 2)
            dir_label = f"BUY_{strike_info['type']}"
            est_margin = entry * lot_size

        if is_recommended:
            approved, cost_info = self.costs.is_trade_worth_it(
                entry, target, lot_size, exchange, trade_mode
            )
            if not approved:
                return None
            est_costs = cost_info["total_costs"]
            expected_net = cost_info["net_pnl"]
        else:
            est_costs = self.costs.estimate_round_trip_cost(entry, lot_size, exchange, trade_mode, 20)
            expected_net = 0
            cost_info = {"total_costs": est_costs, "net_pnl": 0}

        reasoning_parts = [
            note,
            f"Mode: {trade_mode}",
            f"Session: {self.pipeline.time.get_session_phase(instrument_key)}",
            f"Trend: {trend['direction']} (strength {trend['strength']:.2f})",
        ]
        if strike_info.get("delta"):
            reasoning_parts.append(f"Delta: {strike_info['delta']:.2f}")
        if is_recommended and not liq_ok:
            reasoning_parts.append(f"Liquidity: {liq_reason}")
        if chain_analysis.get("valid"):
            reasoning_parts.append(f"PCR: {chain_analysis['pcr']} ({chain_analysis['oi_signal']})")
        reasoning_parts.append(f"News: {news_sentiment['score']:.2f}")
        if is_recommended:
            reasoning_parts.append(
                f"Costs: ₹{cost_info['total_costs']:.0f} | Net if target: ₹{cost_info['net_pnl']:.0f}"
            )
            reasoning_parts.append(f"Est. margin required: ₹{est_margin:,.0f}")

        opp = TradeOpportunity(
            instrument=instrument_key,
            direction=dir_label,
            trade_mode=trade_mode,
            confidence=confidence,
            entry_price=entry,
            target_price=target,
            stop_loss=sl,
            strike=strike_info["strike"],
            expiry=strike_info.get("expiry", ""),
            lot_size=lot_size,
            estimated_costs=est_costs,
            expected_net_pnl=expected_net,
            strategy_scores=scores,
            reasoning=" | ".join(reasoning_parts),
            is_recommended=is_recommended,
            recommendation_note=note,
            estimated_margin=est_margin,
            delta=float(strike_info.get("delta", 0.0)),
        )

        self._enrich_metrics(opp, exchange, chain_analysis, news_sentiment, vix, liq_ok)
        return opp

    def _enrich_metrics(
        self,
        opp: TradeOpportunity,
        exchange: str,
        chain_analysis: dict,
        news_sentiment: dict,
        vix: float,
        liq_ok: bool,
    ) -> None:
        """Compute probability of profit, expected value, and an edge score (the 'magic')."""
        qty = opp.lot_size

        # Net P&L if target hit and if stop-loss hit (after all costs)
        net_win, _ = self.costs.net_pnl(
            opp.entry_price, opp.target_price, qty, exchange, opp.trade_mode
        )
        net_loss, _ = self.costs.net_pnl(
            opp.entry_price, opp.stop_loss, qty, exchange, opp.trade_mode
        )
        opp.expected_net_pnl = round(net_win, 2)
        opp.max_loss = round(net_loss, 2)

        # Probability of profit.
        # For a sold OTM option, P(expire OTM) ~ 1 - |delta|. We also collect
        # partial profit before expiry, so blend with the option-selling target.
        delta = abs(opp.delta) if opp.delta else 0.0
        if opp.trade_mode == "SELL_OPTION":
            base_pop = (1 - delta) if delta else 0.70
            # Selling exits at 50% of premium, well before expiry → higher realised hit-rate
            pop = min(0.92, base_pop + 0.08)
        else:
            base_pop = delta if delta else 0.40
            # Buyers must overcome premium decay → haircut
            pop = max(0.20, base_pop - 0.10)

        # News alignment nudges POP
        news_score = news_sentiment.get("score", 0)
        if opp.trade_mode == "SELL_OPTION":
            if opp.direction.endswith("CE") and news_score < -0.1:
                pop = min(0.95, pop + 0.04)
            elif opp.direction.endswith("PE") and news_score > 0.1:
                pop = min(0.95, pop + 0.04)
        opp.pop = round(pop, 3)

        # Expected value (₹): EV = POP*win + (1-POP)*loss  (loss is negative)
        opp.expected_value = round(pop * net_win + (1 - pop) * net_loss, 2)

        # IV percentile (theta edge for sellers)
        try:
            opp.iv_percentile = self.pipeline.iv.get_vix_percentile()
        except Exception:
            opp.iv_percentile = 50.0

        opp.edge_score = self._compute_edge_score(opp, chain_analysis, news_score, liq_ok)
        opp.verdict = self._verdict(opp)
        opp.headline = self._headline(opp)

    def _compute_edge_score(
        self, opp: TradeOpportunity, chain_analysis: dict, news_score: float, liq_ok: bool
    ) -> float:
        """Blend all signals into a single 0-100 conviction score."""
        score = 0.0

        # Confidence (max 25)
        score += min(25, opp.confidence * 25)

        # Probability of profit (max 25)
        score += min(25, opp.pop * 25)

        # Risk/reward & positive EV (max 20)
        if opp.max_loss < 0:
            rr = opp.expected_net_pnl / abs(opp.max_loss)
            score += min(12, rr * 6)
        if opp.expected_value > 0:
            score += 8

        # IV percentile favouring sellers (max 12)
        if opp.trade_mode == "SELL_OPTION":
            score += min(12, (opp.iv_percentile / 100) * 12)
        else:
            score += min(12, ((100 - opp.iv_percentile) / 100) * 12)

        # OI / PCR alignment (max 8)
        if chain_analysis.get("valid"):
            oi_signal = chain_analysis.get("oi_signal", "NEUTRAL")
            bullish_trade = opp.direction.endswith("PE")  # selling PE / buying CE = bullish
            if (oi_signal == "BULLISH" and bullish_trade) or (
                oi_signal == "BEARISH" and not bullish_trade
            ):
                score += 8
            elif oi_signal == "NEUTRAL":
                score += 4

        # News alignment (max 5)
        aligned = (
            (opp.direction.endswith("CE") and news_score < 0)
            or (opp.direction.endswith("PE") and news_score > 0)
        )
        if aligned:
            score += 5

        # Liquidity penalty
        if not liq_ok:
            score -= 15

        return round(max(0, min(100, score)), 1)

    @staticmethod
    def _verdict(opp: TradeOpportunity) -> str:
        if opp.expected_value <= 0:
            return "AVOID"
        if opp.edge_score >= 75 and opp.pop >= 0.75:
            return "STRONG"
        if opp.edge_score >= 60:
            return "GOOD"
        if opp.edge_score >= 45:
            return "FAIR"
        return "AVOID"

    @staticmethod
    def _headline(opp: TradeOpportunity) -> str:
        action = "SELL" if opp.trade_mode == "SELL_OPTION" else "BUY"
        opt = opp.direction.split("_")[-1]
        name = opp.instrument.upper().replace("NIFTY50", "NIFTY").replace("CRUDE_OIL", "CRUDE")
        if opp.trade_mode == "SELL_OPTION":
            return (
                f"{action} {name} {int(opp.strike)} {opt} @ ₹{opp.entry_price:.1f} → "
                f"collect premium, ~{opp.pop:.0%} chance of profit, "
                f"net ₹{opp.expected_net_pnl:,.0f} if target"
            )
        return (
            f"{action} {name} {int(opp.strike)} {opt} @ ₹{opp.entry_price:.1f} → "
            f"target ₹{opp.target_price:.1f}, ~{opp.pop:.0%} POP, "
            f"net ₹{opp.expected_net_pnl:,.0f} if target"
        )

    def _score_strategies(self, trend, breakout, chain, news, vix, df) -> dict:
        scores = {}
        strat_cfg = self.config["strategies"]

        if strat_cfg["momentum_breakout"]["enabled"]:
            score = 0.0
            if trend["direction"] == "BULLISH":
                score += trend["strength"] * 0.4
            elif trend["direction"] == "BEARISH":
                score -= trend["strength"] * 0.4

            if breakout.get("breakout"):
                if breakout["direction"] == "UP":
                    score += 0.3
                else:
                    score -= 0.3

            if trend.get("volume_spike"):
                score += 0.15 if score > 0 else -0.15

            rsi_cfg = strat_cfg["momentum_breakout"]
            rsi = trend.get("rsi", 50)
            if rsi < rsi_cfg["rsi_oversold"]:
                score += 0.15
            elif rsi > rsi_cfg["rsi_overbought"]:
                score -= 0.15

            scores["momentum"] = {
                "score": max(-1, min(1, score)),
                "direction": "BULLISH" if score > 0 else "BEARISH" if score < 0 else "NEUTRAL",
            }

        if strat_cfg["oi_analysis"]["enabled"] and chain.get("valid"):
            pcr = chain["pcr"]
            score = 0.0
            if pcr > 1.2:
                score = 0.6
            elif pcr < 0.8:
                score = -0.6
            elif pcr > 1.0:
                score = 0.3
            else:
                score = -0.3

            if chain["put_oi_change"] > chain["call_oi_change"]:
                score += 0.2
            else:
                score -= 0.2

            scores["oi_analysis"] = {
                "score": max(-1, min(1, score)),
                "direction": "BULLISH" if score > 0 else "BEARISH" if score < 0 else "NEUTRAL",
                "pcr": pcr,
            }

        if strat_cfg["news_sentiment"]["enabled"]:
            sent = news["score"]
            min_sent = strat_cfg["news_sentiment"]["min_sentiment_score"]
            if abs(sent) >= min_sent:
                scores["news"] = {
                    "score": sent,
                    "direction": "BULLISH" if sent > 0 else "BEARISH",
                    "articles": news["article_count"],
                }
            else:
                scores["news"] = {"score": 0, "direction": "NEUTRAL", "articles": 0}

        if strat_cfg["iv_rank"]["enabled"]:
            iv_score = 0.0
            if vix > 20:
                iv_score = -0.3
            elif vix < 13:
                iv_score = 0.3
            scores["iv_rank"] = {
                "score": iv_score,
                "direction": "BULLISH" if iv_score > 0 else "BEARISH" if iv_score < 0 else "NEUTRAL",
                "vix": vix,
            }

        return scores
