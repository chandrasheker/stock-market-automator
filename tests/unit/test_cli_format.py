from __future__ import annotations

from datetime import date

from crude_research.cli import format_chain_table
from crude_research.config import Settings
from crude_research.diagnostics.doctor import run_doctor
from crude_research.market.chain import build_option_chain
from crude_research.market.models import ExpiryTimeSource
from tests.fixtures.builders import crude_master
from tests.unit.test_chain import _mini_quotes, _now


def test_doctor_offline() -> None:
    report = run_doctor(Settings(_env_file=None), try_network=False)
    names = {c.name for c in report.checks}
    assert "python_version" in names
    assert "kite_credentials" in names
    joined = " ".join(c.detail for c in report.checks)
    assert "KITE_API_KEY" in joined
    assert "<empty>" in joined


def test_mask_never_prints_full_secret() -> None:
    from crude_research.diagnostics.kite_auth import inspect_secret, mask_secret

    secret = "abcdefghijklmnop"
    assert secret not in mask_secret(secret)
    view = inspect_secret("KITE_ACCESS_TOKEN", '  "abcdefghijklmnop"  ')
    assert "WRAPPED_IN_QUOTES" in view.issues
    assert "LEADING_OR_TRAILING_WHITESPACE" in view.issues
    assert secret not in view.fingerprint


def test_table_mentions_not_a_recommendation() -> None:
    snapshot = build_option_chain(
        "CRUDEOILM",
        date(2026, 10, 16),
        _mini_quotes(),
        crude_master(),
        now=_now(),
        stale_after_seconds=120,
        rate=0.06,
        time_years=0.14,
        expiry_timestamp=None,
        expiry_time_source=ExpiryTimeSource.CONFIGURED_ASSUMPTION,
        vol_lower=1e-6,
        vol_upper=5.0,
        compute_greeks=False,
    )
    table = format_chain_table(snapshot)
    assert "not a trading recommendation" in table.lower()
    assert "8000" in table
    assert "CRUDEOILM26OCTFUT" in table
