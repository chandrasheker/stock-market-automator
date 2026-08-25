"""Tiny SQLite store for paper trades, SMA state, and decision history."""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def db_path(data_dir: Path) -> Path:
    return Path(data_dir) / "sma.sqlite"


def connect(data_dir: Path) -> sqlite3.Connection:
    path = db_path(data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute(
        """CREATE TABLE IF NOT EXISTS kv (
            k TEXT PRIMARY KEY,
            v TEXT NOT NULL
        )"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS trades (
            id TEXT PRIMARY KEY,
            state TEXT NOT NULL,
            payload TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS decisions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            logged_at TEXT NOT NULL,
            payload TEXT NOT NULL
        )"""
    )
    conn.commit()
    return conn


class SmaStore:
    def __init__(self, data_dir: Path) -> None:
        self._conn = connect(data_dir)

    def get(self, key: str, default: str | None = None) -> str | None:
        row = self._conn.execute("SELECT v FROM kv WHERE k=?", (key,)).fetchone()
        return str(row["v"]) if row else default

    def set(self, key: str, value: str) -> None:
        self._conn.execute("INSERT OR REPLACE INTO kv(k,v) VALUES(?,?)", (key, value))
        self._conn.commit()

    def save_trade(self, trade_id: str, state: str, payload: dict[str, Any]) -> None:
        now = datetime.now(tz=UTC).isoformat()
        self._conn.execute(
            "INSERT OR REPLACE INTO trades(id,state,payload,updated_at) VALUES(?,?,?,?)",
            (trade_id, state, json.dumps(payload, default=str), now),
        )
        self._conn.commit()

    def load_trade(self, trade_id: str) -> dict[str, Any] | None:
        row = self._conn.execute("SELECT payload FROM trades WHERE id=?", (trade_id,)).fetchone()
        return json.loads(row["payload"]) if row else None

    def open_trades(self) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT payload FROM trades WHERE state NOT IN ('CLOSED','ABORTED')"
        ).fetchall()
        return [json.loads(r["payload"]) for r in rows]

    def log_decision(self, payload: dict[str, Any]) -> None:
        self._conn.execute(
            "INSERT INTO decisions(logged_at, payload) VALUES(?,?)",
            (datetime.now(tz=UTC).isoformat(), json.dumps(payload, default=str)),
        )
        self._conn.commit()
