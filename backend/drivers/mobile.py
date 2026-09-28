"""Appium-backed target. Wraps the existing device code behind UITarget."""

import asyncio
import re
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
# How long a wheel is given to settle before it is read a second time.
WHEEL_SETTLE = 0.4

# How a month wheel spells its months, in the two languages the app is used
# in and in both the short and the long form. Matched whole rather than by
# prefix: "Mar" is March and "Marmaris" is an airport, and a wheel of airports
# taken for a wheel of months is a value typed into the wrong one.
_MONTHS = (
    ("jan", "january", "oca", "ocak"),
    ("feb", "february", "şub", "sub", "şubat", "subat"),
    ("mar", "march", "mart"),
    ("apr", "april", "nis", "nisan"),
    ("may", "mayıs", "mayis"),
    ("jun", "june", "haz", "haziran"),
    ("jul", "july", "tem", "temmuz"),
    ("aug", "august", "ağu", "agu", "ağustos", "agustos"),
    ("sep", "sept", "september", "eyl", "eylül", "eylul"),
    ("oct", "october", "eki", "ekim"),
    ("nov", "november", "kas", "kasım", "kasim"),
    ("dec", "december", "ara", "aralık", "aralik"),
)


def _notch(text: str):
    """What a wheel value means: a number, a month, or just its words.

    Punctuation around it is not part of it: the iOS day wheel reads "5.",
    and "5." taken as a word has no direction to "1."."""
    folded = " ".join(str(text or "").split()).casefold().strip(".,;:")
    if folded.isdigit():
        return ("number", int(folded))
    for index, names in enumerate(_MONTHS):
        if folded in names:
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


def _holds(text: str) -> str:
    """What kind of thing a wheel is showing: a year, a month, a plain number
    or words.

    Which wheel is which is asked of what they hold, because where they sit
    moves. On the iPhone the month wheel was recorded as the first of three
    and came back as the second on the next run, and the path from the root
    moves with every container above it — so the recording pointed the month
    at the day's wheel and the replay found nothing at all. A day is a short
    number, a year is four digits, a month says its name.
    """
    if not str(text or "").strip():
        # A wheel the device would not answer for. Kept distinct so it can
        # never be mistaken for the one a value was meant for.
        return "nothing"
    kind, _ = _notch(text)
    if kind == "number":
        digits = "".join(ch for ch in str(text) if ch.isdigit())
        return "year" if len(digits) == 4 else "day"
    return kind


