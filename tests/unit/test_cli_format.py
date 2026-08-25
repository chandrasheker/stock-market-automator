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
    from crude_research import __version__

    report = run_doctor(Settings(_env_file=None), try_network=False)
    names = {c.name for c in report.checks}
    assert "python_version" in names
    assert "kite_credentials" in names
    assert "package_version" in names
    joined = " ".join(c.detail for c in report.checks)
    assert "KITE_API_KEY" in joined
    assert "<empty>" in joined
    assert __version__ in joined


def test_cli_help_lists_kite_and_version() -> None:
    from typer.testing import CliRunner

    from crude_research import __version__
    from crude_research.cli import app

    help_result = CliRunner().invoke(app, ["--help"])
    assert help_result.exit_code == 0
    assert "kite" in help_result.stdout
    assert "serve" in help_result.stdout
    version_result = CliRunner().invoke(app, ["--version"])
    assert version_result.exit_code == 0
    assert __version__ in version_result.stdout


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


def test_doctor_notes_unrelated_pypi_kite(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    import importlib.metadata as metadata

    class _Dist:
        version = "0.2.3"

    def _distribution(name: str) -> object:
        if name == "kite":
            return _Dist()
        raise metadata.PackageNotFoundError(name)

    monkeypatch.setattr(metadata, "distribution", _distribution)
    report = run_doctor(Settings(_env_file=None), try_network=False)
    joined = " ".join(c.detail for c in report.checks)
    assert "unrelated PyPI package 'kite' 0.2.3" in joined
    assert "pip uninstall kite" in joined
