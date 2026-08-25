"""Standard-library logging setup. Secrets must never be emitted."""

from __future__ import annotations

import logging
import sys

_SECRET_KEYS = ("access_token", "api_key", "authorization", "password", "secret")


class _RedactFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        lowered = message.lower()
        for key in _SECRET_KEYS:
            if key in lowered and len(message) > 16:
                record.msg = "[redacted log record containing credential-like key]"
                record.args = ()
                break
        return True


def setup_logging(level: str = "INFO") -> None:
    root = logging.getLogger()
    if root.handlers:
        root.setLevel(getattr(logging, level.upper(), logging.INFO))
        return
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
    )
    handler.addFilter(_RedactFilter())
    root.addHandler(handler)
    root.setLevel(getattr(logging, level.upper(), logging.INFO))
    logging.getLogger("kiteconnect").setLevel(logging.WARNING)
