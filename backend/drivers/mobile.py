"""Appium-backed target. Wraps the existing device code behind UITarget."""

import asyncio
from typing import Any, Dict, Optional

import appium_client as appium
import locator
from gesture_controller import MobileGestureController

from .base import ActionResult, Snapshot

# How far a picker wheel is turned in one go — a fraction of its height, small
# enough to move exactly one notch — and how many notches it may be turned
# before giving up: a month wheel is at most eleven away, a year wheel on a
# date of birth some tens.
WHEEL_NOTCH = 0.15
WHEEL_TURNS = 60

# The order a month wheel keeps, in the two languages the app is used in.
_MONTHS = (
    ("jan", "oca"), ("feb", "şub"), ("mar", "mar"), ("apr", "nis"), ("may", "may"),
    ("jun", "haz"), ("jul", "tem"), ("aug", "ağu"), ("sep", "eyl"), ("oct", "eki"),
    ("nov", "kas"), ("dec", "ara"),
)


def _notch(text: str):
    """What a wheel value means: a number, a month, or just its words."""
    folded = " ".join(str(text or "").split()).casefold()
    if folded.isdigit():
        return ("number", int(folded))
    for index, names in enumerate(_MONTHS):
        if any(folded.startswith(name) for name in names):
            return ("month", index)
    return ("words", folded)


def _same_notch(a: str, b: str) -> bool:
    return _notch(a) == _notch(b)


def _comes_after(wanted: str, current: str) -> bool:
    """Is `wanted` further down the wheel than `current`? Forwards when the
    two cannot be compared — a wheel of words is tried in its own order."""
    kind, at = _notch(wanted)
    other_kind, now = _notch(current)
    if kind == other_kind and kind in ("number", "month"):
        return at > now
    return True


