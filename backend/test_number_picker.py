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

    async def found(session_id, xpath):
        return "el-1"

    async def post(path, body=None, **kwargs):
        # The only element call a wheel gets is the clear: typing appends,
        # and "2014" then "2000" made "20142000", which the picker threw out.
        assert path.endswith("/element/el-1/clear"), path
        done.append(("clear",))

        class Res:
            status_code = 200
        return Res()

    monkeypatch.setattr(MobileGestureController, "perform_tap", tap)
    monkeypatch.setattr(MobileGestureController, "perform_type_text", typed)
    monkeypatch.setattr(MobileGestureController, "perform_key_event", key)
    monkeypatch.setattr(appium, "find_element_by_xpath", found)
    monkeypatch.setattr(appium, "post", post)
    return done


def test_a_wheel_is_cleared_tapped_typed_into_and_left(monkeypatch):
    done = _thumb(monkeypatch)
    target = MobileTarget("s1", {"platform": "Android"}, {})

    ok, message = asyncio.run(target._interact("type", YearWheel(), "2000"))

    assert ok, message
    assert done == [("clear",), ("tap", 50, 20), ("type", "2000"), ("key", "android", "tab")]


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
    assert done == [], "no tap, no key: the ordinary path went through the element"


def test_a_wheel_with_no_bounds_says_so(monkeypatch):
    _thumb(monkeypatch)

    class Ghost(YearWheel):
        bounds = None

    target = MobileTarget("s1", {"platform": "Android"}, {})
    ok, message = asyncio.run(target._interact("type", Ghost(), "2000"))
    assert not ok
    assert "no bounds" in message


# --- the iOS wheel ---------------------------------------------------------- #

class IosWheel:
    """An XCUIElementTypePickerWheel as the snapshot shows it: its text is
    the value it is on."""
    xpath = ("/AppiumAUT[1]/XCUIElementTypeApplication[1]/XCUIElementTypeWindow[1]"
             "/XCUIElementTypePicker[1]/XCUIElementTypePickerWheel[3]")
    bounds = {"x1": 0, "y1": 0, "x2": 100, "y2": 200, "width": 100,
              "height": 200, "cx": 50, "cy": 100}

    def __init__(self, text):
        self.text = text

    def describe(self):
        return "year wheel"


def _turning(monkeypatch, notches, start, stale=False):
    """A wheel that really turns: `notches` in order, `start` where it is.
    `stale` makes the first read after every turn answer with the value the
    wheel is leaving, as a wheel still settling does."""
    state = {"at": notches.index(start), "orders": [], "shown": notches.index(start)}

    found_by = []

    async def found(session_id, using, value, timeout=None):
        # By which wheel it is, not where it sits: an XPath over a screen
        # with a picker open timed out and the wheel was "not found".
        found_by.append((using, value))
        assert using == "-ios class chain"
        assert value == "**/XCUIElementTypePickerWheel[3]"
        return "wheel-1"

    async def never_xpath(session_id, xpath):
        raise AssertionError("the wheel must not be looked up by its position")

    async def execute(session_id, script, args=None):
        assert script == "mobile: selectPickerWheelValue"
        assert args["element"] == "wheel-1"
        state["orders"].append(args["order"])
        step = 1 if args["order"] == "next" else -1
        state["shown"] = state["at"] if stale else None
        state["at"] = min(max(state["at"] + step, 0), len(notches) - 1)
        return True, None

    class Res:
        status_code = 200

        def json(self):
            # The value the wheel is leaving, once, when it is still settling.
            if state["shown"] is not None:
                shown, state["shown"] = state["shown"], None
                return {"value": notches[shown]}
            return {"value": notches[state["at"]]}

    async def get(path, timeout=None, base_url=None):
        assert path.endswith("/element/wheel-1/attribute/value")
        return Res()

    async def never(*args, **kwargs):
        raise AssertionError("a wheel is not typed into through the element endpoint")

    monkeypatch.setattr(appium, "find_element", found)
    monkeypatch.setattr(appium, "find_element_by_xpath", never_xpath)
    monkeypatch.setattr(appium, "execute", execute)
    monkeypatch.setattr(appium, "get", get)
    monkeypatch.setattr(appium, "post", never)
    return state


def test_a_year_wheel_is_turned_up_to_the_year(monkeypatch):
    state = _turning(monkeypatch, [str(y) for y in range(1990, 2011)], "1998")
    target = MobileTarget("s1", {"platform": "iOS"}, {})

    ok, message = asyncio.run(target._interact("type", IosWheel("1998"), "2000"))

    assert ok, message
    assert state["orders"] == ["next", "next"]
    assert message == 'Turned "year wheel" to "2000"'


def test_a_month_wheel_knows_which_way_january_is(monkeypatch):
    months = ["January", "February", "March", "April", "May", "June", "July",
              "August", "September", "October", "November", "December"]
    state = _turning(monkeypatch, months, "October")
    target = MobileTarget("s1", {"platform": "iOS"}, {})

    ok, _ = asyncio.run(target._interact("type", IosWheel("October"), "Jan"))

    assert ok
    assert state["orders"] == ["previous"] * 9


