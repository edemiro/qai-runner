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
