"""Live Kite tests — skipped unless credentials exist. Never places orders."""

from __future__ import annotations

import os

import pytest

pytestmark = pytest.mark.network


def test_credentials_optional() -> None:
    if not (os.environ.get("KITE_API_KEY") and os.environ.get("KITE_ACCESS_TOKEN")):
        pytest.skip("Kite credentials not configured")
    from crude_research.config import Settings
    from crude_research.zerodha.client import KiteMarketDataClient

    client = KiteMarketDataClient(Settings())
    profile = client.profile()
    assert "user_id" in profile or "user_name" in profile
