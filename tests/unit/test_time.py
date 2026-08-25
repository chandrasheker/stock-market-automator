from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from crude_research.exceptions import ConfigurationError
from crude_research.market.models import ExpiryTimeSource
from crude_research.quant.time import assume_expiry_timestamp, time_to_expiry


def test_time_to_expiry_rejects_naive() -> None:
    tz = ZoneInfo("Asia/Kolkata")
    aware = datetime(2026, 10, 16, 23, 30, tzinfo=tz)
    naive = datetime(2026, 8, 25, 15, 0)
    with pytest.raises(ConfigurationError):
        time_to_expiry(naive, aware)
    with pytest.raises(ConfigurationError):
        time_to_expiry(aware, naive)


def test_exact_year_fraction_not_integer_dte() -> None:
    tz = ZoneInfo("Asia/Kolkata")
    now = datetime(2026, 10, 15, 23, 30, tzinfo=tz)
    expiry = datetime(2026, 10, 16, 23, 30, tzinfo=tz)
    t = time_to_expiry(now, expiry, seconds_per_year=365.25 * 86400)
    assert t == pytest.approx(1.0 / 365.25)
    # Integer DTE/365 would be 1/365, which we explicitly do not use.
    assert t != pytest.approx(1.0 / 365.0)


def test_assume_expiry_timestamp_is_explicit() -> None:
    tz = ZoneInfo("Asia/Kolkata")
    from datetime import date, time

    ts, source = assume_expiry_timestamp(date(2026, 10, 16), tz=tz, expiry_time=time(23, 30))
    assert source == ExpiryTimeSource.CONFIGURED_ASSUMPTION
    assert ts.hour == 23 and ts.minute == 30
    assert ts.tzinfo is not None
