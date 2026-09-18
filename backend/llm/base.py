"""Provider-neutral shapes the agent speaks.

Each provider translates these into its own SDK's wire format. The agent never
sees a provider-specific type, so adding a fourth provider touches only this
package.
"""

from dataclasses import dataclass
from typing import Any, AsyncIterator, Dict, List, Optional, Protocol, runtime_checkable


def add_usage(sink: Optional[Dict[str, Any]], **counts: int) -> None:
    """Accumulate token counts into a caller-supplied dict.

    A dict rather than a return value because a stream yields text and cannot
    also return a total; a caller-supplied one rather than a module global
    because a suite run has several agents streaming at once and their totals
    must not land in the same place.
    """
    if sink is None:
        return
    for name, value in counts.items():
        if value:
            sink[name] = sink.get(name, 0) + int(value)
    sink["calls"] = sink.get("calls", 0) + 1


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
        usage: Optional[Dict[str, Any]] = None,
    ) -> AsyncIterator[str]:
        """Yield response text as it arrives.

        `usage` is an optional dict the provider adds this call's token counts
        to (see `add_usage`), so a run can report what it actually cost. A
        provider whose SDK does not report usage simply leaves it alone.

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
