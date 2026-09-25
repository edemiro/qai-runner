"""Playwright-style element resolution: semantic re-matching, auto-waiting,
auto-scrolling, and an explicit ambiguity check.

Snapshots are keyed by id and kept in a small per-session ring buffer. An action
always resolves against the snapshot the caller actually saw, so a concurrent
inspector refresh can no longer renumber the tree underneath a pending action.
"""

import asyncio
import re
import time
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import appium_client as appium
from mobile_dom import MobileDOMManager, MobileElement

# A candidate must clear this score to be accepted as "the same element".
ACCEPTANCE_THRESHOLD = 45.0
# ...and it must beat the runner-up by this margin, or the match is ambiguous.
# Without this, two visually identical rows both score highly and the first one
# in document order silently wins.
AMBIGUITY_MARGIN = 25.0

SNAPSHOTS_PER_SESSION = 8


class AmbiguousElementError(Exception):
    def __init__(self, description: str, count: int):
        super().__init__(
            f"Ambiguous target: {count} elements match '{description}' equally well. "
            "Refine the instruction with distinguishing text or an id."
        )
        self.description = description
        self.count = count


@dataclass
class ResolvedElement:
    element: MobileElement
    manager: MobileDOMManager
    screen_width: int
    screen_height: int


class SnapshotStore:
    """Per-session ring buffer of DOM snapshots, addressed by snapshot_id."""

    def __init__(self):
        self._by_session: Dict[str, List[MobileDOMManager]] = {}

    def add(self, session_id: str, manager: MobileDOMManager) -> MobileDOMManager:
        bucket = self._by_session.setdefault(session_id, [])
        bucket.append(manager)
        if len(bucket) > SNAPSHOTS_PER_SESSION:
            del bucket[0 : len(bucket) - SNAPSHOTS_PER_SESSION]
        return manager

    def latest(self, session_id: str) -> Optional[MobileDOMManager]:
        bucket = self._by_session.get(session_id)
        return bucket[-1] if bucket else None

    def get(self, session_id: str, snapshot_id: Optional[str]) -> Optional[MobileDOMManager]:
        bucket = self._by_session.get(session_id, [])
        if snapshot_id:
            for manager in reversed(bucket):
                if manager.snapshot_id == snapshot_id:
                    return manager
            return None
        return bucket[-1] if bucket else None

    def clear(self, session_id: str) -> None:
        self._by_session.pop(session_id, None)


snapshots = SnapshotStore()


def calculate_semantic_similarity(cached: MobileElement, candidate: MobileElement) -> float:
    """
    Calculates a semantic similarity score between a cached MobileElement
    and a candidate MobileElement in a newly captured DOM tree.
    """
    score = 0.0
    role_match = (cached.role == candidate.role)

    # 1. Resource ID Match
    if cached.resource_id and candidate.resource_id:
        if cached.resource_id == candidate.resource_id:
            score += 100.0
        else:
            score -= 50.0
    elif cached.resource_id or candidate.resource_id:
        score -= 10.0

    # 2. Text Match
    if cached.text and candidate.text:
        if cached.text == candidate.text:
            score += 80.0
        else:
            score -= 40.0
    elif cached.text or candidate.text:
        score -= 10.0

    # 3. Name (content-desc) Match
    if cached.name and candidate.name:
        if cached.name == candidate.name:
            score += 80.0
        else:
            score -= 40.0
    elif cached.name or candidate.name:
        score -= 10.0

    # 4. Role Match
    if role_match:
        score += 20.0
    else:
        score -= 30.0

    # 5. XPath Match
    if cached.xpath == candidate.xpath:
        score += 30.0

    return score


def _best_matches(cached: MobileElement, manager: MobileDOMManager) -> List[Tuple[float, MobileElement]]:
    scored = [
        (calculate_semantic_similarity(cached, candidate), candidate)
        for candidate in manager.get_all_elements()
    ]
    scored.sort(key=lambda pair: pair[0], reverse=True)
    return scored


async def capture_snapshot(session_id: str, platform_cache: Dict[str, str]) -> Optional[MobileDOMManager]:
    """Fetch the current screen and register it as a new snapshot."""
    xml_source = await appium.get_source(session_id)
    if not xml_source:
        return None

    platform = await appium.get_platform(session_id, platform_cache)
    size = await appium.get_window_size(session_id)
    manager = MobileDOMManager(xml_source, platform, size["width"], size["height"])
    return snapshots.add(session_id, manager)


