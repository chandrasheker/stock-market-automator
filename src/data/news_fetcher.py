"""News fetcher and sentiment analyzer for market-moving events."""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Optional
from xml.etree import ElementTree

import feedparser
import requests
from loguru import logger
from textblob import TextBlob
from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer

from src.config import get_env, get_yaml_config
from src.data.database import NewsArticle, get_session, init_db

vader = SentimentIntensityAnalyzer()


class NewsFetcher:
    """Aggregates financial news and scores sentiment per instrument."""

    def __init__(self):
        self.config = get_yaml_config()
        self.env = get_env()
        init_db()

    def fetch_all_news(self, hours: int = 24) -> list[dict]:
        articles = []
        for source in self.config["news_sources"]["rss"]:
            try:
                feed_articles = self._fetch_rss(source["name"], source["url"])
                articles.extend(feed_articles)
            except Exception as e:
                logger.warning(f"RSS fetch failed for {source['name']}: {e}")

        if self.env.news_api_key:
            articles.extend(self._fetch_newsapi(hours))

        articles = self._deduplicate(articles)
        articles = [a for a in articles if self._is_recent(a, hours)]

        for article in articles:
            article["sentiment"] = self._analyze_sentiment(
                f"{article['title']} {article.get('summary', '')}"
            )
            article["instruments"] = self._match_instruments(article)
            self._save_article(article)

        return articles

    def _fetch_rss(self, name: str, url: str) -> list[dict]:
        feed = feedparser.parse(url)
        articles = []
        for entry in feed.entries[:30]:
            published = None
            if hasattr(entry, "published_parsed") and entry.published_parsed:
                published = datetime(*entry.published_parsed[:6])
            elif hasattr(entry, "updated_parsed") and entry.updated_parsed:
                published = datetime(*entry.updated_parsed[:6])

            articles.append({
                "title": entry.get("title", ""),
                "summary": self._clean_html(entry.get("summary", entry.get("description", ""))),
                "source": name,
                "url": entry.get("link", ""),
                "published_at": published or datetime.utcnow(),
            })
        return articles

    def _fetch_newsapi(self, hours: int) -> list[dict]:
        keywords = " OR ".join([
            "nifty", "sensex", "crude oil", "india stock market",
            "rbi", "fii", "dii", "nse", "bse",
        ])
        url = "https://newsapi.org/v2/everything"
        params = {
            "q": keywords,
            "language": "en",
            "sortBy": "publishedAt",
            "from": (datetime.utcnow() - timedelta(hours=hours)).isoformat(),
            "apiKey": self.env.news_api_key,
            "pageSize": 50,
        }

        try:
            resp = requests.get(url, params=params, timeout=10)
            if resp.status_code == 200:
                return [
                    {
                        "title": a["title"],
                        "summary": a.get("description", ""),
                        "source": a["source"]["name"],
                        "url": a["url"],
                        "published_at": datetime.fromisoformat(
                            a["publishedAt"].replace("Z", "+00:00")
                        ).replace(tzinfo=None),
                    }
                    for a in resp.json().get("articles", [])
                ]
        except Exception as e:
            logger.warning(f"NewsAPI fetch failed: {e}")
        return []

    def _analyze_sentiment(self, text: str) -> float:
        if not text.strip():
            return 0.0
        vader_score = vader.polarity_scores(text)["compound"]
        blob_score = TextBlob(text).sentiment.polarity
        return round((vader_score + blob_score) / 2, 4)

    def _match_instruments(self, article: dict) -> list[str]:
        text = f"{article['title']} {article.get('summary', '')}".lower()
        matched = []
        keywords = self.config["news_sources"]["keywords"]
        for instrument, kws in keywords.items():
            if any(kw in text for kw in kws):
                matched.append(instrument)
        return matched

    def get_instrument_sentiment(self, instrument: str, hours: int = 24) -> dict:
        """Aggregate sentiment for a specific instrument."""
        news = self.fetch_all_news(hours)
        relevant = [a for a in news if instrument in a.get("instruments", [])]

        if not relevant:
            return {
                "instrument": instrument,
                "score": 0.0,
                "article_count": 0,
                "bullish_count": 0,
                "bearish_count": 0,
                "headlines": [],
            }

        scores = [a["sentiment"] for a in relevant]
        avg_score = sum(scores) / len(scores)

        return {
            "instrument": instrument,
            "score": round(avg_score, 4),
            "article_count": len(relevant),
            "bullish_count": sum(1 for s in scores if s > 0.1),
            "bearish_count": sum(1 for s in scores if s < -0.1),
            "headlines": [a["title"] for a in relevant[:5]],
        }

    def _save_article(self, article: dict):
        db = get_session()
        try:
            existing = db.query(NewsArticle).filter_by(url=article["url"]).first()
            if existing:
                return
            record = NewsArticle(
                title=article["title"][:500],
                summary=article.get("summary", "")[:2000],
                source=article["source"],
                url=article["url"],
                published_at=article.get("published_at"),
                sentiment_score=article.get("sentiment", 0),
                relevant_instruments=",".join(article.get("instruments", [])),
            )
            db.add(record)
            db.commit()
        except Exception as e:
            db.rollback()
            logger.debug(f"Article save skipped: {e}")
        finally:
            db.close()

    @staticmethod
    def _clean_html(text: str) -> str:
        return re.sub(r"<[^>]+>", "", text).strip()

    @staticmethod
    def _deduplicate(articles: list[dict]) -> list[dict]:
        seen = set()
        unique = []
        for a in articles:
            key = a["title"].lower().strip()
            if key not in seen:
                seen.add(key)
                unique.append(a)
        return unique

    @staticmethod
    def _is_recent(article: dict, hours: int) -> bool:
        pub = article.get("published_at")
        if not pub:
            return True
        return datetime.utcnow() - pub < timedelta(hours=hours)
