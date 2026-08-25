"""Project-specific exceptions. Fail loudly; never guess market semantics."""

from __future__ import annotations

from typing import Any


class CrudeResearchError(Exception):
    """Base error for this research library."""


class CredentialsMissingError(CrudeResearchError):
    """Kite API key or access token is not configured."""


class InstrumentMasterError(CrudeResearchError):
    """Instrument master missing, unreadable, or malformed."""


class AmbiguousFutureMappingError(CrudeResearchError):
    """Option-to-futures mapping cannot be proven from available metadata."""

    def __init__(self, message: str, *, candidates: list[dict[str, Any]] | None = None) -> None:
        super().__init__(message)
        self.candidates = candidates or []


class QuoteRequestError(CrudeResearchError):
    """Full-quote retrieval failed or returned unusable data."""


class ConfigurationError(CrudeResearchError):
    """Invalid configuration that must be fixed before continuing."""
