"""Real devices from BrowserStack, driven by the same agent as a local one.

QAi already speaks Appium's REST API, and BrowserStack App Automate is an
Appium hub — so a cloud device is not a second kind of target, it is the same
session pointed at a different address. That is the whole design here: this
module produces a device list and a capability set, and everything downstream
(the agent loop, the inspector, the reports) stays as it is.

Two things do differ from a phone on the desk, and both are handled here.
A cloud device is picked by model and OS version rather than by a udid, so the
"udid" QAi carries for one is a synthetic id built from those. And the app under
test has to already be uploaded to BrowserStack, which hands back a `bs://…`
id — that id is what goes in the capabilities where a local session would name
an installed package.
"""

import asyncio
import base64
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote

import httpx

from config import (
    BROWSERSTACK_ACCESS_KEY, BROWSERSTACK_IDLE_TIMEOUT, BROWSERSTACK_RESIGN_APP,
    BROWSERSTACK_USERNAME, MOBILE_AUTO_PERMISSIONS,
)

API_ROOT = "https://api-cloud.browserstack.com/app-automate"
HUB_HOST = "hub-cloud.browserstack.com"

# The device id QAi uses in place of a udid. Kept parseable so a session can be
# rebuilt from it — a run recorded last week names a device, not a handle that
# expired with the session.
ID_PREFIX = "bs"


def credentials() -> Tuple[str, str]:
    """Read at call time, not at import: the user can save these in Settings
    while the server is running and expects the next click to use them."""
    import os

    return (
        os.environ.get("BROWSERSTACK_USERNAME", BROWSERSTACK_USERNAME).strip(),
        os.environ.get("BROWSERSTACK_ACCESS_KEY", BROWSERSTACK_ACCESS_KEY).strip(),
    )


def configured() -> bool:
    user, key = credentials()
    return bool(user and key)


def hub_url() -> str:
    """The Appium endpoint for a cloud session.

    The credentials travel in the URL because that is the only place the W3C
    protocol has for them, and every path built from this one inherits them.
    """
    user, key = credentials()
    return f"https://{quote(user, safe='')}:{quote(key, safe='')}@{HUB_HOST}/wd/hub"


