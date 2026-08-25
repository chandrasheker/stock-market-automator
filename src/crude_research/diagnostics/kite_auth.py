"""Safe Kite credential diagnostics. Never prints secrets or full tokens."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from crude_research.config import Settings


@dataclass(frozen=True)
class CredentialView:
    name: str
    present: bool
    length: int
    fingerprint: str
    issues: tuple[str, ...]


def mask_secret(value: str | None) -> str:
    """Return a non-reversible preview: first 2 + last 2 chars and length."""
    if not value:
        return "<empty>"
    if len(value) <= 4:
        return f"<len={len(value)}>"
    return f"{value[:2]}…{value[-2:]} (len={len(value)})"


def inspect_secret(name: str, raw: str | None) -> CredentialView:
    issues: list[str] = []
    if raw is None or raw == "":
        return CredentialView(name, False, 0, "<empty>", ("MISSING",))
    if raw != raw.strip():
        issues.append("LEADING_OR_TRAILING_WHITESPACE")
    if "\n" in raw or "\r" in raw:
        issues.append("CONTAINS_NEWLINE")
    stripped = raw.strip().strip("\ufeff")
    if (stripped.startswith('"') and stripped.endswith('"')) or (
        stripped.startswith("'") and stripped.endswith("'")
    ):
        issues.append("WRAPPED_IN_QUOTES")
    if any(ord(ch) < 32 for ch in raw if ch not in "\t"):
        issues.append("CONTROL_CHARS")
    if any(ord(ch) > 127 for ch in raw):
        issues.append("NON_ASCII")
    if " " in stripped.strip("\"'"):
        issues.append("INTERNAL_SPACE")
    return CredentialView(
        name=name,
        present=True,
        length=len(raw),
        fingerprint=mask_secret(raw.strip().strip("\ufeff").strip("\"'")),
        issues=tuple(issues),
    )


def format_kite_exception(exc: BaseException) -> str:
    """Human-readable Kite error without dumping headers or tokens."""
    pieces = [type(exc).__name__]
    message = getattr(exc, "message", None) or str(exc)
    message = " ".join(str(message).split())
    if message and message != type(exc).__name__:
        pieces.append(message)
    code = getattr(exc, "code", None)
    if code not in (None, ""):
        pieces.append(f"code={code}")
    return ": ".join(pieces[:2]) + (f" ({pieces[2]})" if len(pieces) > 2 else "")


def token_exception_hints(api_key: str | None, access_token: str | None) -> list[str]:
    hints = [
        "Kite `access_token` is a *daily* session token. It is not the API secret.",
        "Do not put `api_secret` or the one-time `request_token` in KITE_ACCESS_TOKEN.",
        "Generate a fresh access_token today: login → request_token → kite.generate_session(request_token, api_secret).",
        "Then set KITE_API_KEY=<api_key> and KITE_ACCESS_TOKEN=<access_token> in .env (no quotes, no spaces).",
        "https://kite.trade/docs/connect/v3/user/#login-flow",
    ]
    if api_key and access_token and api_key == access_token:
        hints.insert(0, "API key and access token are identical — that is almost certainly wrong.")
    if access_token and len(access_token) in {32, 64} and api_key and len(api_key) == 32:
        hints.insert(
            0,
            "Both values look 32 chars: confirm ACCESS_TOKEN is the session token, not api_secret.",
        )
    return hints


def env_file_status(cwd: Path | None = None) -> str:
    here = cwd or Path.cwd()
    env_path = here / ".env"
    if env_path.is_file():
        return f"found {env_path.resolve()}"
    return f"not found at {env_path.resolve()} (pydantic still reads process env vars)"


def describe_settings_load(settings: Settings) -> list[str]:
    key = inspect_secret("KITE_API_KEY", settings.kite_api_key)
    token = inspect_secret("KITE_ACCESS_TOKEN", settings.kite_access_token)
    lines = [
        f"cwd={Path.cwd()}",
        f".env {env_file_status()}",
        f"{key.name}: {key.fingerprint}"
        + (f" issues={','.join(key.issues)}" if key.issues else ""),
        f"{token.name}: {token.fingerprint}"
        + (f" issues={','.join(token.issues)}" if token.issues else ""),
    ]
    env_path = Path.cwd() / ".env"
    if env_path.is_file():
        lines.extend(_env_file_line_issues(env_path))
    return lines


def _env_file_line_issues(path: Path) -> list[str]:
    """Flag quote/whitespace problems in .env without printing secret values."""
    notes: list[str] = []
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        return [f".env unreadable: {exc}"]
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        name, _, value = stripped.partition("=")
        name = name.strip()
        if name not in {"KITE_API_KEY", "KITE_ACCESS_TOKEN"}:
            continue
        view = inspect_secret(name, value)
        if view.issues and view.issues != ("MISSING",):
            notes.append(f".env {name} line issues={','.join(view.issues)} (values not printed)")
    return notes
