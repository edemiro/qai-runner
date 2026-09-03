"""Device discovery for Android (adb) and iOS (libimobiledevice or devicectl)."""

import asyncio
import shutil
from typing import Dict, List


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

    out = await _run(["ideviceinstaller", "-u", udid, "-l"], timeout=20.0)
    apps = []
    for line in out.splitlines()[1:]:
        parts = [p.strip() for p in line.split(",")]
        if parts and "." in parts[0]:
            bundle_id = parts[0]
            name = parts[-1].strip('"') if len(parts) > 2 else bundle_id
            apps.append({"id": bundle_id, "name": name})
    return sorted(apps, key=lambda a: a["name"].lower())
