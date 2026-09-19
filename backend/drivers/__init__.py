"""Targets the agent can drive: a mobile device, or a browser page."""

from typing import Any, Dict, Optional

from .base import ActionResult, Snapshot, UITarget
from .mobile import MobileTarget
from .web import WebTarget

# session_id -> target. One registry for both kinds, so every route that takes
# a session id works the same whether it is a phone or a page.
_targets: Dict[str, UITarget] = {}

# session_id -> what this target is being used for, when it is not the tester's
# own. A suite run opens a browser per scenario; those are registered so the
# mirror can reach them and the run can be watched, but they must not be
# mistaken for the page the tester opened themselves — the workspace would
# otherwise offer to close a browser the run is driving.
_owners: Dict[str, Dict[str, Any]] = {}


def register(target: UITarget, owner: Optional[Dict[str, Any]] = None) -> UITarget:
    _targets[target.session_id] = target
    if owner:
        _owners[target.session_id] = owner
    else:
        _owners.pop(target.session_id, None)
    return target


def get(session_id: str) -> Optional[UITarget]:
    return _targets.get(session_id)


def owner(session_id: str) -> Optional[Dict[str, Any]]:
    """What is driving this session, or None when it is the tester's own page."""
    return _owners.get(session_id)


def all_targets() -> Dict[str, UITarget]:
    return dict(_targets)


async def close(session_id: str) -> bool:
    target = _targets.pop(session_id, None)
    _owners.pop(session_id, None)
    if target is None:
        return False
    await target.close()
    return True


async def close_all() -> None:
    for session_id in list(_targets):
        await close(session_id)


__all__ = [
    "ActionResult", "Snapshot", "UITarget", "MobileTarget", "WebTarget",
    "register", "get", "owner", "all_targets", "close", "close_all",
]
