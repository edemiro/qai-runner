"""BrowserStack devices, driven as if they were on the desk."""

import os
import unittest
from unittest.mock import AsyncMock, patch

import appium_client as appium
import browserstack


class DeviceIdentity(unittest.TestCase):
    """A cloud device is picked by model and OS version, so QAi carries those
    where a local device carries a udid. The id has to survive the round trip
    or a saved run names a device nobody can book again."""

    def test_an_id_round_trips(self):
        made = browserstack.device_id("iPhone 15 Pro", "17")
        self.assertEqual(browserstack.parse_device_id(made), ("iPhone 15 Pro", "17"))

    def test_a_local_udid_is_not_mistaken_for_a_cloud_one(self):
        for udid in ("emulator-5554", "00008030-001A2C3D4E5F", "R58M12345AB"):
            self.assertFalse(browserstack.is_cloud_device(udid), udid)

    def test_a_model_name_containing_spaces_survives(self):
        made = browserstack.device_id("Samsung Galaxy S23 Ultra", "13.0")
        self.assertEqual(
            browserstack.parse_device_id(made), ("Samsung Galaxy S23 Ultra", "13.0")
        )

    def test_a_malformed_id_is_rejected_rather_than_guessed(self):
        self.assertIsNone(browserstack.parse_device_id("bs:onlyname"))


class Capabilities(unittest.TestCase):

    def setUp(self):
        self._env = dict(os.environ)
        os.environ["BROWSERSTACK_USERNAME"] = "ergun"
        os.environ["BROWSERSTACK_ACCESS_KEY"] = "secret-key"

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._env)

    def _caps(self, **kwargs):
        base = {"udid": browserstack.device_id("Google Pixel 8", "14.0"),
                "platform": "Android"}
        base.update(kwargs)
        return browserstack.capabilities(**base)["capabilities"]["alwaysMatch"]

    def test_the_device_is_booked_by_model_and_version(self):
        options = self._caps()["bstack:options"]
        self.assertEqual(options["deviceName"], "Google Pixel 8")
        self.assertEqual(options["osVersion"], "14.0")

    def test_android_and_ios_get_their_own_automation_engine(self):
        self.assertEqual(self._caps()["appium:automationName"], "UiAutomator2")
        ios = self._caps(
            udid=browserstack.device_id("iPhone 15", "17"), platform="iOS",
        )
        self.assertEqual(ios["appium:automationName"], "XCUITest")
        self.assertEqual(ios["platformName"], "iOS")

    def test_the_app_under_test_is_the_uploaded_one(self):
        caps = self._caps(app_id="bs://abc123")
        self.assertEqual(caps["appium:app"], "bs://abc123")

    def test_no_app_means_no_app_capability_rather_than_an_empty_one(self):
        # An empty appium:app is not "no app"; BrowserStack reads it as a
        # request to install nothing and fails the session.
        self.assertNotIn("appium:app", self._caps())

    def test_the_device_is_named_rather_than_asked_for_as_real(self):
        """`realMobile` is an Automate capability — it picks a phone to run a
        browser on. This is App Automate, where deviceName and osVersion name
        the device, and sending it was seen to hand back a desktop Chrome that
        answered every Appium command with "unknown command"."""
        options = self._caps()["bstack:options"]
        self.assertNotIn("realMobile", options)
        self.assertEqual(options["deviceName"], "Google Pixel 8")
        self.assertEqual(options["osVersion"], "14.0")

    def test_the_build_keeps_its_own_signature(self):
        """The one that stopped every iOS run: BrowserStack re-signs by
        default, which replaces the team prefix, and the app then fails its
        first keychain read with -34018 and exits before drawing anything."""
        self.assertIs(self._caps()["bstack:options"]["resignApp"], False)

    def test_the_session_is_given_longer_than_the_default_to_go_quiet(self):
        """90 seconds is BrowserStack's default and less than one agent step
        against a full screen, so a run lost the device mid-scenario."""
        self.assertGreaterEqual(self._caps()["bstack:options"]["idleTimeout"], 300)

    def test_credentials_are_read_when_the_session_is_made(self):
        # Saved in Settings while the server runs: the next session must use
        # them without a restart.
        os.environ["BROWSERSTACK_USERNAME"] = "someone-else"
        self.assertEqual(self._caps()["bstack:options"]["userName"], "someone-else")

    def test_the_hub_url_carries_the_credentials_escaped(self):
        os.environ["BROWSERSTACK_USERNAME"] = "user@example.com"
        url = browserstack.hub_url()
        self.assertIn("user%40example.com", url)
        self.assertIn("hub-cloud.browserstack.com/wd/hub", url)


class DeviceList(unittest.IsolatedAsyncioTestCase):

    RAW = [
        {"device": "iPhone 15", "os": "ios", "os_version": "17", "realMobile": True},
        {"device": "Google Pixel 8", "os": "android", "os_version": "14.0",
         "realMobile": True},
        {"device": "", "os": "android", "os_version": "13.0"},
        {"device": "Broken", "os": "android"},
    ]

    async def test_devices_come_back_in_the_shape_the_picker_already_uses(self):
        with patch.object(browserstack, "_get", AsyncMock(return_value=self.RAW)):
            devices = await browserstack.list_devices()
        self.assertEqual(len(devices), 2)
        for device in devices:
            self.assertEqual(
                set(device) >= {"udid", "platform", "version", "name", "source"}, True
            )
            self.assertEqual(device["source"], "browserstack")

    async def test_entries_without_a_model_or_version_are_dropped(self):
        with patch.object(browserstack, "_get", AsyncMock(return_value=self.RAW)):
            names = [d["name"] for d in await browserstack.list_devices()]
        self.assertNotIn("Broken", names)
        self.assertNotIn("", names)

    async def test_an_account_with_no_uploaded_apps_is_not_an_error(self):
        import httpx

        response = httpx.Response(422, request=httpx.Request("GET", "http://x"))
        error = httpx.HTTPStatusError("no apps", request=response.request, response=response)
        with patch.object(browserstack, "_get", AsyncMock(side_effect=error)):
            self.assertEqual(await browserstack.list_apps(), [])


class SessionRouting(unittest.TestCase):
    """Every call after the session is made — source, screenshot, tap — has to
    reach the hub the session lives on, or it lands on the local Appium server
    and reports a session that does not exist."""

    def setUp(self):
        appium._session_hubs.clear()

    tearDown = setUp

    def test_a_bound_session_routes_to_its_hub(self):
        appium.bind_session("cloud-1", "https://u:k@hub-cloud.browserstack.com/wd/hub")
        self.assertEqual(
            appium._hub_for("/session/cloud-1/screenshot"),
            "https://u:k@hub-cloud.browserstack.com/wd/hub",
        )

    def test_an_unbound_session_stays_local(self):
        self.assertEqual(appium._hub_for("/session/local-1/source"), appium.APPIUM_HOST)

    def test_a_local_session_is_never_bound(self):
        appium.bind_session("local-2", appium.APPIUM_HOST)
        self.assertEqual(appium._hub_for("/session/local-2/source"), appium.APPIUM_HOST)

    def test_a_path_without_a_session_stays_local(self):
        self.assertEqual(appium._hub_for("/status"), appium.APPIUM_HOST)

    def test_a_closed_session_stops_routing(self):
        appium.bind_session("cloud-3", "https://hub.example/wd/hub")
        appium.release_session("cloud-3")
        self.assertEqual(appium._hub_for("/session/cloud-3/source"), appium.APPIUM_HOST)
