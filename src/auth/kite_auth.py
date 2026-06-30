"""Zerodha Kite Connect authentication manager."""

from __future__ import annotations

import webbrowser
from typing import Optional
from urllib.parse import urlparse, parse_qs

from kiteconnect import KiteConnect
from loguru import logger

from src.config import ROOT_DIR, get_env


class KiteAuth:
    """Handles Kite Connect OAuth flow and session management."""

    def __init__(self):
        self.env = get_env()
        self.kite: Optional[KiteConnect] = None
        self._init_client()

    def _init_client(self):
        if not self.env.kite_api_key:
            logger.warning("KITE_API_KEY not configured")
            return

        self.kite = KiteConnect(api_key=self.env.kite_api_key)
        token = self._load_token()
        if token:
            self.kite.set_access_token(token)
            logger.info("Kite session restored from saved token")

    def get_login_url(self) -> str:
        if not self.kite:
            raise RuntimeError("Kite client not initialized. Set KITE_API_KEY in .env")
        return self.kite.login_url()

    def authenticate_interactive(self) -> bool:
        """Open browser for login and prompt for request token."""
        login_url = self.get_login_url()
        print(f"\n{'='*60}")
        print("ZERODHA KITE LOGIN")
        print(f"{'='*60}")
        print(f"Open this URL in your browser:\n{login_url}\n")
        print("After login, copy the 'request_token' from the redirect URL.")
        print(f"{'='*60}\n")

        try:
            webbrowser.open(login_url)
        except Exception:
            pass

        request_token = input("Paste request_token here: ").strip()
        if not request_token:
            if "request_token=" in request_token:
                parsed = urlparse(request_token)
                request_token = parse_qs(parsed.query).get("request_token", [""])[0]

        return self.generate_session(request_token)

    def authenticate_from_url(self, redirect_url: str) -> bool:
        parsed = urlparse(redirect_url)
        request_token = parse_qs(parsed.query).get("request_token", [""])[0]
        if not request_token:
            logger.error("No request_token found in URL")
            return False
        return self.generate_session(request_token)

    def generate_session(self, request_token: str) -> bool:
        if not self.kite or not self.env.kite_api_secret:
            logger.error("API key/secret not configured")
            return False

        try:
            data = self.kite.generate_session(
                request_token, api_secret=self.env.kite_api_secret
            )
            access_token = data["access_token"]
            self.kite.set_access_token(access_token)
            self._save_token(access_token)
            self.env.kite_access_token = access_token
            logger.info(f"Authenticated as {data.get('user_name', 'user')}")
            return True
        except Exception as e:
            logger.error(f"Authentication failed: {e}")
            return False

    def is_authenticated(self) -> bool:
        if not self.kite:
            return False
        try:
            self.kite.profile()
            return True
        except Exception:
            return False

    def get_client(self) -> KiteConnect:
        if not self.kite:
            raise RuntimeError("Kite not initialized")
        if not self.is_authenticated():
            raise RuntimeError("Not authenticated. Run login first.")
        return self.kite

    def _save_token(self, token: str):
        token_file = ROOT_DIR / "data" / ".access_token"
        token_file.parent.mkdir(parents=True, exist_ok=True)
        token_file.write_text(token)
        token_file.chmod(0o600)

    def _load_token(self) -> Optional[str]:
        token_file = ROOT_DIR / "data" / ".access_token"
        if token_file.exists():
            return token_file.read_text().strip()
        return self.env.kite_access_token or None
