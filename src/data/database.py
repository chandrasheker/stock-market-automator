"""SQLite database models for personal trading data."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Float,
    Integer,
    String,
    Text,
    create_engine,
)
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from src.config import get_db_path


class Base(DeclarativeBase):
    pass


class HistoricalCandle(Base):
    __tablename__ = "historical_candles"

    id = Column(Integer, primary_key=True, autoincrement=True)
    symbol = Column(String(50), index=True, nullable=False)
    exchange = Column(String(10), nullable=False)
    timestamp = Column(DateTime, index=True, nullable=False)
    open = Column(Float)
    high = Column(Float)
    low = Column(Float)
    close = Column(Float)
    volume = Column(Float, default=0)
    oi = Column(Float, default=0)


class NewsArticle(Base):
    __tablename__ = "news_articles"

    id = Column(Integer, primary_key=True, autoincrement=True)
    title = Column(String(500), nullable=False)
    summary = Column(Text)
    source = Column(String(100))
    url = Column(String(500), unique=True)
    published_at = Column(DateTime, index=True)
    sentiment_score = Column(Float, default=0.0)
    relevant_instruments = Column(String(200))
    fetched_at = Column(DateTime, default=datetime.utcnow)


class TradeSignal(Base):
    __tablename__ = "trade_signals"

    id = Column(Integer, primary_key=True, autoincrement=True)
    timestamp = Column(DateTime, default=datetime.utcnow, index=True)
    instrument = Column(String(50), nullable=False)
    strategy = Column(String(50), nullable=False)
    direction = Column(String(10))  # BUY_CE, BUY_PE
    strike = Column(Float)
    expiry = Column(String(20))
    confidence = Column(Float)
    entry_price = Column(Float)
    target_price = Column(Float)
    stop_loss = Column(Float)
    reasoning = Column(Text)
    executed = Column(Boolean, default=False)


class Trade(Base):
    __tablename__ = "trades"

    id = Column(Integer, primary_key=True, autoincrement=True)
    signal_id = Column(Integer)
    order_id = Column(String(50))
    instrument = Column(String(50), nullable=False)
    tradingsymbol = Column(String(100))
    exchange = Column(String(10))
    direction = Column(String(10))
    quantity = Column(Integer)
    entry_price = Column(Float)
    exit_price = Column(Float)
    target_price = Column(Float)
    stop_loss = Column(Float)
    pnl = Column(Float, default=0.0)
    pnl_pct = Column(Float, default=0.0)
    status = Column(String(20), default="OPEN")  # OPEN, CLOSED, CANCELLED
    is_paper = Column(Boolean, default=True)
    entry_time = Column(DateTime, default=datetime.utcnow)
    exit_time = Column(DateTime)
    exit_reason = Column(String(50))


class DailyPnL(Base):
    __tablename__ = "daily_pnl"

    id = Column(Integer, primary_key=True, autoincrement=True)
    date = Column(DateTime, unique=True, index=True)
    realized_pnl = Column(Float, default=0.0)
    unrealized_pnl = Column(Float, default=0.0)
    trades_count = Column(Integer, default=0)
    win_count = Column(Integer, default=0)


class DailyBacktestReport(Base):
    __tablename__ = "daily_backtest_reports"

    id = Column(Integer, primary_key=True, autoincrement=True)
    date = Column(DateTime, unique=True, index=True)
    combined_trades = Column(Integer, default=0)
    combined_net_win_rate = Column(Float, default=0.0)
    combined_gross_pnl = Column(Float, default=0.0)
    combined_costs = Column(Float, default=0.0)
    combined_net_pnl = Column(Float, default=0.0)
    combined_sharpe = Column(Float, default=0.0)
    strategy_approved = Column(Boolean, default=False)
    news_json = Column(Text)
    instruments_json = Column(Text)
    today_bias_json = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)


_engine = None
_SessionLocal: Optional[sessionmaker] = None


def get_engine():
    global _engine
    if _engine is None:
        _engine = create_engine(f"sqlite:///{get_db_path()}", echo=False)
    return _engine


def get_session() -> Session:
    global _SessionLocal
    if _SessionLocal is None:
        _SessionLocal = sessionmaker(bind=get_engine())
    return _SessionLocal()


def init_db():
    engine = get_engine()
    Base.metadata.create_all(engine)
