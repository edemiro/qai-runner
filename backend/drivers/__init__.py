"""Targets the agent can drive: a mobile device, or a browser page."""

from typing import Dict, Optional

from .base import ActionResult, Snapshot, UITarget
from .mobile import MobileTarget
from .web import WebTarget

# session_id -> target. One registry for both kinds, so every route that takes
# a session id works the same whether it is a phone or a page.
_targets: Dict[str, UITarget] = {}


def register(target: UITarget) -> UITarget:
    _targets[target.session_id] = target
    return target


def get(session_id: str) -> Optional[UITarget]:
    return _targets.get(session_id)


def all_targets() -> Dict[str, UITarget]:
    return dict(_targets)


async def close(session_id: str) -> bool:
    target = _targets.pop(session_id, None)
    if target is None:
        return False
    await target.close()
    return True


async def close_all() -> None:
    for session_id in list(_targets):
        await close(session_id)


__all__ = [
    "ActionResult", "Snapshot", "UITarget", "MobileTarget", "WebTarget",
    "register", "get", "all_targets", "close", "close_all",
]
