"""Usage providers. Register a new service by adding its class to PROVIDERS."""

from typing import Dict, Type

from .base import Provider, ProviderError, UsageReport, UsageWindow
from .claude import ClaudeProvider

PROVIDERS: Dict[str, Type[Provider]] = {
    ClaudeProvider.id: ClaudeProvider,
}

__all__ = ["PROVIDERS", "Provider", "ProviderError", "UsageReport", "UsageWindow"]
