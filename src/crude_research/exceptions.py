"""Project-specific exceptions. Fail loudly; never guess market semantics."""

from __future__ import annotations

from datetime import date
from typing import Any


class CrudeResearchError(Exception):
    """Base error for this research library."""


class CredentialsMissingError(CrudeResearchError):
    """Kite API key or access token is not configured."""


class AuthenticationRequiredError(CrudeResearchError):
    """Market-data call blocked: no current Kite session for today."""

    code = "AUTHENTICATION_REQUIRED"

    def __init__(self, detail: str = "Kite session is missing or expired") -> None:
        super().__init__(f"AUTHENTICATION_REQUIRED: {detail}")


class InstrumentMasterError(CrudeResearchError):
    """Instrument master missing, unreadable, or malformed."""


class AmbiguousFutureMappingError(CrudeResearchError):
    """Option-to-futures mapping cannot be proven from available metadata."""

    def __init__(self, message: str, *, candidates: list[dict[str, Any]] | None = None) -> None:
        super().__init__(message)
        self.candidates = candidates or []


class UnknownOptionExpiryError(CrudeResearchError):
    """Requested option expiry is not in the instrument master."""

    def __init__(self, message: str, *, available: list[date] | None = None) -> None:
        super().__init__(message)
        self.available = available or []


class QuoteRequestError(CrudeResearchError):
    """Full-quote retrieval failed or returned unusable data."""


class ConfigurationError(CrudeResearchError):
    """Invalid configuration that must be fixed before continuing."""


class NoTrade(CrudeResearchError):
    """Fail-closed decision: do not enter a new position."""

    def __init__(self, reason: str, *extra: str) -> None:
        self.reasons = (reason, *extra)
        super().__init__(reason)