def _looks_like_a_wheel(selector: str) -> bool:
    """Does this locator address a picker wheel?"""
    selector = selector or ""
    return "PickerWheel" in selector or "NumberPicker" in selector


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
            size = await locator.screen_size(self.session_id)
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

        # A picker wheel is found by what it holds, straight on the device —
        # see _set_wheel. A recording cannot address one by position: the
        # path it wrote down runs through every container above the wheel,
        # and on the next run it reaches nothing. Three date-of-birth steps
        # were being re-derived every single time for that.
        asked_the_wheels = False
        if kind == "type" and _looks_like_a_wheel(selector or ""):
            turned = await self._set_wheel(value)
            if turned is not None:
                ok, message = turned
                return ActionResult(ok, message, {"xpath": selector})
            # Asked and answered: the careful path below must not ask again,
            # which on a three-wheel picker is another seven round trips.
            asked_the_wheels = True

        # A recording addresses its element by name, and the device can find a
        # name on its own. Going through a snapshot for one costs a full page
        # source — 2.9 seconds on an iPhone 16 Pro Max — to build a tree that
        # nothing on this path then reads: measured on the booking scenario, a
        # replayed tap took 6.9 seconds, six of them two readings of the same
        # screen. Only a recording, which carries a selector and no elementId,
        # comes through here; the model addresses what it saw in its own
        # snapshot and goes the careful way below.
        if element_id is None and locator.names_one_element(selector or ""):
            straight = await self._act_by_name(kind, selector, value)
            if straight is not None:
                return straight

        # Twice at most: the screen the caller already read, then one taken
        # now. What fails on a remembered screen is tried again on a real one
        # rather than acted on blind — see _interact.
        message, info = "", None
        for attempt in (0, 1):
            try:
                resolved = await locator.resolve(
                    self.session_id, self._platform_cache,
                    element_id=element_id, xpath=selector, snapshot_id=snapshot_id,
                    allow_cached=attempt == 0,
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

            ok, message = await self._interact(
                kind, element, value, resolved.fresh, asked_the_wheels)
            if ok or resolved.fresh:
                return ActionResult(ok, message, info)

        return ActionResult(False, message, info)

    async def _act_by_name(self, kind: str, selector: str,
                           value: Optional[str]) -> Optional[ActionResult]:
        """Carry the action out against the element the device finds by name.

        Returns None when that cannot be done — the name reaches more than one
        element now, the driver refused, or this is an action with its own way
        of being performed — and the caller takes the careful path instead.
        """
        # A picker wheel is typed into by turning it, not by setting a value.
        if kind == "type" and ("PickerWheel" in selector or "NumberPicker" in selector):
            return None

        handles = await appium.find_elements(self.session_id, "xpath", selector)
        if len(handles) != 1:
            # None: gone, or not there yet — the careful path waits for it.
            # Several: the name stopped being this element's alone, which is
            # exactly the case that must not be acted on blind.
            return None

        handle = handles[0]
        label = locator.named_value(selector) or selector
        # Thin on purpose: no tree was read, so there is nothing to describe
        # beyond how the element was addressed. The run's own record falls
        # back to the name the recording carries.
        info = {"xpath": selector}

        if kind == "assert_visible":
            res = await appium.get(f"/session/{self.session_id}/element/{handle}/displayed")
            if res is None or res.status_code != 200:
                return None
            if res.json().get("value") is True:
                return ActionResult(True, f'"{label}" is visible', info)
            # On the screen and not shown yet: let the careful path wait.
            return None

        if kind == "click":
            res = await appium.post(f"/session/{self.session_id}/element/{handle}/click")
            if res is not None and res.status_code == 200:
                return ActionResult(True, f'Clicked "{label}"', info)
            return None

        if kind == "type":
            await appium.post(f"/session/{self.session_id}/element/{handle}/clear")
            res = await appium.post(
                f"/session/{self.session_id}/element/{handle}/value",
                {"text": value or "", "value": list(value or "")},
            )
            if res is not None and res.status_code == 200:
                return ActionResult(True, f'Typed "{value}" into "{label}"', info)
            return None

        if kind == "clear":
            res = await appium.post(f"/session/{self.session_id}/element/{handle}/clear")
            if res is not None and res.status_code == 200:
                return ActionResult(True, f'Cleared "{label}"', info)
            return None

        return None

    async def _interact(self, kind: str, element, value: Optional[str],
                        fresh: bool = True, asked_the_wheels: bool = False):
        """W3C interaction with a coordinate fallback.

        The fallback taps where the element was, which is only safe on a
        screen that was read just now: `fresh` off means the element came
        from the reading the caller already had, and a tap at coordinates it
        gave would land wherever the screen has since moved to. Refused, so
        the caller resolves again against a screen taken now.
        """
        label = element.describe()
        if kind == "type" and _looks_like_a_wheel(element.xpath or ""):
            # Which wheel this is, asked of what the wheels hold. The model
            # picked one out of the tree and can pick the wrong one: a
            # recording promoted from a green run sent the month to the day's
            # wheel, because that is where the model had put it.
            turned = None if asked_the_wheels else await self._set_wheel(value)
            if turned is not None:
                return turned
            # Neither path below can be trusted on a remembered screen: one
            # taps where the wheel was, and the other is handed the value the
            # wheel was showing then — which, if it already matches, reports
            # the wheel set without the device being touched at all.
            if not fresh:
                return False, (f'"{label}" could not be reached on the screen '
                               "that was already in hand")
            if "NumberPicker" in (element.xpath or ""):
                return await self._set_number_picker(element, value, label)
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

        if not fresh:
            return False, (f'"{label}" could not be reached on the screen that was '
                           "already in hand")
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
        # Emptied first, because typing appends: "2014" and then "2000" made
        # "20142000", which the picker threw out and stayed on 2014 — three
        # wheels sent, none of them taken. The one sequence that did take on
        # the first run had a clear in front of it.
        handle = await appium.find_element_by_xpath(self.session_id, element.xpath)
        if handle:
            await appium.post(f"/session/{self.session_id}/element/{handle}/clear")
        cx, cy = bounds["cx"], bounds["cy"]
        if not await MobileGestureController.perform_tap(self.session_id, cx, cy):
            return False, f'Could not focus "{label}"'
        await asyncio.sleep(0.4)
        if not await MobileGestureController.perform_type_text(self.session_id, value or ""):
            return False, f'Could not type into "{label}"'
        await MobileGestureController.perform_key_event(self.session_id, "android", "tab")
        return True, f'Set "{label}" to "{value}"'

    async def _set_wheel(self, value: Optional[str]):
        """Set the wheel that holds this kind of value, whichever one it is.

        A date picker's wheels are told apart by what they show — a day is a
        short number, a month says its name, a year is four digits — because
        where they sit is not dependable. Measured on the iPhone: the month
        wheel was the first of three when the recording was made and the
        second when it was replayed, and the full path to it runs through
        containers that move on their own. Asked of the device, so no screen
        is read for it either.

        Returns (ok, message), or None when the wheels cannot be told apart
        this way and the caller should address the one it was given.
        """
        platform = (await appium.get_platform(self.session_id, self._platform_cache)).lower()
        wheels = await self._wheels_on_screen(platform)
        if not wheels:
            return None
        wanted = " ".join(str(value or "").split())
        matching = [wheel for wheel in wheels if _holds(wheel[1]) == _holds(wanted)]
        if len(matching) != 1:
            # Two wheels showing the same kind of thing, or none: nothing here
            # says which one was meant.
            return None
        handle, current = matching[0]
        name = f"the {_holds(wanted)} wheel"
        if platform == "ios":
            return await self._turn_wheel(handle, current, wanted, name)
        return await self._type_into_wheel(handle, wanted, name)

    async def _wheels_on_screen(self, platform: str):
        """Every picker wheel the device can see, with what it is showing."""
        if platform == "ios":
            handles = await appium.find_elements(
                self.session_id, "-ios class chain", "**/XCUIElementTypePickerWheel")
        else:
            handles = await appium.find_elements(
                self.session_id, "xpath",
                "//android.widget.NumberPicker//android.widget.EditText")
        return [(handle, await self._wheel_value(handle) or "") for handle in handles]

    async def _type_into_wheel(self, handle: str, wanted: str, name: str):
        """The Android way: a real tap focuses the wheel's field, the value
        goes in, and TAB is what makes the picker read it. See
        _set_number_picker, which does the same from a snapshot's element."""
        res = await appium.get(f"/session/{self.session_id}/element/{handle}/rect")
        if res is None or res.status_code != 200:
            return False, f'Could not find where {name} is'
        box = res.json().get("value") or {}
        cx = int(box.get("x", 0)) + int(box.get("width", 0)) // 2
        cy = int(box.get("y", 0)) + int(box.get("height", 0)) // 2
        await appium.post(f"/session/{self.session_id}/element/{handle}/clear")
        if not await MobileGestureController.perform_tap(self.session_id, cx, cy):
            return False, f"Could not focus {name}"
        await asyncio.sleep(0.4)
        if not await MobileGestureController.perform_type_text(self.session_id, wanted):
            return False, f"Could not type into {name}"
        await MobileGestureController.perform_key_event(self.session_id, "android", "tab")
        # Read back, because a picker takes a value or ignores it and says
        # nothing either way. Reporting the gestures as the outcome is how a
        # step goes green on a date nobody set.
        settled = await self._wheel_value(handle)
        if settled is not None and not _same_notch(settled, wanted):
            return False, f'{name} still reads "{settled}", not "{wanted}"'
        return True, f'Set {name} to "{wanted}"'

    async def _spin_wheel(self, element, value: Optional[str], label: str):
        """Turn an iOS picker wheel, addressed by the element it was found as."""
        handle = await self._find_wheel(element)
        if not handle:
            return False, f'Could not find "{label}"'
        return await self._turn_wheel(
            handle, " ".join(str(element.text or "").split()),
            " ".join(str(value or "").split()), label)

    async def _turn_wheel(self, handle: str, current: str, wanted: str, label: str):
        """Turn an iOS picker wheel to `wanted`, one notch at a time.

        Typing into an XCUIElementTypePickerWheel comes back 200 and leaves
        the wheel where it was. Measured on the passenger form's date of
        birth: "2000" sent to the year wheel three times, the wheel reading
        1998 and then 2002, and the step gone after twenty-four actions.
        `mobile: selectPickerWheelValue` moves a wheel one notch in a known
        direction, so the wheel is read, turned toward the value and read
        again until it says so — a day, a month or a year knows which way
        that is; anything else is tried forwards.
        """
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
            latest = await self._wheel_value(handle)
            if latest == current:
                # Read too soon: the wheel was still settling and answered
                # with the value it was leaving. Asked once more before
                # concluding it did not move — its end, or a notch that is
                # not on it.
                await asyncio.sleep(WHEEL_SETTLE)
                latest = await self._wheel_value(handle)
            if latest is None:
                return False, f'Could not read "{label}" after turning it'
            if latest == current:
                return False, f'"{label}" stops at "{current}"; "{wanted}" is not on it'
            current = latest
        return False, f'"{label}" reads "{current}" after {WHEEL_TURNS} turns, not "{wanted}"'

    async def _wheel_value(self, handle: str) -> Optional[str]:
        """What a wheel is showing. An iPhone answers on `value`, an Android
        text field on either — asked in that order, and the first that says
        anything wins."""
        for path in ("attribute/value", "text"):
            res = await appium.get(f"/session/{self.session_id}/element/{handle}/{path}")
            if res is not None and res.status_code == 200:
                said = " ".join(str(res.json().get("value") or "").split())
                if said:
                    return said
        return None

    async def _find_wheel(self, element) -> Optional[str]:
        """The wheel's handle, by which wheel it is rather than where it sits.

        An XPath over an iOS screen with a picker open took longer than the
        ten seconds allowed and the wheel was "not found" — and on the run
        before, the same lookup timing out sent the typing to the coordinate
        fallback, which taps a wheel and types into nothing. A class chain is
        answered natively: the k-th picker wheel on the screen, where k is
        the wheel's own index at the end of its path.
        """
        match = re.search(r"XCUIElementTypePickerWheel\[(\d+)\]$", element.xpath or "")
        if match:
            handle = await appium.find_element(
                self.session_id, "-ios class chain",
                f"**/XCUIElementTypePickerWheel[{match.group(1)}]", timeout=20.0)
            if handle:
                return handle
        return await appium.find_element(self.session_id, "xpath", element.xpath, timeout=20.0)

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