class MobileTarget:
    kind = "mobile"

    def __init__(self, session_id: str, device: Dict[str, Any], platform_cache: Dict[str, str]):
        self.session_id = session_id
        self.device = device or {}
        self._platform_cache = platform_cache

    # --- reading --------------------------------------------------------- #

    async def snapshot(self) -> Optional[Snapshot]:
        return await locator.capture_snapshot(self.session_id, self._platform_cache)

    async def screenshot(self) -> Optional[str]:
        return await appium.get_screenshot(self.session_id)

    async def element_at(self, x: int, y: int) -> Optional[Dict[str, Any]]:
        manager = locator.snapshots.latest(self.session_id) or await self.snapshot()
        if manager is None:
            return None

        best, best_area = None, None
        for element in manager.get_all_elements():
            bounds = element.bounds
            if not bounds or not (element.displayed and element.visible):
                continue
            if bounds["x1"] <= x <= bounds["x2"] and bounds["y1"] <= y <= bounds["y2"]:
                area = max(bounds["width"], 1) * max(bounds["height"], 1)
                if best_area is None or area < best_area:
                    best, best_area = element, area

        if best is None:
            return None
        payload = best.to_dict(include_children=False)
        payload["elementId"] = best.element_id
        return payload

    # --- acting ---------------------------------------------------------- #

    async def scroll(self, direction: str, element_id: Optional[str] = None) -> ActionResult:
        origin_x = origin_y = 0
        if element_id:
            resolved = await locator.resolve(self.session_id, self._platform_cache, element_id=element_id)
            if resolved is None:
                return ActionResult(False, f"Could not find the scroll container '{element_id}'")
            bounds = resolved.element.bounds
            if not bounds:
                return ActionResult(False, f'"{resolved.element.describe()}" has no bounds to scroll within')
            origin_x, origin_y = bounds["x1"], bounds["y1"]
            width, height = max(bounds["width"], 1), max(bounds["height"], 1)
        else:
            size = await appium.get_window_size(self.session_id)
            width, height = size["width"], size["height"]

        ok = await MobileGestureController.perform_scroll(
            self.session_id, (direction or "down").lower(), width, height, origin_x, origin_y
        )
        return ActionResult(ok, f"Scrolled {direction}" if ok else f"Scroll {direction} failed")

    async def press_key(self, key: str) -> ActionResult:
        platform = await appium.get_platform(self.session_id, self._platform_cache)
        ok = await MobileGestureController.perform_key_event(self.session_id, platform, key)
        return ActionResult(
            ok,
            f"Pressed {key}" if ok else f"Key '{key}' is not supported on {platform}",
        )

    async def act(
        self,
        kind: str,
        element_id: Optional[str],
        selector: Optional[str],
        value: Optional[str],
        snapshot_id: Optional[str],
    ) -> ActionResult:
        if not element_id and not selector:
            return ActionResult(False, f"Action '{kind}' needs an elementId")

        try:
            resolved = await locator.resolve(
                self.session_id, self._platform_cache,
                element_id=element_id, xpath=selector, snapshot_id=snapshot_id,
            )
        except locator.AmbiguousElementError as exc:
            return ActionResult(False, str(exc))

        if resolved is None:
            return ActionResult(
                False,
                f"Element {element_id or selector} never became actionable within 10s",
            )

        element = resolved.element
        info = {
            "id": element.resource_id,
            "text": element.text,
            "content-desc": element.name,
            "role": element.role,
            # The locator that goes into the recording: what the element is
            # where the screen says so, its position otherwise. Recording the
            # position meant every replayed tap on a phone failed — the path
            # from the root moves when anything above it does.
            "xpath": getattr(element, "selector", None) or element.xpath,
            "bounds": element.bounds,
            "label": element.describe(),
        }

        if kind == "assert_visible":
            return ActionResult(True, f'"{element.describe()}" is visible', info)

        ok, message = await self._interact(kind, element, value)
        return ActionResult(ok, message, info)

    async def _interact(self, kind: str, element, value: Optional[str]):
        """W3C interaction with a coordinate fallback."""
        label = element.describe()
        if kind == "type" and "NumberPicker" in (element.xpath or ""):
            return await self._set_number_picker(element, value, label)
        if kind == "type" and "XCUIElementTypePickerWheel" in (element.xpath or ""):
            return await self._spin_wheel(element, value, label)
        handle = await appium.find_element_by_xpath(self.session_id, element.xpath)

        if handle:
            if kind == "click":
                res = await appium.post(f"/session/{self.session_id}/element/{handle}/click")
                if res is not None and res.status_code == 200:
                    return True, f'Clicked "{label}"'
            elif kind == "type":
                await appium.post(f"/session/{self.session_id}/element/{handle}/clear")
                res = await appium.post(
                    f"/session/{self.session_id}/element/{handle}/value",
                    {"text": value or "", "value": list(value or "")},
                )
                if res is not None and res.status_code == 200:
                    return True, f'Typed "{value}" into "{label}"'
            elif kind == "clear":
                res = await appium.post(f"/session/{self.session_id}/element/{handle}/clear")
                if res is not None and res.status_code == 200:
                    return True, f'Cleared "{label}"'
            else:
                return False, f"Unsupported action: {kind}"

        if not element.bounds:
            return False, f'W3C interaction failed and "{label}" has no bounds to tap'

        cx, cy = element.bounds["cx"], element.bounds["cy"]
        if kind == "click":
            ok = await MobileGestureController.perform_tap(self.session_id, cx, cy)
            return ok, (f'Clicked "{label}" at ({cx}, {cy}) via coordinates' if ok else f'Could not click "{label}"')

        if kind in ("type", "clear"):
            if not await MobileGestureController.perform_tap(self.session_id, cx, cy):
                return False, f'Could not focus "{label}"'
            await asyncio.sleep(0.4)
            if kind == "clear":
                active = await appium.get_active_element(self.session_id)
                if active:
                    res = await appium.post(f"/session/{self.session_id}/element/{active}/clear")
                    if res is not None and res.status_code == 200:
                        return True, f'Cleared "{label}"'
                return False, f'Could not clear "{label}"'
            ok = await MobileGestureController.perform_type_text(self.session_id, value or "")
            return ok, (f'Typed "{value}" into "{label}"' if ok else f'Could not type into "{label}"')

        return False, f"Unsupported action: {kind}"

    async def _set_number_picker(self, element, value: Optional[str], label: str):
        """Type a value into one wheel of an Android date picker.

        A NumberPicker reads its text field only when the field loses focus.
        Measured on the passenger form's date of birth: W3C setValue put
        "2000" into the year field, Done was pressed, and the picker stayed
        on 2014 — four times over, twenty-four actions, the step lost.
        setValue writes the text without ever focusing the field, so nothing
        told the picker to read it.

        Done the way a thumb does it: a real tap on the wheel focuses the
        field (and opens it for typing), the value goes in, and TAB moves
        focus on — which is the moment the picker takes the value.
        """
        bounds = element.bounds
        if not bounds:
            return False, f'"{label}" has no bounds to tap'
        cx, cy = bounds["cx"], bounds["cy"]
        if not await MobileGestureController.perform_tap(self.session_id, cx, cy):
            return False, f'Could not focus "{label}"'
        await asyncio.sleep(0.4)
        if not await MobileGestureController.perform_type_text(self.session_id, value or ""):
            return False, f'Could not type into "{label}"'
        await MobileGestureController.perform_key_event(self.session_id, "android", "tab")
        return True, f'Set "{label}" to "{value}"'

    async def _spin_wheel(self, element, value: Optional[str], label: str):
        """Turn an iOS picker wheel to `value`, one notch at a time.

        Typing into an XCUIElementTypePickerWheel comes back 200 and leaves
        the wheel where it was. Measured on the passenger form's date of
        birth: "2000" sent to the year wheel three times, the wheel reading
        1998 and then 2002, and the step gone after twenty-four actions.
        `mobile: selectPickerWheelValue` moves a wheel one notch in a known
        direction, so the wheel is read, turned toward the value and read
        again until it says so — a day, a month or a year knows which way
        that is; anything else is tried forwards.
        """
        handle = await appium.find_element_by_xpath(self.session_id, element.xpath)
        if not handle:
            return False, f'Could not find "{label}"'
        wanted = " ".join(str(value or "").split())
        current = " ".join(str(element.text or "").split())
        for _ in range(WHEEL_TURNS):
            if _same_notch(current, wanted):
                return True, f'Turned "{label}" to "{current}"'
            order = "next" if _comes_after(wanted, current) else "previous"
            ok, _ = await appium.execute(
                self.session_id, "mobile: selectPickerWheelValue",
                {"element": handle, "order": order, "offset": WHEEL_NOTCH},
            )
            if not ok:
                return False, f'Could not turn "{label}"'
            res = await appium.get(
                f"/session/{self.session_id}/element/{handle}/attribute/value")
            if res is None or res.status_code != 200:
                return False, f'Could not read "{label}" after turning it'
            latest = " ".join(str(res.json().get("value") or "").split())
            if latest == current:
                # The wheel did not move: its end, or "{wanted}" is not on it.
                return False, f'"{label}" stops at "{current}"; "{wanted}" is not on it'
            current = latest
        return False, f'"{label}" reads "{current}" after {WHEEL_TURNS} turns, not "{wanted}"'

    # --- lifecycle ------------------------------------------------------- #

    def is_alive(self) -> bool:
        """Appium sessions die when the device unplugs or the server restarts.
        Checked lazily: a screenshot failing is the signal that matters."""
        return True

    def describe(self) -> Dict[str, Any]:
        return {
            "kind": "mobile",
            "platform": self.device.get("platform"),
            "name": self.device.get("name"),
            "udid": self.device.get("udid"),
            "appId": self.device.get("appId"),
            # What the app is called on the device. On a cloud session `appId`
            # is an upload handle ("bs://…") that names nothing there, so
            # anything that has to address the app — terminate it, relaunch it
            # — needs this one instead.
            "bundleId": self.device.get("bundleId"),
        }

    async def close(self) -> None:
        locator.snapshots.clear(self.session_id)
        await appium.delete_session(self.session_id)
