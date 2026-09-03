"""Pluggable LLM providers for the QAi agent.

The agent talks to `registry.get()` and the neutral `Turn` shape only; each
provider owns its own official SDK and error translation.
"""

from .base import LLMProvider, ProviderError, Turn

__all__ = ["LLMProvider", "ProviderError", "Turn"]