def _auth_header() -> Dict[str, str]:
    user, key = credentials()
    token = base64.b64encode(f"{user}:{key}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


def device_id(name: str, os_version: str) -> str:
    return f"{ID_PREFIX}:{name}:{os_version}"


def parse_device_id(udid: str) -> Optional[Tuple[str, str]]:
    """The model and OS version a synthetic id was built from, or None if this
    is an ordinary local udid."""
    if not udid.startswith(f"{ID_PREFIX}:"):
        return None
    parts = udid.split(":", 2)
    if len(parts) != 3:
        return None
    return parts[1], parts[2]


def is_cloud_device(udid: str) -> bool:
    return parse_device_id(udid) is not None


async def _get(path: str, timeout: float = 20.0) -> Any:
    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.get(f"{API_ROOT}{path}", headers=_auth_header())
    if response.status_code in (401, 403):
        raise PermissionError(
            "BrowserStack rejected these credentials. Check the username and "
            "access key in Settings."
        )
    response.raise_for_status()
    return response.json()


async def list_devices() -> List[Dict[str, Any]]:
    """The devices this account can actually book, in QAi's own device shape.

    Returned in the same shape as a local phone so the picker, the session and
    the reports do not need to know which kind they are looking at — only the
    `source` field says, and that is there to be shown, not branched on.
    """
    raw = await _get("/devices.json")
    devices: List[Dict[str, Any]] = []
    for entry in raw if isinstance(raw, list) else []:
        name = str(entry.get("device") or "").strip()
        version = str(entry.get("os_version") or "").strip()
        platform = "iOS" if str(entry.get("os") or "").lower() == "ios" else "Android"
        if not name or not version:
            continue
        devices.append({
            "udid": device_id(name, version),
            "platform": platform,
            "version": version,
            "name": name,
            "isEmulator": not bool(entry.get("realMobile", True)),
            "source": "browserstack",
        })
    # Newest OS first within a model, so the list opens on what teams test on.
    devices.sort(key=lambda d: (d["platform"], d["name"], _version_key(d["version"])))
    return devices


def _version_key(version: str) -> Tuple[int, ...]:
    parts = []
    for chunk in version.split("."):
        digits = "".join(ch for ch in chunk if ch.isdigit())
        parts.append(-int(digits) if digits else 0)
    return tuple(parts)


_apps_cache: Dict[str, Any] = {"at": 0.0, "value": None}
_APPS_TTL = 60.0
_apps_lock = asyncio.Lock()


def _apps_cache_fresh() -> bool:
    import time
    return _apps_cache["value"] is not None and time.monotonic() - _apps_cache["at"] < _APPS_TTL


async def list_apps() -> List[Dict[str, Any]]:
    """Apps already uploaded to this account.

    An app has to be on BrowserStack before a device can install it, and the
    upload is done from their dashboard or CI — QAi only picks from what is
    there, so nothing here can spend the account's storage.

    Cached briefly, behind a single-flight lock: the device picker asks once per
    card, so a screen of cloud devices fires this identical account-wide request
    a dozen times at once on load. Without the lock every one of them misses the
    still-empty cache and hits BrowserStack, which times most of them out; with
    it, the first fills the cache and the rest read it.
    """
    if _apps_cache_fresh():
        return _apps_cache["value"]
    async with _apps_lock:
        # Filled while waiting for the lock — the whole point of the lock.
        if _apps_cache_fresh():
            return _apps_cache["value"]
        return await _fetch_apps()


async def _fetch_apps() -> List[Dict[str, Any]]:
    """Every app the account can install, newest first.

    Uploads belong to the team (group), not to whoever is logged in: a build
    the release engineer pushed is exactly the one a tester wants to run, and
    the per-user list does not show it. So the group list is read first and
    the user's own list merged in behind it. Either may answer "no results"
    (a 422, or a message object) rather than an empty list.
    """
    import time

    seen: set = set()
    apps: List[Dict[str, Any]] = []
    # The default page is the ten newest uploads; a team that pushes several
    # builds a day buries last month's REG package well below that.
    for path in ("/recent_group_apps?limit=100", "/recent_apps?limit=100"):
        try:
            raw = await _get(path)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 422:
                continue
            raise
        for entry in raw if isinstance(raw, list) else []:
            app_url = entry.get("app_url")
            if not app_url or app_url in seen:
                continue
            seen.add(app_url)
            apps.append({
                "id": app_url,
                "name": entry.get("app_name") or app_url,
                "version": entry.get("app_version") or "",
                "uploadedAt": entry.get("uploaded_at") or "",
            })
    apps.sort(key=lambda a: a["uploadedAt"], reverse=True)
    _apps_cache.update(at=time.monotonic(), value=apps)
    return apps


def _looks_like_an_app_id(value: str) -> bool:
    """Is this something a device could actually launch?

    Two shapes are real: a `bs://` upload handle, and a reverse-DNS bundle id
    or package. A bare word is a label that got sent instead of the id behind
    it — "ThyReg" rather than the `bs://…` it stands for — and the session it
    produces fails with a capabilities error that names neither.
    """
    value = (value or "").strip()
    if value.startswith("bs://"):
        return True
    return "." in value and " " not in value


def capabilities(
    *,
    udid: str,
    platform: str,
    app_id: Optional[str] = None,
    name: Optional[str] = None,
    project: str = "QAi Runner",
    build: str = "QAi",
) -> Dict[str, Any]:
    """W3C capabilities for one cloud session.

    A cloud device is booked by model and OS version, so those replace the udid
    a local session matches on. Everything else is deliberately the same as the
    local capability set — the agent must not be able to tell the difference.
    """
    parsed = parse_device_id(udid)
    device_name, os_version = parsed if parsed else (name or udid, "")

    user, key = credentials()
    bstack: Dict[str, Any] = {
        "userName": user,
        "accessKey": key,
        "deviceName": device_name,
        "projectName": project,
        "buildName": build,
        "sessionName": name or device_name,
        # Leave the build's own signature alone. Re-signing is what stopped
        # every iOS run before this: it replaces the team prefix, the shared
        # keychain group and the app groups named after it stop matching, and
        # the app exits on the -34018 from its first keychain read. See the
        # note on BROWSERSTACK_RESIGN_APP.
        "resignApp": BROWSERSTACK_RESIGN_APP,
        # BrowserStack ends a session that has been quiet for 90 seconds, and
        # an agent thinking about a full screen takes longer than that — so a
        # run would lose the device in the middle of a scenario.
        "idleTimeout": BROWSERSTACK_IDLE_TIMEOUT,
    }
    if os_version:
        bstack["osVersion"] = os_version

    # There is deliberately no `realMobile` here. It belongs to Automate, where
    # it asks for a phone to run a browser on; this is App Automate, where the
    # device is already named by deviceName and osVersion. Sending it was seen
    # to route a session to a desktop Chrome instead of an iPhone, which then
    # answered every Appium command with "unknown command".

    is_ios = platform.lower() == "ios"
    always: Dict[str, Any] = {
        "platformName": "iOS" if is_ios else "Android",
        "appium:automationName": "XCUITest" if is_ios else "UiAutomator2",
        "bstack:options": bstack,
    }
    if MOBILE_AUTO_PERMISSIONS:
        # OS permission prompts are answered by the driver, not by the agent
        # looking at the screen: iOS accepts each alert as it appears, Android
        # grants the manifest permissions at install so no dialog ever shows.
        if is_ios:
            always["appium:autoAcceptAlerts"] = True
        else:
            always["appium:autoGrantPermissions"] = True
    if app_id and not _looks_like_an_app_id(app_id):
        # A label that leaked through instead of the id behind it. The picker
        # sent "ThyReg" for a while, because an environment row has no `id`
        # field and the option fell back to its own text; BrowserStack then
        # refused the session with a capabilities error naming neither. Better
        # to say which value was wrong than to send it and read the wreckage.
        raise ValueError(
            f"{app_id!r} is not an app id. A build is either a bs:// handle "
            f"BrowserStack installs, or the bundle id / package of an app "
            f"already on the device."
        )
    if app_id:
        # A bs:// handle is an app BrowserStack installs for the session; anything
        # else is the id of an app already on the device — a bundle id on iOS
        # (com.apple.Preferences, thy.mobile…), a package on Android — launched
        # the same way a physical device would, without an upload.
        if app_id.startswith("bs://"):
            always["appium:app"] = app_id
        elif is_ios:
            always["appium:bundleId"] = app_id
        else:
            always["appium:appPackage"] = app_id
            always["appium:appActivity"] = ""
            always["appium:appWaitActivity"] = "*"
    elif is_ios:
        # No app chosen: attach to the home screen instead of letting the session
        # fall back to launching Safari, so the device waits on springboard until
        # an app id is given.
        always["appium:bundleId"] = "com.apple.springboard"
    return {"capabilities": {"alwaysMatch": always}}
