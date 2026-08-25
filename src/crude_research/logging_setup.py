"""Standard-library logging setup. Secrets must never be emitted."""

from __future__ import annotations

import logging
import re
import sys

# Only redact values that look like they include a live secret, not field names
# in Kite error text such as: Incorrect `api_key` or `access_token`.
_ASSIGNMENT = re.compile(
    r"(?i)(api[_-]?key|access[_-]?token|authorization|password|secret)\s*[:=]\s*\S{8,}"
)
_BEARER = re.compile(r"(?i)(authorization\s*:\s*token\s+)\S+")


class _RedactFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:
            return True
        if _ASSIGNMENT.search(message) or _BEARER.search(message):
            record.msg = "[redacted log record containing credential assignment]"
            record.args = ()
        return True


def setup_logging(level: str = "INFO") -> None:
    root = logging.getLogger()
    if root.handlers:
        root.setLevel(getattr(logging, level.upper(), logging.INFO))
        return
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    handler.addFilter(_RedactFilter())
    root.addHandler(handler)
    root.setLevel(getattr(logging, level.upper(), logging.INFO))
    logging.getLogger("kiteconnect").setLevel(logging.WARNING)
