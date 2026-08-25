from __future__ import annotations

from crude_research.diagnostics.kite_auth import format_kite_exception, mask_secret


def test_format_kite_exception_includes_message() -> None:
    class TokenException(Exception):
        def __init__(self) -> None:
            super().__init__("Incorrect `api_key` or `access_token`.")
            self.message = "Incorrect `api_key` or `access_token`."
            self.code = 403

    text = format_kite_exception(TokenException())
    assert "TokenException" in text
    assert "api_key" in text
    assert "403" in text


def test_mask_secret_hides_middle() -> None:
    assert "SECRETVALUE99" not in mask_secret("SECRETVALUE99")
    assert "len=" in mask_secret("SECRETVALUE99")
