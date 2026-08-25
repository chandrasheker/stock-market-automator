"""MCX session close is independent of OPTION_EXPIRY_TIME / Black-76 T."""

from __future__ import annotations

from datetime import date, datetime, time
from zoneinfo import ZoneInfo

import pytest

from crude_research.config import Settings
from crude_research.exceptions import ConfigurationError
from crude_research.market.candles import IST, expected_60m_starts
from crude_research.market.models import ExpiryTimeSource
from crude_research.market.session import resolve_mcx_session_close
from crude_research.quant.time import assume_expiry_timestamp


def test_summer_session_close_is_not_option_expiry_time() -> None:
    settings = Settings(_env_file=None)
    expiry = settings.parsed_expiry_time()
    session = settings.resolve_session_close(date(2026, 8, 25))
    assert expiry == time(23, 30, 0)
    assert session.clock == time(23, 55)
    assert session.rule == "NY_DST_2355"
    ts, source = assume_expiry_timestamp(
        date(2026, 8, 25), tz=ZoneInfo("Asia/Kolkata"), expiry_time=expiry
    )
    assert source == ExpiryTimeSource.CONFIGURED_ASSUMPTION
    assert ts.hour == 23 and ts.minute == 30
    assert ts.minute != session.clock.minute


def test_winter_session_close_uses_standard_evening() -> None:
    session = resolve_mcx_session_close(date(2026, 1, 15))
    assert session.clock == time(23, 30)
    assert session.rule == "NY_STANDARD_2330"


def test_configured_session_close_override_wins() -> None:
    settings = Settings(mcx_session_close="22:00:00", option_expiry_time="23:30:00", _env_file=None)
    session = settings.resolve_session_close(date(2026, 8, 25))
    assert settings.parsed_expiry_time() == time(23, 30, 0)
    assert session.clock == time(22, 0, 0)
    assert session.rule == "CONFIGURED_OVERRIDE"


def test_invalid_session_close_override_fails_closed() -> None:
    settings = Settings(mcx_session_close="23:30", _env_file=None)
    with pytest.raises(ConfigurationError):
        settings.parsed_session_close_override()


def test_final_4h_expected_hours_follow_session_close() -> None:
    start = datetime(2026, 8, 25, 21, 0, tzinfo=IST)
    assert [ts.hour for ts in expected_60m_starts(start, time(23, 55))] == [21, 22, 23]
    assert [ts.hour for ts in expected_60m_starts(start, time(23, 30))] == [21, 22, 23]
    assert [ts.hour for ts in expected_60m_starts(start, time(23, 0))] == [21, 22]
