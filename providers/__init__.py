"""Usage providers. Register a new service by adding its class to PROVIDERS."""

from typing import Dict, Type

from .base import Provider, ProviderError, UsageReport, UsageWindow
from .claude import ClaudeProvider
from .codex import CodexProvider

PROVIDERS: Dict[str, Type[Provider]] = {
    ClaudeProvider.id: ClaudeProvider,
    CodexProvider.id: CodexProvider,
}

__all__ = ["PROVIDERS", "Provider", "ProviderError", "UsageReport", "UsageWindow"]
