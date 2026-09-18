"""Device discovery for Android (adb) and iOS (libimobiledevice or devicectl)."""

import asyncio
import json
import os
import shutil
import tempfile
from typing import Dict, List, Optional


async def _run(cmd: List[str], timeout: float = 10.0) -> str:
    """Run a command and return stdout, or '' on any failure."""
    if shutil.which(cmd[0]) is None:
        return ""
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        return stdout.decode("utf-8", errors="ignore")
    except Exception:
        return ""


async def _android_property(udid: str, prop: str) -> str:
    out = await _run(["adb", "-s", udid, "shell", "getprop", prop], timeout=6.0)
    return out.strip()


async def get_android_devices() -> List[Dict]:
    out = await _run(["adb", "devices"])
    if not out:
        return []

    udids = []
    for line in out.splitlines()[1:]:
        parts = line.split()
        if len(parts) >= 2 and parts[1] == "device":
            udids.append(parts[0])

    async def describe(udid: str) -> Dict:
        version, model, manufacturer = await asyncio.gather(
            _android_property(udid, "ro.build.version.release"),
            _android_property(udid, "ro.product.model"),
            _android_property(udid, "ro.product.manufacturer"),
        )
        is_emulator = udid.startswith("emulator-") or model.lower().startswith("sdk")
        label = " ".join(p for p in (manufacturer.title(), model) if p) or udid
        return {
            "udid": udid,
            "platform": "Android",
            "version": version or "Unknown",
            "name": label,
            "isEmulator": is_emulator,
        }

    return list(await asyncio.gather(*(describe(u) for u in udids)))


async def get_ios_devices() -> List[Dict]:
    devices = await _get_ios_physical_devices()
    devices.extend(await _get_ios_simulators())
    return devices


async def _get_ios_physical_devices() -> List[Dict]:
    """Physical iPhones and iPads, from whichever tool this machine has.

    libimobiledevice is the documented path, but it arrives through Homebrew,
    which a fresh macOS does not have and which needs an admin password to
    install. Xcode ships `devicectl`, and it already knows every paired device,
    so fall back to it rather than telling the user no phone is attached when
    one plainly is.
    """
    devices = await _devices_via_libimobiledevice()
    return devices or await _devices_via_devicectl()


async def _devices_via_libimobiledevice() -> List[Dict]:
    out = await _run(["idevice_id", "-l"])
    udids = [line.strip() for line in out.splitlines() if line.strip()]

    async def describe(udid: str) -> Dict:
        version = (await _run(["ideviceinfo", "-u", udid, "-k", "ProductVersion"], timeout=6.0)).strip()
        name = (await _run(["ideviceinfo", "-u", udid, "-k", "DeviceName"], timeout=6.0)).strip()
        return {
            "udid": udid,
            "platform": "iOS",
            "version": version or "Unknown",
            "name": name or "iPhone",
            "isEmulator": False,
        }

    return list(await asyncio.gather(*(describe(u) for u in udids)))


def _parse_devicectl_devices(data: Dict) -> List[Dict]:
    """Pick the drivable iOS devices out of a `devicectl list devices` report."""
    devices = []
    for entry in data.get("result", {}).get("devices", []):
        hardware = entry.get("hardwareProperties", {})
        connection = entry.get("connectionProperties", {})
        properties = entry.get("deviceProperties", {})

        if hardware.get("platform") != "iOS":
            continue
        # devicectl keeps listing a device long after it is unplugged, so
        # pairing alone means nothing — only a live tunnel can carry commands.
        if connection.get("pairingState") != "paired":
            continue
        if connection.get("tunnelState") not in ("connected", "available"):
            continue

        udid = hardware.get("udid")
        if not udid:
            continue

        devices.append({
            "udid": udid,
            "platform": "iOS",
            "version": properties.get("osVersionNumber") or "Unknown",
            "name": properties.get("name") or hardware.get("marketingName") or "iPhone",
            "isEmulator": False,
        })
    return devices


