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
from src.data.historical import HistoricalDataFetcher
from src.data.news_fetcher import NewsFetcher


@dataclass
class TradeOpportunity:
    instrument: str
    direction: str  # BUY_CE or BUY_PE
    confidence: float
    entry_price: float
    target_price: float
    stop_loss: float
    strike: float
    expiry: str
    lot_size: int
    strategy_scores: dict = field(default_factory=dict)
    reasoning: str = ""
    timestamp: datetime = field(default_factory=datetime.now)

    @property
    def expected_profit_pct(self) -> float:
        if self.entry_price <= 0:
            return 0
        return ((self.target_price - self.entry_price) / self.entry_price) * 100

    @property
    def risk_reward_ratio(self) -> float:
        risk = self.entry_price - self.stop_loss
        reward = self.target_price - self.entry_price
        return reward / risk if risk > 0 else 0


class SignalEngine:
    """Combines technical, OI, news, and IV analysis into trade signals."""

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
        self.data_fetcher = HistoricalDataFetcher()
        self.news = NewsFetcher()
        self.profit_target = self.env.profit_target_pct / 100
        self.stop_loss = self.env.stop_loss_pct / 100
        self.min_confidence = self.config.get("backtest", {}).get("defaults", {}).get(
            "min_confidence", 0.65
        )

    def scan_all(self) -> list[TradeOpportunity]:
        opportunities = []
        for instrument_key, cfg in self.config["instruments"].items():
            if not cfg.get("enabled", True):
                continue
            try:
                opp = self.scan_instrument(instrument_key)
                if opp and opp.confidence >= self.min_confidence:
                    opportunities.append(opp)
            except Exception as e:
                logger.error(f"Scan failed for {instrument_key}: {e}")
        return sorted(opportunities, key=lambda x: x.confidence, reverse=True)

    def scan_instrument(self, instrument_key: str) -> Optional[TradeOpportunity]:
        cfg = self.config["instruments"][instrument_key]
        news_key = self.INSTRUMENT_NEWS_MAP.get(instrument_key, instrument_key)

        hist = self.data_fetcher.fetch_index_history(instrument_key)
        if hist.empty or len(hist) < 30:
            logger.warning(f"Insufficient history for {instrument_key}")
            return None

        df = self.technical.add_all_indicators(hist)
        trend = self.technical.get_trend_signal(df)
        breakout = self.technical.detect_breakout(df)

        chain_data = self.data_fetcher.fetch_option_chain_snapshot(cfg["underlying"])
        chain_analysis = self.options.analyze_chain(chain_data) if chain_data else {"valid": False}

        news_sentiment = self.news.get_instrument_sentiment(news_key)
        vix = self.data_fetcher.fetch_india_vix()

        scores = self._score_strategies(
            trend, breakout, chain_analysis, news_sentiment, vix, df
        )

        entry_direction = self.entry_rules.evaluate(
            instrument_key, trend, breakout, df.iloc[-1], df
        )
        if not entry_direction:
            return None

        confidence = self.entry_rules.compute_confidence(
            instrument_key, trend, breakout, df.iloc[-1],
            chain_analysis, news_sentiment, entry_direction,
        )
        if confidence < self.min_confidence:
            return None

        opt_direction = entry_direction
        underlying = chain_analysis.get("underlying", float(df["close"].iloc[-1]))

        chain_df = self.options.parse_option_chain(chain_data) if chain_data else pd.DataFrame()
        strike_info = self.options.select_strike(chain_df, opt_direction, underlying)

        if not strike_info:
            return None

        entry = strike_info["premium"]
        target = round(entry * (1 + self.profit_target), 2)
        sl = round(entry * (1 - self.stop_loss), 2)

        reasoning_parts = [
            f"Trend: {trend['direction']} (strength {trend['strength']:.2f})",
            f"Breakout: {breakout.get('direction', 'None')}",
        ]
        if chain_analysis.get("valid"):
            reasoning_parts.append(f"PCR: {chain_analysis['pcr']} ({chain_analysis['oi_signal']})")
        reasoning_parts.append(
            f"News sentiment: {news_sentiment['score']:.2f} ({news_sentiment['article_count']} articles)"
        )
        reasoning_parts.append(f"India VIX: {vix:.1f}")

        return TradeOpportunity(
            instrument=instrument_key,
            direction=f"BUY_{strike_info['type']}",
            confidence=confidence,
            entry_price=entry,
            target_price=target,
            stop_loss=sl,
            strike=strike_info["strike"],
            expiry=strike_info.get("expiry", ""),
            lot_size=cfg["lot_size"],
            strategy_scores=scores,
            reasoning=" | ".join(reasoning_parts),
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

    def _combine_scores(self, scores: dict) -> dict:
        strat_cfg = self.config["strategies"]
        total_weight = 0
        weighted_score = 0

        weight_map = {
            "momentum": strat_cfg["momentum_breakout"]["weight"],
            "oi_analysis": strat_cfg["oi_analysis"]["weight"],
            "news": strat_cfg["news_sentiment"]["weight"],
            "iv_rank": strat_cfg["iv_rank"]["weight"],
        }

        for key, weight in weight_map.items():
            if key in scores:
                total_weight += weight
                weighted_score += scores[key]["score"] * weight

        if total_weight == 0:
            return {"confidence": 0, "direction": "NEUTRAL"}

        normalized = weighted_score / total_weight
        confidence = min(0.95, 0.5 + abs(normalized) * 0.45)

        direction_votes = {"BULLISH": 0, "BEARISH": 0, "NEUTRAL": 0}
        for key in scores:
            d = scores[key].get("direction", "NEUTRAL")
            w = weight_map.get(key, 0.1)
            direction_votes[d] = direction_votes.get(d, 0) + w

        final_direction = max(direction_votes, key=direction_votes.get)

        agreeing = sum(
            1 for s in scores.values()
            if s.get("direction") == final_direction
        )
        if agreeing >= 2:
            confidence = min(0.95, confidence + 0.1)

        return {
            "confidence": round(confidence, 3),
            "direction": final_direction,
            "normalized_score": round(normalized, 3),
        }
