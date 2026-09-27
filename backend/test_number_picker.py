"""Typing into one wheel of an Android date picker.

A NumberPicker reads its text field only when the field loses focus, and W3C
setValue writes the text without ever focusing it. Measured on the passenger
form: "2000" went into the year field four times, Done was pressed four
times, the picker stayed on 2014, and the step lost all twenty-four of its
actions to it. So a wheel is typed into the way a thumb does it — tap, type,
move on — and never through the element endpoint.
"""

import asyncio

import appium_client as appium
from drivers.mobile import MobileTarget
from gesture_controller import ANDROID_KEYCODES, MobileGestureController


class YearWheel:
    xpath = ("/hierarchy[1]/android.widget.FrameLayout[1]"
             "/android.widget.NumberPicker[3]/android.widget.EditText[1]")
    bounds = {"x1": 0, "y1": 0, "x2": 100, "y2": 40, "width": 100,
              "height": 40, "cx": 50, "cy": 20}

    def describe(self):
        return "year wheel"


class PlainField(YearWheel):
    xpath = "/hierarchy[1]/android.widget.FrameLayout[1]/android.widget.EditText[1]"


def _thumb(monkeypatch):
    done = []

    async def tap(session_id, x, y):
        done.append(("tap", x, y))
        return True

    async def typed(session_id, text):
        done.append(("type", text))
        return True

    async def key(session_id, platform, name):
        done.append(("key", platform, name))
        return True

    async def never(session_id, xpath):
        raise AssertionError("a wheel must not be typed into through the element endpoint")

    monkeypatch.setattr(MobileGestureController, "perform_tap", tap)
    monkeypatch.setattr(MobileGestureController, "perform_type_text", typed)
    monkeypatch.setattr(MobileGestureController, "perform_key_event", key)
    monkeypatch.setattr(appium, "find_element_by_xpath", never)
    return done


def test_a_wheel_is_tapped_typed_into_and_left(monkeypatch):
    done = _thumb(monkeypatch)
    target = MobileTarget("s1", {"platform": "Android"}, {})

    ok, message = asyncio.run(target._interact("type", YearWheel(), "2000"))

    assert ok, message
    assert done == [("tap", 50, 20), ("type", "2000"), ("key", "android", "tab")]


def test_tab_is_a_key_the_phone_knows():
    assert ANDROID_KEYCODES["tab"] == 61


def test_a_field_that_is_not_a_wheel_still_goes_through_the_element(monkeypatch):
    """The ordinary path is untouched: the check is on the wheel, not on
    every field of the form."""
    done = _thumb(monkeypatch)

    async def found(session_id, xpath):
        return "el-1"

    posted = []

    async def post(path, body=None, **kwargs):
        posted.append(path.rsplit("/", 1)[-1])

        class Res:
            status_code = 200
        return Res()

    monkeypatch.setattr(appium, "find_element_by_xpath", found)
    monkeypatch.setattr(appium, "post", post)
    target = MobileTarget("s1", {"platform": "Android"}, {})

    ok, _ = asyncio.run(target._interact("type", PlainField(), "Ergün"))

    assert ok
    assert posted == ["clear", "value"]
    assert done == []


def test_a_wheel_with_no_bounds_says_so(monkeypatch):
    _thumb(monkeypatch)

    class Ghost(YearWheel):
        bounds = None

    target = MobileTarget("s1", {"platform": "Android"}, {})
    ok, message = asyncio.run(target._interact("type", Ghost(), "2000"))
    assert not ok
    assert "no bounds" in message