async def _devices_via_devicectl() -> List[Dict]:
    import json
    import os
    import tempfile

    # devicectl writes its report to a file and prints only progress to stdout,
    # so there is nothing useful to capture from the pipe.
    with tempfile.TemporaryDirectory() as directory:
        report = os.path.join(directory, "devices.json")
        await _run(
            ["xcrun", "devicectl", "list", "devices", "--json-output", report],
            timeout=20.0,
        )
        try:
            with open(report, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            return []

    return _parse_devicectl_devices(data)


async def _get_ios_simulators() -> List[Dict]:
    """Booted simulators, discovered through xcrun simctl (macOS only)."""
    import json

    out = await _run(["xcrun", "simctl", "list", "devices", "booted", "--json"])
    if not out:
        return []
    try:
        data = json.loads(out)
    except Exception:
        return []

    simulators = []
    for runtime, entries in data.get("devices", {}).items():
        # runtime looks like "com.apple.CoreSimulator.SimRuntime.iOS-17-4"
        version = runtime.split(".")[-1].replace("iOS-", "").replace("-", ".")
        for entry in entries:
            if entry.get("state") != "Booted":
                continue
            simulators.append({
                "udid": entry.get("udid"),
                "platform": "iOS",
                "version": version,
                "name": f"{entry.get('name', 'Simulator')} (Simulator)",
                "isEmulator": True,
            })
    return simulators


async def list_devices() -> List[Dict]:
    android, ios = await asyncio.gather(get_android_devices(), get_ios_devices())
    return [*android, *ios]


# The three builds of the Turkish Airlines app the team tests against. They are
# the same app pointed at development, test and regression back ends, so which
# one a session opens is the single most common decision made on this screen —
# it gets named buttons rather than a hunt through an alphabetical app list.
#
# Matched by the name the device itself reports, not by a hardcoded bundle id:
# the ids differ per build and change, the names do not, and a wrong id would
# fail at session start with nothing useful to say.
TEST_ENVIRONMENTS = ("ThyDev", "ThyTest", "ThyReg")


# How the team names an uploaded build: the platform and the environment both
# appear in the file name — "26.9.17.0_Android_Test.apk", "26.9.17.0_IOS_Test.ipa",
# "AND_REG_1.50.0.973.apk". Matching on those tokens is what lets the ThyReg
# button on a cloud iPhone find the iOS REG build and on a Pixel the Android one.
_ENV_TOKENS = {"thydev": "dev", "thytest": "test", "thyreg": "reg"}
_PLATFORM_TOKENS = {
    "android": (("android", "and_", "_and", ".apk", ".aab"), (".ipa", "ios")),
    "ios": (("ios", ".ipa"), (".apk", ".aab", "android")),
}


def _upload_for(label: str, platform: str, apps: List[Dict]) -> Optional[Dict]:
    """The newest uploaded build whose file name says this platform and env."""
    env = _ENV_TOKENS.get(label.lower())
    wants, rejects = _PLATFORM_TOKENS.get(platform.lower(), ((), ()))
    if not env or not wants:
        return None
    candidates = []
    for app in apps:
        if not str(app.get("id", "")).startswith("bs://"):
            continue
        name = str(app.get("name", "")).lower()
        if env not in name:
            continue
        if any(bad in name for bad in rejects) or not any(ok in name for ok in wants):
            continue
        candidates.append(app)
    candidates.sort(key=lambda a: str(a.get("uploadedAt", "")), reverse=True)
    return candidates[0] if candidates else None


def match_environments(apps: List[Dict], platform: str = "Android") -> List[Dict]:
    """Pair each known environment with the app that is it, if any.

    An environment resolves to an app id three ways, in order: a bundle id /
    package configured for it (THY_ENV_BUNDLE_IDS); an uploaded cloud build
    whose file name carries this platform and environment ("…_IOS_Test.ipa"),
    which BrowserStack then installs and launches for the session; or an
    installed app whose name is the environment. An environment with none is
    still returned, marked absent, so the picker shows all three and says which
    are missing — an option that quietly disappears looks like the feature is
    broken.
    """
    import config

    configured = {k.lower(): v for k, v in config.THY_ENV_BUNDLE_IDS.items()}
    by_name = {str(app.get("name", "")).strip().lower(): app for app in apps}
    matched = []
    for label in TEST_ENVIRONMENTS:
        app_id = configured.get(label.lower())
        source = "configured" if app_id else None
        if not app_id:
            upload = _upload_for(label, platform, apps)
            if upload:
                app_id, source = upload["id"], upload.get("name")
        if not app_id:
            app = by_name.get(label.lower())
            if app:
                app_id, source = app["id"], "installed"
        matched.append({
            "label": label,
            "appId": app_id,
            "installed": bool(app_id),
            # Which build the button will actually launch, for the tooltip.
            "source": source,
        })
    return matched


async def list_installed_apps(udid: str, platform: str) -> List[Dict]:
    """Third-party packages/bundles, so a session can target a specific app."""
    if platform.lower() == "android":
        out = await _run(["adb", "-s", udid, "shell", "pm", "list", "packages", "-3"], timeout=15.0)
        packages = sorted(
            line.replace("package:", "").strip()
            for line in out.splitlines()
            if line.startswith("package:")
        )
        return [{"id": pkg, "name": pkg.split(".")[-1]} for pkg in packages]

    # devicectl first: it ships with Xcode, which is already required to drive
    # an iPhone at all, whereas libimobiledevice is a separate install that is
    # usually missing — and when it is missing this list came back empty, which
    # reads as "this phone has no apps" rather than "the tool is not here".
    apps = await _ios_apps_via_devicectl(udid)
    if apps:
        return apps

    out = await _run(["ideviceinstaller", "-u", udid, "-l"], timeout=20.0)
    apps = []
    for line in out.splitlines()[1:]:
        parts = [p.strip() for p in line.split(",")]
        if parts and "." in parts[0]:
            bundle_id = parts[0]
            name = parts[-1].strip('"') if len(parts) > 2 else bundle_id
            apps.append({"id": bundle_id, "name": name})
    return sorted(apps, key=lambda a: a["name"].lower())


async def _ios_apps_via_devicectl(udid: str) -> List[Dict]:
    """Installed apps as Xcode's own device tool reports them.

    Written to a temporary file rather than parsed from the printed table,
    because the table is aligned for reading and an app whose name contains
    spaces cannot be split out of it reliably.
    """
    with tempfile.TemporaryDirectory() as directory:
        report = os.path.join(directory, "apps.json")
        await _run(
            ["xcrun", "devicectl", "device", "info", "apps",
             "--device", udid, "--json-output", report],
            timeout=60.0,
        )
        try:
            with open(report, encoding="utf-8") as handle:
                payload = json.load(handle)
        except Exception:
            return []

    apps = []
    for entry in payload.get("result", {}).get("apps", []):
        bundle_id = entry.get("bundleIdentifier")
        if not bundle_id or entry.get("appClip"):
            continue
        # WebDriverAgent is how QAi drives the phone, not something to test.
        if "WebDriverAgent" in bundle_id:
            continue
        apps.append({"id": bundle_id, "name": entry.get("name") or bundle_id})
    return sorted(apps, key=lambda a: a["name"].lower())
