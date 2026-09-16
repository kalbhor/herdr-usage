"""Types shared by every usage provider."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


@dataclass
class UsageWindow:
    """One rate-limit window. `detail` replaces the reset text when set (e.g. spend amounts)."""

    label: str
    percent: float
    resets_at: Optional[datetime] = None
    detail: str = ""

    def to_json(self) -> Dict[str, Any]:
        return {
            "label": self.label,
            "percent": self.percent,
            "resets_at": self.resets_at.isoformat() if self.resets_at else None,
            "detail": self.detail,
        }

    @classmethod
    def from_json(cls, data: Dict[str, Any]) -> "UsageWindow":
        return cls(
            label=str(data.get("label", "")),
            percent=float(data.get("percent") or 0),
            resets_at=parse_time(data.get("resets_at")),
            detail=str(data.get("detail") or ""),
        )


@dataclass
class UsageReport:
    provider: str
    title: str
    subtitle: str = ""
    windows: List[UsageWindow] = field(default_factory=list)
    fetched_at: Optional[datetime] = None

    def to_json(self) -> Dict[str, Any]:
        return {
            "provider": self.provider,
            "title": self.title,
            "subtitle": self.subtitle,
            "windows": [w.to_json() for w in self.windows],
            "fetched_at": self.fetched_at.isoformat() if self.fetched_at else None,
        }

    @classmethod
    def from_json(cls, data: Dict[str, Any]) -> "UsageReport":
        return cls(
            provider=str(data.get("provider", "")),
            title=str(data.get("title", "")),
            subtitle=str(data.get("subtitle") or ""),
            windows=[UsageWindow.from_json(w) for w in data.get("windows") or []],
            fetched_at=parse_time(data.get("fetched_at")),
        )


class ProviderError(Exception):
    """Fetch failed for a reason worth showing to the user as-is."""


class Provider:
    """A service whose subscription limits can be fetched. Subclasses set `id`, `title` and implement `fetch`."""

    id = ""
    title = ""

    def fetch(self) -> UsageReport:
        raise NotImplementedError


def parse_time(value: Any) -> Optional[datetime]:
    """Parse an ISO-8601 timestamp into an aware datetime; naive values are assumed UTC."""
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed

