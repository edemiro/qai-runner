import unittest
from unittest.mock import AsyncMock, patch

import devices


def _entry(**overrides):
    """A devicectl record for a healthy, plugged-in iPhone."""
    entry = {
        "hardwareProperties": {
            "udid": "00008130-000E31341E6A001C",
            "platform": "iOS",
            "marketingName": "iPhone 15 Pro Max",
            "deviceType": "iPhone",
        },
        "connectionProperties": {
            "pairingState": "paired",
            "tunnelState": "connected",
            "transportType": "wired",
        },
        "deviceProperties": {
            "name": "Ergün iPhone’u",
            "osVersionNumber": "26.6",
        },
    }
    for section, values in overrides.items():
        entry[section] = {**entry[section], **values}
    return entry


def _report(*entries):
    return {"result": {"devices": list(entries)}}


class ParseDevicectlDevices(unittest.TestCase):
    def test_reads_a_connected_iphone(self):
        [device] = devices._parse_devicectl_devices(_report(_entry()))
        self.assertEqual(device["udid"], "00008130-000E31341E6A001C")
        self.assertEqual(device["platform"], "iOS")
        self.assertEqual(device["version"], "26.6")
        self.assertEqual(device["name"], "Ergün iPhone’u")
        self.assertFalse(device["isEmulator"])

    def test_falls_back_to_the_marketing_name(self):
        entry = _entry()
        entry["deviceProperties"].pop("name")
        [device] = devices._parse_devicectl_devices(_report(entry))
        self.assertEqual(device["name"], "iPhone 15 Pro Max")

    def test_unknown_os_version_is_labelled_not_blank(self):
        entry = _entry()
        entry["deviceProperties"].pop("osVersionNumber")
        [device] = devices._parse_devicectl_devices(_report(entry))
        self.assertEqual(device["version"], "Unknown")

    def test_an_unplugged_device_is_not_offered(self):
        # devicectl keeps listing a device after it is unplugged; a dead tunnel
        # is what separates it from one that can actually be driven.
        report = _report(_entry(connectionProperties={"tunnelState": "unavailable"}))
        self.assertEqual(devices._parse_devicectl_devices(report), [])

    def test_an_unpaired_device_is_not_offered(self):
        report = _report(_entry(connectionProperties={"pairingState": "unpaired"}))
        self.assertEqual(devices._parse_devicectl_devices(report), [])

    def test_non_ios_hardware_is_ignored(self):
        report = _report(_entry(hardwareProperties={"platform": "macOS"}))
        self.assertEqual(devices._parse_devicectl_devices(report), [])

    def test_an_entry_without_a_udid_is_skipped(self):
        entry = _entry()
        entry["hardwareProperties"].pop("udid")
        self.assertEqual(devices._parse_devicectl_devices(_report(entry)), [])

    def test_an_empty_or_malformed_report_yields_nothing(self):
        self.assertEqual(devices._parse_devicectl_devices({}), [])
        self.assertEqual(devices._parse_devicectl_devices(_report()), [])


class PhysicalDeviceFallback(unittest.IsolatedAsyncioTestCase):
    async def test_devicectl_is_only_consulted_when_libimobiledevice_finds_nothing(self):
        found = [{"udid": "abc", "platform": "iOS", "version": "18.0",
                  "name": "iPhone", "isEmulator": False}]
        with patch.object(devices, "_devices_via_libimobiledevice",
                          AsyncMock(return_value=found)), \
             patch.object(devices, "_devices_via_devicectl", AsyncMock()) as fallback:
            self.assertEqual(await devices._get_ios_physical_devices(), found)
            fallback.assert_not_awaited()

    async def test_devicectl_covers_a_machine_without_libimobiledevice(self):
        found = [{"udid": "xyz", "platform": "iOS", "version": "26.6",
                  "name": "iPhone", "isEmulator": False}]
        with patch.object(devices, "_devices_via_libimobiledevice",
                          AsyncMock(return_value=[])), \
             patch.object(devices, "_devices_via_devicectl",
                          AsyncMock(return_value=found)):
            self.assertEqual(await devices._get_ios_physical_devices(), found)

    async def test_neither_tool_present_is_not_an_error(self):
        with patch.object(devices, "_devices_via_libimobiledevice",
                          AsyncMock(return_value=[])), \
             patch.object(devices, "_devices_via_devicectl",
                          AsyncMock(return_value=[])):
            self.assertEqual(await devices._get_ios_physical_devices(), [])


if __name__ == "__main__":
    unittest.main()


