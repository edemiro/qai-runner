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

import base64
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote

import httpx

from config import BROWSERSTACK_ACCESS_KEY, BROWSERSTACK_USERNAME

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


async def list_apps() -> List[Dict[str, Any]]:
    """Apps already uploaded to this account.

    An app has to be on BrowserStack before a device can install it, and the
    upload is done from their dashboard or CI — QAi only picks from what is
    there, so nothing here can spend the account's storage.
    """
    try:
        raw = await _get("/recent_apps")
    except httpx.HTTPStatusError as exc:
        # An account that has never uploaded an app answers 422 rather than an
        # empty list, which is not an error worth showing anyone.
        if exc.response.status_code == 422:
            return []
        raise
    apps = []
    for entry in raw if isinstance(raw, list) else []:
        app_url = entry.get("app_url")
        if not app_url:
            continue
        apps.append({
            "id": app_url,
            "name": entry.get("app_name") or app_url,
            "version": entry.get("app_version") or "",
            "uploadedAt": entry.get("uploaded_at") or "",
        })
    return apps


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
        # Real hardware, never an emulator: a test that passes on a simulated
        # device says less than the team needs it to.
        "realMobile": "true",
    }
    if os_version:
        bstack["osVersion"] = os_version

    always: Dict[str, Any] = {
        "platformName": "iOS" if platform.lower() == "ios" else "Android",
        "appium:automationName": "XCUITest" if platform.lower() == "ios" else "UiAutomator2",
        "bstack:options": bstack,
    }
    if app_id:
        always["appium:app"] = app_id
    return {"capabilities": {"alwaysMatch": always}}
