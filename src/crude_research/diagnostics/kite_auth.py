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


def token_exception_hints(
    api_key: str | None,
    access_token: str | None,
    api_secret: str | None = None,
) -> list[str]:
    hints = [
        "Kite `access_token` is a *daily* session token. It is not the API secret.",
        "A 16-char KITE_API_KEY looks like an api_key. A 32-char KITE_ACCESS_TOKEN is also the length of api_secret.",
        "If you pasted the developers-console api_secret into KITE_ACCESS_TOKEN, Kite returns exactly this 403.",
        "Put api_secret in KITE_API_SECRET instead, then exchange a request_token:",
        "  python -m crude_research.cli kite login-url",
        "  python -m crude_research.cli kite session --request-token <token_from_redirect_url>",
        "This project does not log in with password/PIN/TOTP. You complete login in the browser.",
        "https://kite.trade/docs/connect/v3/user/#login-flow",
    ]
    if api_key and access_token and api_key == access_token:
        hints.insert(0, "API key and access token are identical — that is almost certainly wrong.")
    if api_secret and access_token and api_secret == access_token:
        hints.insert(
            0,
            "KITE_API_SECRET and KITE_ACCESS_TOKEN are identical — the access_token is the secret, not a session token.",
        )
    if not api_secret:
        hints.insert(
            0,
            "KITE_API_SECRET is empty. Copy api_secret from the developers console into that field, not into KITE_ACCESS_TOKEN.",
        )
    if api_key and len(api_key) == 16 and access_token and len(access_token) == 32:
        hints.insert(
            0,
            "Likely mix-up: api_key length is 16 (ok) but access_token length is 32 (same as api_secret).",
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
    secret = inspect_secret("KITE_API_SECRET", settings.kite_api_secret)
    lines = [
        f"cwd={Path.cwd()}",
        f".env {env_file_status()}",
        f"{key.name}: {key.fingerprint}"
        + (f" issues={','.join(key.issues)}" if key.issues else ""),
        f"{secret.name}: {secret.fingerprint}"
        + (f" issues={','.join(secret.issues)}" if secret.issues else ""),
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
        if name not in {"KITE_API_KEY", "KITE_ACCESS_TOKEN", "KITE_API_SECRET"}:
            continue
        view = inspect_secret(name, value)
        if view.issues and view.issues != ("MISSING",):
            notes.append(f".env {name} line issues={','.join(view.issues)} (values not printed)")
    return notes


def upsert_env_value(path: Path, key: str, value: str) -> None:
    """Set `key=value` in a .env file without logging the value."""
    if key != key.upper() or not key.replace("_", "").isalnum():
        raise ValueError(f"refusing to write unexpected env key {key!r}")
    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    lines = existing.splitlines()
    prefix = f"{key}="
    replaced = False
    out: list[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith(prefix):
            if not replaced:
                out.append(f"{key}={value}")
                replaced = True
            continue
        out.append(line)
    if not replaced:
        if out and out[-1] != "":
            out.append("")
        out.append(f"{key}={value}")
    path.write_text("\n".join(out) + "\n", encoding="utf-8")


_PLACEHOLDER_REQUEST_TOKENS = frozenset(
    {
        "REQUEST_TOKEN",
        "PASTE_TOKEN_HERE",
        "PASTE_THE_REDIRECT_TOKEN",
        "TOKEN_FROM_REDIRECT_URL",
        "<TOKEN_FROM_REDIRECT_URL>",
        "<TOKEN_FROM_REDIRECT>",
    }
)


def is_placeholder_request_token(value: str) -> bool:
    """True when the user pasted a docs placeholder instead of the redirect token."""
    text = value.strip()
    if not text:
        return True
    compact = text.upper().replace("-", "_")
    return compact in _PLACEHOLDER_REQUEST_TOKENS or compact.strip("<>") in _PLACEHOLDER_REQUEST_TOKENS


def env_has_key(path: Path, key: str) -> bool:
    if not path.is_file():
        return False
    prefix = f"{key}="
    return any(line.strip().startswith(prefix) for line in path.read_text(encoding="utf-8").splitlines())


def ensure_env_key(path: Path, key: str) -> bool:
    """Append `key=` when missing. Returns True if a line was added."""
    if env_has_key(path, key):
        return False
    upsert_env_value(path, key, "")
    return True