class TestEnvironments(unittest.TestCase):
    """Dev, Test and Reg are the same TK app on different back ends, and which
    one a session opens is the decision the device card exists for.

    They are matched by the name the phone reports rather than by a hardcoded
    bundle id: the ids differ per build and change, and a wrong one fails at
    session start with nothing useful to say.
    """

    INSTALLED = [
        {"id": "com.turkishairlines.smartmobile.enterprise.regresyon", "name": "ThyReg"},
        {"id": "com.turkishairlines.store", "name": "TK Store"},
    ]

    def test_an_installed_environment_resolves_to_its_bundle_id(self):
        matched = {e["label"]: e for e in devices.match_environments(self.INSTALLED)}
        self.assertEqual(
            matched["ThyReg"]["appId"],
            "com.turkishairlines.smartmobile.enterprise.regresyon",
        )
        self.assertTrue(matched["ThyReg"]["installed"])

    def test_a_missing_environment_is_still_offered_and_marked_absent(self):
        # Shown rather than hidden: an option that silently disappears reads as
        # the feature being broken, not as the app not being installed.
        matched = {e["label"]: e for e in devices.match_environments(self.INSTALLED)}
        self.assertIsNone(matched["ThyDev"]["appId"])
        self.assertFalse(matched["ThyDev"]["installed"])

    def test_all_three_are_always_returned_in_order(self):
        labels = [e["label"] for e in devices.match_environments([])]
        self.assertEqual(labels, ["ThyDev", "ThyTest", "ThyReg"])

    def test_matching_ignores_case_and_padding(self):
        matched = {
            e["label"]: e
            for e in devices.match_environments([{"id": "x.y", "name": "  thyreg "}])
        }
        self.assertEqual(matched["ThyReg"]["appId"], "x.y")

    def test_an_unrelated_app_never_becomes_an_environment(self):
        matched = devices.match_environments([{"id": "a.b", "name": "TK Store"}])
        self.assertTrue(all(e["appId"] is None for e in matched))


class IosAppListing(unittest.IsolatedAsyncioTestCase):
    """iOS apps are read with Xcode's own device tool. The previous route
    needed libimobiledevice, which is a separate install and usually missing —
    and when it was missing the list came back empty, which reads as "this
    phone has no apps" rather than "the tool is not here"."""

    REPORT = {
        "result": {
            "apps": [
                {"bundleIdentifier": "com.turkishairlines.smartmobile.enterprise.regresyon",
                 "name": "ThyReg"},
                {"bundleIdentifier": "com.qai.WebDriverAgentRunner.xctrunner",
                 "name": "WebDriverAgentRunner-Runner"},
                {"bundleIdentifier": "com.example.clip", "name": "Clip", "appClip": True},
                {"name": "No bundle id"},
            ]
        }
    }

    async def _list(self, report):
        import json as json_module

        def write_report(cmd, timeout=0):
            path = cmd[cmd.index("--json-output") + 1]
            with open(path, "w", encoding="utf-8") as handle:
                json_module.dump(report, handle)
            return ""

        with patch.object(devices, "_run", AsyncMock(side_effect=write_report)):
            return await devices._ios_apps_via_devicectl("udid")

    async def test_apps_are_read_with_their_real_names(self):
        apps = await self._list(self.REPORT)
        self.assertIn(
            {"id": "com.turkishairlines.smartmobile.enterprise.regresyon", "name": "ThyReg"},
            apps,
        )

    async def test_the_automation_runner_is_not_offered_as_a_target(self):
        # WebDriverAgent is how QAi drives the phone, not something to test.
        names = [app["name"] for app in await self._list(self.REPORT)]
        self.assertNotIn("WebDriverAgentRunner-Runner", names)

    async def test_app_clips_and_malformed_entries_are_skipped(self):
        ids = [app["id"] for app in await self._list(self.REPORT)]
        self.assertNotIn("com.example.clip", ids)
        self.assertEqual(len(ids), 1)

    async def test_an_unreadable_report_is_an_empty_list_not_a_crash(self):
        with patch.object(devices, "_run", AsyncMock(return_value="")):
            self.assertEqual(await devices._ios_apps_via_devicectl("udid"), [])


class BuildsOfferedForADevice(unittest.TestCase):
    """The app picker should only offer what the device in hand can install.

    The account holds both tracks — measured on the real one, 58 .apk against
    18 .ipa — and the picker listed all 76 whichever device was connected. The
    environment buttons already read the platform out of the file name; this is
    the same reading applied to the full list.
    """

    ANDROID = [
        "26.9.17.0_Android_Test.apk",
        "TK_REG_1.49.3.967.apk",
        "and_reg_1.47.0.919.apk",
        "AJet-AND-preprod-2-1-4-52.apk",
        "something.aab",
    ]
    IOS = [
        "26.9.17.0_IOS_Test.ipa",
        "BrowserStack-SampleApp.ipa",
    ]

    def test_an_android_device_is_offered_the_android_builds(self):
        for name in self.ANDROID:
            self.assertTrue(devices.matches_platform(name, "Android"), name)
        for name in self.IOS:
            self.assertFalse(devices.matches_platform(name, "Android"), name)

    def test_an_ios_device_is_offered_the_ios_builds(self):
        for name in self.IOS:
            self.assertTrue(devices.matches_platform(name, "iOS"), name)
        for name in self.ANDROID:
            self.assertFalse(devices.matches_platform(name, "iOS"), name)

    def test_the_platform_written_into_the_name_counts_too(self):
        """Not every build carries an extension QAi recognises, but the team
        writes the platform into the name as well."""
        self.assertTrue(devices.matches_platform("26.9.17.0_Android_Test", "Android"))
        self.assertFalse(devices.matches_platform("26.9.17.0_IOS_Test", "Android"))

    def test_a_name_that_says_neither_is_kept(self):
        """This list also carries the packages installed on a physical phone,
        whose names are bundle ids. Hiding one is worse than offering it."""
        for name in ("com.thy.thytest", "ThyReg", ""):
            self.assertTrue(devices.matches_platform(name, "Android"), name)
            self.assertTrue(devices.matches_platform(name, "iOS"), name)

    def test_an_unknown_platform_filters_nothing(self):
        self.assertTrue(devices.matches_platform("x.ipa", "Tizen"))
