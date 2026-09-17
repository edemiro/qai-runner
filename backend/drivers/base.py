"""The surface the agent needs from a target it is driving.

The agent owns the loop, the prompt and the verdict. A driver owns "how do I
read this screen" and "how do I act on it". Mobile and web differ only there,
which is why the loop, the locator, the run recorder and the exporters are all
shared.
"""

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Protocol, runtime_checkable


@dataclass
class ActionResult:
    ok: bool
    message: str
    element: Optional[Dict[str, Any]] = None


@runtime_checkable
class Snapshot(Protocol):
    """One capture of a screen or page.

    Satisfied by both MobileDOMManager and WebSnapshot without either knowing
    about this protocol.
    """
    snapshot_id: str
    screen_width: int
    screen_height: int

    def get_optimized_tree(self) -> Dict[str, Any]: ...
    def get_optimized_tree_for_llm(self) -> Dict[str, Any]: ...
    def get_all_elements(self) -> List[Any]: ...
    def contains_text(self, needle: str) -> bool: ...
    def visible_text(self) -> List[str]: ...


@runtime_checkable
class UITarget(Protocol):
    """A device session or a browser page."""

    kind: str  # "mobile" | "web"
    session_id: str

    async def snapshot(self) -> Optional[Snapshot]:
        """Capture the current screen and register it."""
        ...

    async def screenshot(self) -> Optional[str]:
        """Base64 PNG of the current screen, or None."""
        ...

    async def act(
        self,
        kind: str,
        element_id: Optional[str],
        selector: Optional[str],
        value: Optional[str],
        snapshot_id: Optional[str],
    ) -> ActionResult:
        """click / type / clear / assert_visible against one element."""
        ...

    async def scroll(self, direction: str, element_id: Optional[str] = None) -> ActionResult: ...

    async def press_key(self, key: str) -> ActionResult: ...

    async def element_at(self, x: int, y: int) -> Optional[Dict[str, Any]]:
        """Smallest element containing the point — powers click-to-inspect."""
        ...

    def describe(self) -> Dict[str, Any]:
        """Metadata recorded on every run this target produces."""
        ...

    async def close(self) -> None: ...
