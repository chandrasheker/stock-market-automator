from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import pytest


@pytest.fixture
def ist() -> ZoneInfo:
    return ZoneInfo("Asia/Kolkata")


@pytest.fixture
def now_ist(ist: ZoneInfo) -> datetime:
    return datetime(2026, 8, 25, 15, 30, tzinfo=ist)


@pytest.fixture
def now_utc() -> datetime:
    return datetime(2026, 8, 25, 10, 0, tzinfo=UTC)
