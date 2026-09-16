"""Claude Code subscription limits, read from the same OAuth usage endpoint Claude Code's /usage uses."""

from __future__ import annotations

import json
import os
import platform
import subprocess
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from .base import Provider, ProviderError, UsageReport, UsageWindow, parse_time

USAGE_URL = "https://api.anthropic.com/api/oauth/usage"
KEYCHAIN_SERVICE = "Claude Code-credentials"
HTTP_TIMEOUT = 10

WINDOW_LABELS = {
    "session": "Session (5h)",
    "weekly_all": "Week (all models)",
}

# Fallback for responses without the generic `limits` array.
LEGACY_WINDOWS = [
    ("five_hour", "Session (5h)"),
    ("seven_day", "Week (all models)"),
    ("seven_day_opus", "Week (Opus)"),
    ("seven_day_sonnet", "Week (Sonnet)"),
]


class ClaudeProvider(Provider):
    id = "claude"
    title = "Claude Code"

    def fetch(self) -> UsageReport:
        creds = load_credentials()
        token = creds.get("accessToken")
        if not isinstance(token, str) or not token:
            raise ProviderError("Claude Code credentials have no access token")
        expires_at = creds.get("expiresAt")
        if isinstance(expires_at, (int, float)) and expires_at / 1000 < time.time():
            raise ProviderError("Claude Code token expired; open Claude Code once to refresh it")

        data = fetch_usage(token)
        windows = windows_from_response(data)
        spend = spend_window(data)
        if spend:
            windows.append(spend)
        return UsageReport(
            provider=self.id,
            title=self.title,
            subtitle=subtitle(creds),
            windows=windows,
            fetched_at=datetime.now(timezone.utc),
        )


def load_credentials() -> Dict[str, Any]:
    """Return the `claudeAiOauth` block from Claude Code's credential store. Never logs or copies the token."""
    config_dir = Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude")
    path = config_dir / ".credentials.json"
    raw: Optional[str] = None
    if path.is_file():
        try:
            raw = path.read_text(encoding="utf-8")
        except OSError as exc:
            raise ProviderError(f"cannot read {path}: {exc.strerror}") from exc
    elif platform.system() == "Darwin":
        raw = read_keychain()
    if raw is None:
        raise ProviderError(f"no Claude Code credentials at {path}; run `claude` and log in first")
    try:
        creds = json.loads(raw).get("claudeAiOauth")
    except (ValueError, AttributeError) as exc:
        raise ProviderError("cannot parse Claude Code credentials") from exc
    if not isinstance(creds, dict):
        raise ProviderError("Claude Code is not logged in with a claude.ai subscription")
    return creds


def read_keychain() -> Optional[str]:
    """macOS builds of Claude Code keep credentials in the login keychain instead of a file."""
    try:
        result = subprocess.run(
            ["security", "find-generic-password", "-s", KEYCHAIN_SERVICE, "-w"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    secret = result.stdout.strip()
    return secret if result.returncode == 0 and secret else None


def fetch_usage(token: str) -> Dict[str, Any]:
    request = urllib.request.Request(
        USAGE_URL,
        headers={
            "Authorization": f"Bearer {token}",
            "anthropic-beta": "oauth-2025-04-20",
            "Accept": "application/json",
            "User-Agent": "herdr-usage",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT) as response:
            data = json.load(response)
    except urllib.error.HTTPError as exc:
        if exc.code == 401:
            raise ProviderError("unauthorized; open Claude Code once to refresh the token") from exc
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
    limits = data.get("limits")
    if isinstance(limits, list) and limits:
        windows = [window_from_limit(item) for item in limits if isinstance(item, dict)]
        return [w for w in windows if w is not None]

    windows = []
    for key, label in LEGACY_WINDOWS:
        entry = data.get(key)
        if isinstance(entry, dict) and entry.get("utilization") is not None:
            windows.append(UsageWindow(label, float(entry["utilization"]), parse_time(entry.get("resets_at"))))
    return windows


def window_from_limit(limit: Dict[str, Any]) -> Optional[UsageWindow]:
    percent = limit.get("percent")
    if not isinstance(percent, (int, float)):
        return None
    kind = str(limit.get("kind") or "")
    label = WINDOW_LABELS.get(kind)
    if label is None:
        if kind == "weekly_scoped":
            label = f"Week ({scope_name(limit.get('scope')) or 'scoped'})"
        else:
            label = kind.replace("_", " ").capitalize() or "Limit"
    return UsageWindow(label, float(percent), parse_time(limit.get("resets_at")))


def scope_name(scope: Any) -> str:
    if not isinstance(scope, dict):
        return ""
    model = scope.get("model")
    if isinstance(model, dict) and model.get("display_name"):
        return str(model["display_name"])
    return str(scope.get("surface") or "")


def spend_window(data: Dict[str, Any]) -> Optional[UsageWindow]:
    """Extra-usage spend as a window, only when the account has a non-zero spend limit."""
    spend = data.get("spend")
    if not isinstance(spend, dict) or not spend.get("enabled"):
        return None
    limit = spend.get("limit") or {}
    used = spend.get("used") or {}
    limit_minor = limit.get("amount_minor") or 0
    if not limit_minor:
        return None
    exponent = int(limit.get("exponent") or 2)
    currency = str(limit.get("currency") or used.get("currency") or "")
    scale = 10 ** exponent
    detail = f"{(used.get('amount_minor') or 0) / scale:.{exponent}f} / {limit_minor / scale:.{exponent}f} {currency}".strip()
    percent = spend.get("percent")
    if not isinstance(percent, (int, float)):
        percent = 100.0 * (used.get("amount_minor") or 0) / limit_minor
    return UsageWindow("Extra usage", float(percent), None, detail)


def subtitle(creds: Dict[str, Any]) -> str:
    plan = str(creds.get("subscriptionType") or "")
    tier = str(creds.get("rateLimitTier") or "")
    for prefix in ("default_claude_", "default_"):
        if tier.startswith(prefix):
            tier = tier[len(prefix):]
    tier = tier.replace("_", " ")
    return " · ".join(part for part in (plan, tier) if part)
