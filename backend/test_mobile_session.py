"""Getting a freshly attached device onto the app's own first screen.

None of this can be exercised against a real phone in CI, and all of it is
sequencing — answer prompts until they stop, work out what the app is called,
then make sure it is the thing on screen. A fake driver is enough to pin the
sequencing down, which is where the bugs were.
"""

import asyncio
import time
from typing import List, Optional

import pytest

import mobile_session


class FakeDriver:
    """A device that asks for permissions, and may wander off to the home screen."""

    def __init__(
        self,
        alerts: Optional[List[List[str]]] = None,
        app_state: int = 4,
        active_app: Optional[str] = None,
        activate_works: bool = True,
    ):
        # One entry per poll: the buttons showing, or [] for no alert.
        self.alerts = list(alerts or [])
        self.accepted: List[Optional[str]] = []
        self.app_state = app_state
        self.active_app = active_app
        self.activate_works = activate_works
        self.activations: List[str] = []
        self.settings = {}
        self._moment = []

    async def alert_buttons(self, session_id, platform):
        # A timeline, not a stack: each poll consumes one moment, so an empty
        # entry models a poll where nothing was up yet. The moment is held so
        # alert_text answers about the same one rather than the next.
        if self.alerts and not self.alerts[0]:
            self.alerts.pop(0)
            self._moment = []
        else:
            self._moment = self.alerts[0] if self.alerts else []
        return self._moment

    async def alert_text(self, session_id):
        return "Allow location?" if getattr(self, "_moment", None) else None

    async def accept_alert(self, session_id, button_label=None):
        if not self.alerts or not self.alerts[0]:
            return False
        self.accepted.append(button_label)
        self.alerts.pop(0)
        return True

    async def query_app_state(self, session_id, app_id):
        return self.app_state

    async def activate_app(self, session_id, app_id):
        self.activations.append(app_id)
        if self.activate_works:
            self.app_state = 4
        return self.activate_works

    async def active_app_info(self, session_id, platform):
        return self.active_app

    async def update_settings(self, session_id, settings):
        self.settings.update(settings)
        return True


@pytest.fixture
def driver(monkeypatch):
    fake = FakeDriver()

    def install(d):
        for name in (
            "alert_buttons", "alert_text", "accept_alert", "query_app_state",
            "activate_app", "active_app_info", "update_settings",
        ):
            monkeypatch.setattr(mobile_session.appium, name, getattr(d, name))
        return d

    fake.install = install
    install(fake)
    return fake


def run(coro):
    return asyncio.run(coro)


# --- answering the prompts ------------------------------------------------- #

def test_a_prompt_that_appears_after_a_quiet_moment_is_still_answered(driver):
    """The bug this replaces: the old loop gave up the first time it saw no
    alert, so a location prompt a second behind the session attaching was
    never answered — which is most of them."""
    driver.alerts = [[], [], ["Don't Allow", "Allow While Using App"]]
    handled = run(mobile_session.settle_permissions("s", "iOS", window_s=6))
    assert handled == 1
    assert driver.accepted == ["Allow While Using App"]


def test_stacked_prompts_are_all_answered(driver):
    driver.alerts = [["Allow"], ["OK"], ["İzin Ver"]]
    handled = run(mobile_session.settle_permissions("s", "iOS", window_s=8))
    assert handled == 3
    assert driver.accepted == ["Allow", "OK", "İzin Ver"]


def test_the_permissive_button_is_chosen_never_the_deny(driver):
    driver.alerts = [["Don't Allow", "Allow Once", "Allow While Using App"]]
    run(mobile_session.settle_permissions("s", "iOS", window_s=4))
    assert driver.accepted == ["Allow While Using App"]


def test_a_turkish_prompt_is_answered_in_turkish(driver):
    driver.alerts = [["İzin Verme", "Uygulamayı Kullanırken İzin Ver"]]
    run(mobile_session.settle_permissions("s", "iOS", window_s=4))
    assert driver.accepted == ["Uygulamayı Kullanırken İzin Ver"]


