"""A replayed action does not read the screen to find what it already named.

Measured on an iPhone 16 Pro Max, a page source is 2.9 seconds; a replayed
tap was taking 6.9, six of them two readings of the same screen — one at the
top of the loop and one inside the resolver. A recording carries the name of
its element, the device can find a name by itself, and none of the tree is
read on the way. What the device cannot answer falls through to the careful
path, which reads the screen, waits, scrolls and re-matches as it always did.
"""

import asyncio
from unittest.mock import AsyncMock, patch

import appium_client as appium
import locator
from drivers.mobile import MobileTarget

NAMED = '//*[@resource-id="btnContinue"]'
POSITIONAL = "/hierarchy[1]/android.widget.FrameLayout[1]/android.widget.Button[2]"


class Res:
    def __init__(self, value=None, status_code=200):
        self.status_code = status_code
        self._value = value

    def json(self):
        return {"value": self._value}


def _device(monkeypatch, handles, posted=None, displayed=None):
    """A device that answers a find with `handles` and takes every command."""
    calls = posted if posted is not None else []

    async def find_elements(session_id, using, value, timeout=10.0):
        calls.append(("find", using, value))
        return list(handles)

    async def post(path, body=None, **kwargs):
        calls.append(("post", path.rsplit("/", 1)[-1]))
        return Res()

    async def get(path, timeout=None, base_url=None):
        calls.append(("get", path.rsplit("/", 1)[-1]))
        return Res(displayed)

    async def never(*args, **kwargs):
        raise AssertionError("the screen must not be read on this path")

    monkeypatch.setattr(appium, "find_elements", find_elements)
    monkeypatch.setattr(appium, "post", post)
    monkeypatch.setattr(appium, "get", get)
    monkeypatch.setattr(locator, "capture_snapshot", never)
    return calls


def _target():
    return MobileTarget("s1", {"platform": "iOS"}, {})


def test_a_named_tap_goes_straight_to_the_device(monkeypatch):
    calls = _device(monkeypatch, ["el-1"])
    result = asyncio.run(_target().act("click", None, NAMED, None, None))
    assert result.ok, result.message
    assert result.message == 'Clicked "btnContinue"'
    assert calls == [("find", "xpath", NAMED), ("post", "click")]
    # The record still says how the element was addressed, so the recording
    # this run promotes carries the same name.
    assert result.element == {"xpath": NAMED}


def test_typing_by_name_empties_the_field_first(monkeypatch):
    calls = _device(monkeypatch, ["el-1"])
    result = asyncio.run(_target().act("type", None, NAMED, "Ergün", None))
    assert result.ok
    assert [c for c in calls if c[0] == "post"] == [("post", "clear"), ("post", "value")]


def test_a_name_two_elements_now_share_is_not_acted_on(monkeypatch):
    """The name was this element's alone when it was written down. Tapping
    whichever the device returned first is the blind replay the careful path
    exists to prevent, so it hands over rather than guessing."""
    calls = _device(monkeypatch, ["el-1", "el-2"])
    careful = AsyncMock(return_value=None)
    with patch.object(locator, "resolve", new=careful):
        result = asyncio.run(_target().act("click", None, NAMED, None, None))
    assert not result.ok
    assert calls == [("find", "xpath", NAMED)], "nothing was tapped"
    careful.assert_awaited()


def test_an_element_the_device_cannot_find_falls_through(monkeypatch):
    """Not there yet is the case the careful path waits for."""
    _device(monkeypatch, [])
    careful = AsyncMock(return_value=None)
    with patch.object(locator, "resolve", new=careful):
        result = asyncio.run(_target().act("click", None, NAMED, None, None))
    assert not result.ok
    careful.assert_awaited()


def test_a_positional_recording_still_goes_the_careful_way(monkeypatch):
    """A position has to be checked against the screen before it is trusted."""
    async def never_find(*args, **kwargs):
        raise AssertionError("a position is not a name")

    monkeypatch.setattr(appium, "find_elements", never_find)
    careful = AsyncMock(return_value=None)
    with patch.object(locator, "resolve", new=careful):
        asyncio.run(_target().act("click", None, POSITIONAL, None, None))
    careful.assert_awaited()


def test_the_model_addressing_what_it_saw_goes_the_careful_way(monkeypatch):
    """An elementId means the model chose it out of its own snapshot, and that
    snapshot is where it has to be resolved."""
    async def never_find(*args, **kwargs):
        raise AssertionError("the model's element is not addressed by name")

    monkeypatch.setattr(appium, "find_elements", never_find)
    careful = AsyncMock(return_value=None)
    with patch.object(locator, "resolve", new=careful):
        asyncio.run(_target().act("click", "el_9", NAMED, None, "snap-1"))
    careful.assert_awaited()


def test_a_wheel_is_never_typed_into_by_name(monkeypatch):
    """A picker wheel is turned, not set — see _spin_wheel."""
    wheel = '//*[@resource-id="numberpicker_input"]'

    async def never_find(*args, **kwargs):
        raise AssertionError("a wheel has its own way of being typed into")

    monkeypatch.setattr(appium, "find_elements", never_find)
    careful = AsyncMock(return_value=None)
    with patch.object(locator, "resolve", new=careful):
        asyncio.run(_target().act("type", None, wheel.replace("numberpicker_input",
                                                              "NumberPicker"), "2000", None))
    careful.assert_awaited()


def test_visibility_is_asked_of_the_device(monkeypatch):
    calls = _device(monkeypatch, ["el-1"], displayed=True)
    result = asyncio.run(_target().act("assert_visible", None, NAMED, None, None))
    assert result.ok
    assert result.message == '"btnContinue" is visible'
    assert ("get", "displayed") in calls


def test_something_found_but_not_shown_is_waited_for(monkeypatch):
    """On the screen and not painted yet is exactly what the careful path's
    polling is for, so it is handed over rather than called a failure."""
    _device(monkeypatch, ["el-1"], displayed=False)
    careful = AsyncMock(return_value=None)
    with patch.object(locator, "resolve", new=careful):
        asyncio.run(_target().act("assert_visible", None, NAMED, None, None))
    careful.assert_awaited()
