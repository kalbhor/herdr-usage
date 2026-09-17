"""Codex (ChatGPT plan) rate limits, read from the usage endpoint the Codex CLI's /status uses."""

from __future__ import annotations

import base64
import json
import os
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from .base import Provider, ProviderError, UsageReport, UsageWindow

USAGE_URL = "https://chatgpt.com/backend-api/wham/usage"
HTTP_TIMEOUT = 10


class CodexProvider(Provider):
    id = "codex"
    title = "Codex"

    def fetch(self) -> UsageReport:
        tokens = load_tokens()
        token = tokens["access_token"]
        expiry = token_expiry(token)
        if expiry is not None and expiry < time.time():
            raise ProviderError("Codex token expired; run `codex` once to refresh it")

        data = fetch_usage(token, str(tokens.get("account_id") or ""))
        return UsageReport(
            provider=self.id,
            title=self.title,
            subtitle=subtitle(data),
            windows=windows_from_response(data),
            fetched_at=datetime.now(timezone.utc),
        )


def load_tokens() -> Dict[str, Any]:
    """Return the `tokens` block from Codex's auth file. Never logs or copies the token."""
    home = Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex")
    path = home / "auth.json"
    if not path.is_file():
        raise ProviderError(f"no Codex credentials at {path}; run `codex` and sign in first")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ProviderError(f"cannot read {path}: {exc.strerror}") from exc
    except ValueError as exc:
        raise ProviderError("cannot parse Codex credentials") from exc
    if not isinstance(data, dict):
        raise ProviderError("cannot parse Codex credentials")
    tokens = data.get("tokens")
    if not isinstance(tokens, dict) or not isinstance(tokens.get("access_token"), str) or not tokens["access_token"]:
        if data.get("OPENAI_API_KEY"):
            raise ProviderError("Codex is signed in with an API key; plan limits only exist for ChatGPT sign-in")
        raise ProviderError("Codex is not signed in with a ChatGPT account")
    return tokens


def token_expiry(token: str) -> Optional[float]:
    """Read the `exp` claim from the JWT without verifying it; None when the token is not a readable JWT."""
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        claims = json.loads(base64.urlsafe_b64decode(payload))
    except (IndexError, ValueError):
        return None
    exp = claims.get("exp") if isinstance(claims, dict) else None
    return float(exp) if isinstance(exp, (int, float)) else None


def fetch_usage(token: str, account_id: str) -> Dict[str, Any]:
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
        "User-Agent": "herdr-usage",
    }
    if account_id:
        headers["ChatGPT-Account-Id"] = account_id
    request = urllib.request.Request(USAGE_URL, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT) as response:
            data = json.load(response)
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403):
            raise ProviderError("unauthorized; run `codex` once to refresh the token") from exc
        if exc.code == 429:
            raise ProviderError("usage endpoint rate limited; try again shortly") from exc
        raise ProviderError(f"usage endpoint returned HTTP {exc.code}") from exc
    except OSError as exc:
        reason = getattr(exc, "reason", None) or exc
        raise ProviderError(f"network error: {reason}") from exc
    except ValueError as exc:
        raise ProviderError("usage endpoint returned invalid JSON") from exc
    if not isinstance(data, dict):
        raise ProviderError("usage endpoint returned an unexpected payload")
    return data


def windows_from_response(data: Dict[str, Any]) -> List[UsageWindow]:
    rate_limit = data.get("rate_limit")
    if not isinstance(rate_limit, dict):
        return []
    windows = []
    for key in ("primary_window", "secondary_window"):
        window = window_from_entry(rate_limit.get(key))
        if window is not None:
            windows.append(window)
    return windows


def window_from_entry(entry: Any) -> Optional[UsageWindow]:
    if not isinstance(entry, dict):
        return None
    percent = entry.get("used_percent")
    if not isinstance(percent, (int, float)):
        return None
    resets_at = None
    reset_unix = entry.get("reset_at")
    reset_after = entry.get("reset_after_seconds")
    if isinstance(reset_unix, (int, float)):
        resets_at = datetime.fromtimestamp(reset_unix, tz=timezone.utc)
    elif isinstance(reset_after, (int, float)):
        resets_at = datetime.now(timezone.utc) + timedelta(seconds=reset_after)
    return UsageWindow(window_label(entry.get("limit_window_seconds")), float(percent), resets_at)


def window_label(seconds: Any) -> str:
    if not isinstance(seconds, (int, float)) or seconds <= 0:
        return "Window"
    seconds = int(seconds)
    if seconds % 86400 == 0:
        days = seconds // 86400
        return "Week" if days == 7 else f"{days}d window"
    if seconds % 3600 == 0:
        return f"Session ({seconds // 3600}h)"
    return f"{seconds // 60}m window"


def subtitle(data: Dict[str, Any]) -> str:
    parts = [str(data.get("plan_type") or "").replace("_", " ")]
    rate_limit = data.get("rate_limit")
    reached = data.get("rate_limit_reached_type")
    if isinstance(rate_limit, dict) and rate_limit.get("limit_reached"):
        reason = reached.get("type") if isinstance(reached, dict) else reached
        parts.append(str(reason).replace("_", " ") if reason else "limit reached")
    return " · ".join(p for p in parts if p)