def test_an_unrecognised_alert_still_gets_the_default_accept(driver):
    """Better to take the alert's own default than leave it on screen."""
    driver.alerts = [["Later", "Upgrade Now"]]
    handled = run(mobile_session.settle_permissions("s", "iOS", window_s=4))
    assert handled == 1
    assert driver.accepted == [None]


def test_a_device_with_nothing_to_ask_does_not_spend_the_whole_window(driver):
    """Permissions granted on an earlier session are the common case, and it
    must not cost twelve seconds on every connect to find that out."""
    driver.alerts = []
    started = time.monotonic()
    handled = run(mobile_session.settle_permissions("s", "iOS", window_s=30))
    elapsed = time.monotonic() - started
    assert handled == 0
    assert elapsed < 30
    # ...but it still watches long enough for a cold launch to get its question in.
    assert elapsed >= mobile_session.MIN_WATCH_S


# --- working out what the app is called ------------------------------------ #

def test_the_upload_handle_is_never_the_app_id(driver):
    """A bs:// handle names an upload, not anything the device understands."""
    driver.active_app = "com.thy.reg"
    got = run(mobile_session.resolve_app_id("s", "iOS", "bs://abc123", {}))
    assert got == "com.thy.reg"


def test_the_session_capabilities_name_the_app_first(driver):
    driver.active_app = "com.apple.springboard"
    got = run(mobile_session.resolve_app_id("s", "iOS", "bs://abc", {"bundleId": "com.thy.test"}))
    assert got == "com.thy.test"


def test_the_home_screen_is_never_resolved_as_the_app(driver):
    """Resolving SpringBoard and then activating it is exactly how a session
    ends up pinned to the home screen with no way back to the app."""
    driver.active_app = "com.apple.springboard"
    assert run(mobile_session.resolve_app_id("s", "iOS", "bs://abc", {})) is None
    assert run(mobile_session.resolve_app_id("s", "Android", None, {})) is None


def test_a_plain_bundle_id_is_taken_as_given(driver):
    driver.active_app = None
    got = run(mobile_session.resolve_app_id("s", "iOS", "com.thy.dev", {}))
    assert got == "com.thy.dev"


# --- keeping the app in front ---------------------------------------------- #

def test_an_app_already_in_front_is_left_alone(driver):
    driver.app_state = 4
    assert run(mobile_session.ensure_foreground("s", "com.thy.reg")) is True
    assert driver.activations == []


def test_an_app_pushed_to_the_background_is_brought_back(driver):
    """Accepting a permission prompt can hand the screen to the OS rather than
    to the app that asked for it."""
    driver.app_state = 2
    assert run(mobile_session.ensure_foreground("s", "com.thy.reg")) is True
    assert driver.activations == ["com.thy.reg"]


def test_an_app_that_will_not_come_forward_is_reported_not_raised(driver):
    driver.app_state = 1
    driver.activate_works = False
    assert run(mobile_session.ensure_foreground("s", "com.thy.reg", attempts=2)) is False
    assert len(driver.activations) == 2


def test_no_app_id_means_nothing_to_foreground(driver):
    assert run(mobile_session.ensure_foreground("s", None)) is False
    assert driver.activations == []


# --- the whole routine ----------------------------------------------------- #

def test_prepare_answers_prompts_then_fronts_the_app(driver):
    driver.alerts = [[], ["Don't Allow", "Allow While Using App"]]
    driver.app_state = 2
    driver.active_app = "com.thy.reg"
    got = run(mobile_session.prepare_session("s", "iOS", "bs://abc", {}, window_s=6))
    assert driver.accepted == ["Allow While Using App"]
    assert got == "com.thy.reg"
    assert driver.activations == ["com.thy.reg"]
    assert driver.settings.get("respectSystemAlerts") is True


def test_prepare_never_raises_when_the_device_stops_answering(driver):
    async def boom(*args, **kwargs):
        raise RuntimeError("device went away")

    driver.install(driver)
    import pytest as _pytest  # noqa: F401
    mobile_session.appium.alert_buttons = boom
    mobile_session.appium.query_app_state = boom
    # A device that will not answer must still leave the tester with a session.
    assert run(mobile_session.prepare_session("s", "iOS", "com.thy.dev", {}, window_s=2)) is not None
