"""Getting a freshly attached device onto the app's own first screen.

None of this can be exercised against a real phone in CI, and all of it is
sequencing — answer prompts until they stop, work out what the app is called,
then make sure it is the thing on screen. A fake driver is enough to pin the
sequencing down, which is where the bugs were.
"""

import asyncio
import time
import unittest
from typing import List, Optional
from unittest.mock import AsyncMock, patch

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


class WalkingPastTheWayIn(unittest.IsolatedAsyncioTestCase):
    """The welcome carousel, the sign-in wall, the "what's new" sheet.

    None of it is the feature under test, and all of it used to be dismissed by
    the agent: measured on the real app, four model calls and roughly 35,000
    tokens on the first step of every single mobile run, spent tapping Skip.
    """

    def _phone(self, screens):
        """A phone that shows each screen in turn as taps land on it."""
        state = {"i": 0}

        async def has_text(_sid, _platform, needles):
            here = screens[min(state["i"], len(screens) - 1)]
            return any(n.lower() in here.lower() for n in needles)

        async def tap(_sid, _platform, label):
            here = screens[min(state["i"], len(screens) - 1)]
            if label in here:
                state["i"] += 1
                return True
            return False

        return state, has_text, tap

    async def _walk(self, screens):
        state, has_text, tap = self._phone(screens)
        with patch.object(mobile_session.appium, "screen_has_text", has_text), \
             patch.object(mobile_session.appium, "tap_by_text", tap), \
             patch.object(mobile_session.asyncio, "sleep", AsyncMock()):
            return await mobile_session.settle_onboarding("s", "iOS"), state

    async def test_it_walks_a_carousel_to_the_home_screen(self):
        taps, _ = await self._walk([
            "Skip Next", "Continue as a guest Sign in", "Got it",
            "Book a flight Check-in",
        ])
        self.assertEqual(taps, 3)

    async def test_a_launch_that_lands_on_the_home_screen_costs_one_look(self):
        """Every launch after the first. It must not tap anything."""
        taps, state = await self._walk(["Book a flight Uçuş ara"])
        self.assertEqual(taps, 0)
        self.assertEqual(state["i"], 0)

    async def test_it_stops_when_nothing_matches_rather_than_guessing(self):
        taps, _ = await self._walk(["Bir şey Başka bir şey"])
        self.assertEqual(taps, 0)

    async def test_it_is_bounded_so_it_cannot_walk_into_the_app(self):
        """A screen that always offers Continue — a booking form — must not be
        pressed forever. Four screens is an onboarding; forty is the app."""
        state, has_text, tap = self._phone(["Continue"])
        with patch.object(mobile_session.appium, "screen_has_text", has_text), \
             patch.object(mobile_session.appium, "tap_by_text", tap), \
             patch.object(mobile_session.asyncio, "sleep", AsyncMock()):
            taps = await mobile_session.settle_onboarding("s", "iOS")
        self.assertEqual(taps, mobile_session.MAX_SKIPS)

    def test_it_never_signs_in_buys_or_refuses(self):
        """These are the taps that would change what the run is testing."""
        labels = " | ".join(mobile_session.SKIP_LABELS).lower()
        for forbidden in ("sign in", "giriş yap", "register", "üye ol", "pay",
                          "satın al", "don't allow", "izin verme", "reddet"):
            self.assertNotIn(forbidden, labels, forbidden)

    def test_the_unambiguous_way_out_is_preferred(self):
        """A screen offering both "Continue as a guest" and "Continue" must
        take the door, not the button that may submit something."""
        order = list(mobile_session.SKIP_LABELS)
        self.assertLess(order.index("Continue as a guest"), order.index("Continue"))
        self.assertLess(order.index("Skip"), order.index("OK"))


class StartingEachScenarioFromTheSamePlace(unittest.IsolatedAsyncioTestCase):
    """A web case gets a new browser, so it starts from nothing. Mobile cases
    share one session, and the app remembers.

    Measured on the real set: a scenario left ESB in the destination and handed
    it to the next one, which had been written to expect an empty field and
    failed on a value it never set. That is not flakiness — it is the second
    scenario reading the first one's leftovers, and it makes every mobile set
    order-dependent.
    """

    async def test_the_app_is_closed_and_reopened(self):
        calls = []
        with patch.object(mobile_session.appium, "terminate_app",
                          AsyncMock(side_effect=lambda *a: calls.append("terminate"))), \
             patch.object(mobile_session.appium, "activate_app",
                          AsyncMock(side_effect=lambda *a: calls.append("activate"))), \
             patch.object(mobile_session, "settle_permissions", AsyncMock(return_value=0)), \
             patch.object(mobile_session, "settle_onboarding", AsyncMock(return_value=0)), \
             patch.object(mobile_session.asyncio, "sleep", AsyncMock()):
            ok = await mobile_session.restart_app("s", "iOS", "com.thy.app")
        self.assertTrue(ok)
        self.assertEqual(calls, ["terminate", "activate"])

    async def test_the_way_in_is_walked_again_afterwards(self):
        """A restarted app shows its permission prompts and its carousel once
        more; leaving those to the agent is the cost this whole thing exists
        to avoid."""
        walked = AsyncMock(return_value=1)
        with patch.object(mobile_session.appium, "terminate_app", AsyncMock()), \
             patch.object(mobile_session.appium, "activate_app", AsyncMock()), \
             patch.object(mobile_session, "settle_permissions", AsyncMock(return_value=0)), \
             patch.object(mobile_session, "settle_onboarding", walked), \
             patch.object(mobile_session.asyncio, "sleep", AsyncMock()):
            await mobile_session.restart_app("s", "iOS", "com.thy.app")
        walked.assert_awaited()

    async def test_a_session_with_no_app_is_left_alone(self):
        """Nothing to restart, and terminating whatever happens to be in front
        would be closing the tester's own screen."""
        self.assertFalse(await mobile_session.restart_app("s", "iOS", None))

    async def test_a_driver_that_refuses_does_not_fail_the_run(self):
        with patch.object(mobile_session.appium, "terminate_app",
                          AsyncMock(side_effect=RuntimeError("no"))), \
             patch.object(mobile_session.asyncio, "sleep", AsyncMock()):
            self.assertFalse(await mobile_session.restart_app("s", "iOS", "com.thy.app"))