def test_a_day_wheel_already_there_is_left_alone(monkeypatch):
    state = _turning(monkeypatch, [str(d) for d in range(1, 32)], "1")
    target = MobileTarget("s1", {"platform": "iOS"}, {})

    ok, _ = asyncio.run(target._interact("type", IosWheel("1"), "01"))

    assert ok
    assert state["orders"] == []


def test_a_day_wheel_with_a_full_stop_is_still_counted(monkeypatch):
    """The iOS day wheel reads "5."; as a word it had no direction to "1."."""
    days = [f"{d}." for d in range(1, 32)]
    state = _turning(monkeypatch, days, "5.")
    target = MobileTarget("s1", {"platform": "iOS"}, {})

    ok, message = asyncio.run(target._interact("type", IosWheel("5."), "1"))

    assert ok, message
    assert state["orders"] == ["previous"] * 4


def test_a_wheel_still_settling_is_read_again(monkeypatch):
    """The first read after a turn answered with the value the wheel was
    leaving, and the turn was taken for the wheel's end."""
    monkeypatch.setattr("drivers.mobile.WHEEL_SETTLE", 0.0)
    state = _turning(monkeypatch, [str(y) for y in range(1990, 2011)], "1998", stale=True)
    target = MobileTarget("s1", {"platform": "iOS"}, {})

    ok, message = asyncio.run(target._interact("type", IosWheel("1998"), "2000"))

    assert ok, message
    assert state["orders"] == ["next", "next"]


def test_a_value_the_wheel_does_not_have_is_reported(monkeypatch):
    state = _turning(monkeypatch, [str(y) for y in range(1990, 2011)], "2009")
    target = MobileTarget("s1", {"platform": "iOS"}, {})

    ok, message = asyncio.run(target._interact("type", IosWheel("2009"), "2030"))

    assert not ok
    assert 'stops at "2010"' in message
    assert state["orders"] == ["next"] * 2


# --- what a wheel is recorded as ------------------------------------------- #

IOS_PICKER = """
<AppiumAUT>
  <XCUIElementTypeApplication name="THY" x="0" y="0" width="390" height="844" visible="true" enabled="true">
    <XCUIElementTypeWindow x="0" y="0" width="390" height="844" visible="true" enabled="true">
      <XCUIElementTypeButton name="doneButton" label="Done" x="300" y="500" width="80" height="40" visible="true" enabled="true"/>
      <XCUIElementTypePicker x="0" y="600" width="390" height="200" visible="true" enabled="true">
        <XCUIElementTypePickerWheel value="4" x="0" y="600" width="100" height="200" visible="true" enabled="true"/>
        <XCUIElementTypePickerWheel value="October" x="100" y="600" width="150" height="200" visible="true" enabled="true"/>
        <XCUIElementTypePickerWheel value="1998" x="250" y="600" width="140" height="200" visible="true" enabled="true"/>
      </XCUIElementTypePicker>
    </XCUIElementTypeWindow>
  </XCUIElementTypeApplication>
</AppiumAUT>
"""

ANDROID_PICKER = """
<hierarchy rotation="0">
  <android.widget.FrameLayout bounds="[0,0][1080,2400]" displayed="true">
    <android.widget.Button bounds="[800,1800][1000,1900]" displayed="true" text="Select" resource-id="ivDone"/>
    <android.widget.NumberPicker bounds="[0,1000][300,1400]" displayed="true">
      <android.widget.EditText bounds="[0,1150][300,1250]" displayed="true" text="2014" class="android.widget.EditText"/>
    </android.widget.NumberPicker>
  </android.widget.FrameLayout>
</hierarchy>
"""


def _selectors(xml, platform):
    from mobile_dom import MobileDOMManager
    manager = MobileDOMManager(xml, platform, 1080, 2400)
    return {(e.class_name, e.text): e.selector for e in manager.get_all_elements()}


def test_an_ios_wheel_is_recorded_by_its_position_not_its_value():
    """Named by its value, the year wheel was to be found as "1998" on a run
    where it opened on 2014."""
    selectors = _selectors(IOS_PICKER, "iOS")
    year = selectors[("XCUIElementTypePickerWheel", "1998")]
    assert year.endswith("XCUIElementTypePickerWheel[3]"), year
    assert "@text" not in year
    # A control whose words are its own keeps being found by them.
    assert selectors[("XCUIElementTypeButton", "Done")] == '//*[@content-desc="doneButton"]'


def test_an_android_wheel_is_recorded_by_its_position_not_its_value():
    selectors = _selectors(ANDROID_PICKER, "Android")
    year = selectors[("android.widget.EditText", "2014")]
    assert "NumberPicker" in year and "@text" not in year, year
    assert selectors[("android.widget.Button", "Select")] == '//*[@resource-id="ivDone"]'