async def _auto_scroll(session_id: str, cy: int, screen_width: int, screen_height: int) -> bool:
    """Swipe to bring an off-screen coordinate into the viewport."""
    mid_x = screen_width // 2
    if cy < 0:
        start_y, end_y = int(screen_height * 0.25), int(screen_height * 0.75)
    else:
        start_y, end_y = int(screen_height * 0.75), int(screen_height * 0.25)

    payload = {
        "actions": [
            {
                "type": "pointer",
                "id": "finger1",
                "parameters": {"pointerType": "touch"},
                "actions": [
                    {"type": "pointerMove", "duration": 0, "x": mid_x, "y": start_y},
                    {"type": "pointerDown", "button": 0},
                    {"type": "pointerMove", "duration": 800, "origin": "viewport", "x": mid_x, "y": end_y},
                    {"type": "pointerUp", "button": 0},
                ],
            }
        ]
    }
    res = await appium.post(f"/session/{session_id}/actions", payload)
    return res is not None and res.status_code == 200


_NAMED = re.compile(r'^//\*\[@([\w-]+)=(?:"([^"]*)"|\'([^\']*)\')\]$')


def _answers_to(element: MobileElement, selector: str) -> bool:
    """Does this element answer to the locator a recording wrote down?

    Two shapes arrive here. A position in the view tree is what recordings
    carried before, and still arrives from every one made then. An attribute
    that names the element is what they carry now, because the position moves
    whenever anything above it does — measured on the Android set, every
    element-addressed replay failed against the same build on the same device
    in the same session, and only the assertions that name no element held.

    Both are understood, so the recordings already on disk keep working.
    """
    if element.xpath == selector:
        return True
    named = _NAMED.match(selector or "")
    if not named:
        return False
    attribute = named.group(1)
    wanted = named.group(2) if named.group(2) is not None else named.group(3)
    if attribute == "resource-id":
        return element.resource_id == wanted
    if attribute == "content-desc":
        return element.name == wanted
    if attribute == "text":
        return element.text == wanted
    return False


def _only_match(elements: List[MobileElement], selector: str) -> Optional[MobileElement]:
    """The one element that answers to this locator, or nothing.

    Nothing when several do: a name that was this element's alone when it was
    recorded can be shared by the time it is replayed — two rows of the same
    list, a screen shown twice — and tapping whichever came first is the blind
    replay this exists to prevent. The caller falls back to matching the
    recorded element itself, which weighs more than one attribute.
    """
    found = [e for e in elements if _answers_to(e, selector)]
    return found[0] if len(found) == 1 else None


async def resolve(
    session_id: str,
    platform_cache: Dict[str, str],
    element_id: Optional[str] = None,
    xpath: Optional[str] = None,
    snapshot_id: Optional[str] = None,
    timeout: float = 10.0,
    poll_interval: float = 0.5,
) -> Optional[ResolvedElement]:
    """
    Wait until the requested element exists, is actionable, and is unambiguous.

    Raises AmbiguousElementError when several candidates match equally well.
    Returns None if the element never becomes actionable within `timeout`.
    """
    source_manager = snapshots.get(session_id, snapshot_id)
    cached: Optional[MobileElement] = None

    if source_manager:
        if element_id and element_id in source_manager.elements_by_id:
            cached = source_manager.elements_by_id[element_id]
        elif xpath:
            cached = _only_match(source_manager.get_all_elements(), xpath)

    target_xpath = xpath or (cached.xpath if cached else None)
    deadline = time.monotonic() + timeout
    pending_ambiguity: Optional[AmbiguousElementError] = None

    while time.monotonic() < deadline:
        manager = await capture_snapshot(session_id, platform_cache)
        if manager is None:
            await asyncio.sleep(poll_interval)
            continue

        match: Optional[MobileElement] = None

        if cached is not None:
            scored = _best_matches(cached, manager)
            if scored and scored[0][0] >= ACCEPTANCE_THRESHOLD:
                best_score, best_elem = scored[0]
                runner_up = scored[1][0] if len(scored) > 1 else float("-inf")
                if best_score - runner_up < AMBIGUITY_MARGIN:
                    # The screen may still be settling, so remember the conflict
                    # and keep polling; only report it if it never resolves.
                    tied = sum(1 for score, _ in scored if best_score - score < AMBIGUITY_MARGIN)
                    pending_ambiguity = AmbiguousElementError(cached.describe(), tied)
                else:
                    pending_ambiguity = None
                    match = best_elem

        # Structural fallback: the locator the caller named, matched exactly.
        if match is None and target_xpath:
            match = _only_match(manager.get_all_elements(), target_xpath)

        if match is not None:
            width, height = manager.screen_width, manager.screen_height
            if match.is_actionable(width, height):
                return ResolvedElement(match, manager, width, height)

            if match.bounds:
                cx, cy = match.bounds["cx"], match.bounds["cy"]
                if cy < 0 or cy > height or cx < 0 or cx > width:
                    if await _auto_scroll(session_id, cy, width, height):
                        await asyncio.sleep(1.0)
                        continue

        await asyncio.sleep(poll_interval)

    if pending_ambiguity is not None:
        raise pending_ambiguity

    return None
