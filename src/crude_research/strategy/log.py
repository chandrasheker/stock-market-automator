"""Append-only M4-cycle decision log. Records QUALIFIED and NO TRADE."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd


def decision_log_path(data_dir: Path) -> Path:
    return Path(data_dir) / "decisions" / "decision_log.parquet"


def append_decision(data_dir: Path, record: dict[str, Any]) -> Path:
    path = decision_log_path(data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    row = dict(record)
    row.setdefault("logged_at_utc", datetime.now(tz=UTC).isoformat())
    frame = pd.DataFrame([row])
    if path.exists():
        prior = pd.read_parquet(str(path))
        frame = pd.concat([prior, frame], ignore_index=True)
    frame.to_parquet(path, engine="pyarrow", index=False)
    json_path = path.with_suffix(".jsonl")
    with json_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, default=str) + "\n")
    return path
