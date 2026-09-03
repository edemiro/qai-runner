"""Provider-neutral shapes the agent speaks.

Each provider translates these into its own SDK's wire format. The agent never
sees a provider-specific type, so adding a fourth provider touches only this
package.
"""

from dataclasses import dataclass
from typing import AsyncIterator, List, Optional, Protocol, runtime_checkable


@dataclass
class Turn:
    """One conversational turn.

    `image_b64` carries a PNG screenshot of the device or page. Only the newest
    turn should have one — re-sending every historical frame is what makes an
    agent run cost grow quadratically with its step count.
    """
    role: str  # "user" | "assistant"
    text: str
    image_b64: Optional[str] = None


@dataclass
class ProviderError(Exception):
    """A provider failure worth showing the user verbatim."""
    message: str
    kind: str = "error"  # error | auth | rate_limit | refusal

    def __str__(self) -> str:
        return self.message


@runtime_checkable
class LLMProvider(Protocol):
    """What the agent needs from a model, and nothing more."""

    id: str
    label: str

    async def stream(
        self,
        system: str,
        turns: List[Turn],
        model: str,
        api_key: str,
        effort: str = "medium",
    ) -> AsyncIterator[str]:
        """Yield response text as it arrives.

        `effort` is how hard the model should think about a single step —
        "low", "medium" or "high". It is the one knob that reliably trades run
        speed against judgement on a hard screen, so the agent exposes it per
        run. A provider whose SDK has no equivalent accepts it and ignores it
        rather than failing: a missing dial is not a reason to refuse the run.
        """
        ...

    async def check(self, model: str, api_key: str) -> str:
        """Round-trip a trivial prompt. Returns a short snippet, raises on failure."""
        ...
