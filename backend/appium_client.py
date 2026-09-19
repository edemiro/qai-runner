"""Async wrapper around the Appium/W3C REST API.

Everything here is async so a ten second auto-wait loop does not block the
FastAPI event loop the way the previous synchronous `requests` calls did.
"""

import httpx
from typing import Any, Dict, List, Optional, Tuple

from config import APPIUM_HOST

_clients: Dict[str, httpx.AsyncClient] = {}

# Which hub each live session belongs to. A session on a cloud device answers
# at a different address than the local Appium server, and every later call —
# source, screenshot, tap — has to reach the same place the session was made.
# Empty for local sessions, which is the default and needs no bookkeeping.
_session_hubs: Dict[str, str] = {}


def get_client(base_url: str = APPIUM_HOST) -> httpx.AsyncClient:
    client = _clients.get(base_url)
    if client is None or client.is_closed:
        # Cloud hubs sit behind a queue while a device is booked, so they are
        # slower to answer than a phone on the desk.
        client = httpx.AsyncClient(base_url=base_url, timeout=60.0, follow_redirects=True)
        _clients[base_url] = client
    return client


def bind_session(session_id: str, base_url: str) -> None:
    """Remember where a session lives, so later calls reach the same hub."""
    if base_url and base_url != APPIUM_HOST:
        _session_hubs[session_id] = base_url


def release_session(session_id: str) -> None:
    _session_hubs.pop(session_id, None)


def _hub_for(path: str) -> str:
    """The address a request belongs to, read from the session in its path."""
    parts = path.strip("/").split("/")
    if len(parts) >= 2 and parts[0] == "session":
        return _session_hubs.get(parts[1], APPIUM_HOST)
    return APPIUM_HOST


async def close_client() -> None:
    for client in list(_clients.values()):
        if not client.is_closed:
            await client.aclose()
    _clients.clear()
    _session_hubs.clear()


async def _request(method: str, path: str, **kwargs) -> Optional[httpx.Response]:
    base_url = kwargs.pop("base_url", None) or _hub_for(path)
    try:
        return await get_client(base_url).request(method, path, **kwargs)
    except Exception as exc:  # network error, Appium down, timeout
        print(f"[appium] {method} {path} failed: {exc}")
        return None


async def get(path: str, timeout: float = 10.0, base_url: Optional[str] = None) -> Optional[httpx.Response]:
    return await _request("GET", path, timeout=timeout, base_url=base_url)


async def post(
    path: str, json: Any = None, timeout: float = 15.0, base_url: Optional[str] = None,
) -> Optional[httpx.Response]:
    return await _request(
        "POST", path, json=json if json is not None else {}, timeout=timeout, base_url=base_url,
    )


async def delete(path: str, timeout: float = 15.0) -> Optional[httpx.Response]:
    return await _request("DELETE", path, timeout=timeout)


async def is_server_running() -> bool:
    res = await get("/status", timeout=2.0)
    return res is not None and res.status_code == 200


async def execute(
    session_id: str, script: str, args: Optional[Dict[str, Any]] = None,
) -> Tuple[bool, Any]:
    """Run one `mobile:` command, returning (it worked, what it returned).

    A tuple rather than the value alone because the two cannot be told apart
    otherwise: `mobile: queryAppState` answers 0 for "not installed" and
    `mobile: pressButton` answers null on success, so a falsy value says
    nothing about whether the call reached the device.
    """
    res = await post(
        f"/session/{session_id}/execute/sync",
        {"script": script, "args": [args or {}]},
        timeout=15.0,
    )
    if res is None or res.status_code != 200:
        return False, None
    try:
        return True, res.json().get("value")
    except Exception:
        return True, None


async def alert_text(session_id: str) -> Optional[str]:
    """What the alert on screen says, or None when there is no alert."""
    res = await get(f"/session/{session_id}/alert/text", timeout=5.0)
    if res is None or res.status_code != 200:
        return None
    try:
        value = res.json().get("value")
    except Exception:
        return None
    return value if isinstance(value, str) else None


async def accept_alert(session_id: str, button_label: Optional[str] = None) -> bool:
    """Accept the system alert on screen, if there is one.

    Permission prompts (location, notifications, tracking) are OS dialogs, not
    the app's, and the driver can answer them without anyone — or any model —
    looking at the screen. True means an alert was there and was accepted;
    False means there was nothing to accept, which is the normal case.

    `button_label` names the button to press. The plain W3C accept takes an
    alert's default, and on a permission prompt the default is not reliably
    the permissive answer — "Allow Once" and "Don't Allow" sit side by side.
    """
    if button_label:
        ok, _ = await execute(
            session_id, "mobile: alert", {"action": "accept", "buttonLabel": button_label},
        )
        if ok:
            return True
    res = await post(f"/session/{session_id}/alert/accept", {}, timeout=5.0)
    return res is not None and res.status_code == 200


async def alert_buttons(session_id: str, platform: str) -> List[str]:
    """The buttons on the alert currently up, or [] when there is none."""
    if platform.lower() != "ios":
        return []
    ok, value = await execute(session_id, "mobile: alert", {"action": "getButtons"})
    if not ok or not isinstance(value, list):
        return []
    return [str(item) for item in value if item]


async def query_app_state(session_id: str, app_id: str) -> Optional[int]:
    """How the app stands with the OS, on Appium's 0-4 scale.

    4 is "running in the foreground", which is the only state that means the
    tester is looking at the app. None means the question could not be asked —
    an old driver, or a session that has gone away.
    """
    res = await post(
        f"/session/{session_id}/appium/device/app_state", {"appId": app_id}, timeout=10.0,
    )
    if res is not None and res.status_code == 200:
        try:
            value = res.json().get("value")
            if isinstance(value, int):
                return value
        except Exception:
            pass
    ok, value = await execute(session_id, "mobile: queryAppState", {"bundleId": app_id, "appId": app_id})
    return value if ok and isinstance(value, int) else None


async def activate_app(session_id: str, app_id: str) -> bool:
    """Bring the app to the foreground, launching it if it is not running."""
    res = await post(
        f"/session/{session_id}/appium/device/activate_app", {"appId": app_id}, timeout=20.0,
    )
    if res is not None and res.status_code == 200:
        return True
    ok, _ = await execute(
        session_id, "mobile: activateApp", {"bundleId": app_id, "appId": app_id},
    )
    return ok


async def active_app_info(session_id: str, platform: str) -> Optional[str]:
    """The id of whatever is on screen right now, when the driver will say."""
    if platform.lower() == "ios":
        ok, value = await execute(session_id, "mobile: activeAppInfo")
        if ok and isinstance(value, dict):
            return value.get("bundleId")
        return None
    res = await get(f"/session/{session_id}/appium/device/current_package", timeout=10.0)
    if res is None or res.status_code != 200:
        return None
    try:
        value = res.json().get("value")
    except Exception:
        return None
    return value if isinstance(value, str) else None


async def update_settings(session_id: str, settings: Dict[str, Any]) -> bool:
    res = await post(f"/session/{session_id}/appium/settings", {"settings": settings}, timeout=10.0)
    return res is not None and res.status_code == 200


async def create_session(
    capabilities: Dict[str, Any], base_url: Optional[str] = None,
) -> httpx.Response:
    # A cloud device can sit in a queue before it is handed over, which takes
    # longer than any local start-up.
    timeout = 300.0 if base_url else 120.0
    res = await post("/session", capabilities, timeout=timeout, base_url=base_url)
    if res is None:
        where = "BrowserStack" if base_url else APPIUM_HOST
        raise ConnectionError(f"Could not reach the Appium server on {where}")
    return res


async def delete_session(session_id: str) -> bool:
    res = await delete(f"/session/{session_id}")
    release_session(session_id)
    return res is not None and res.status_code == 200


async def get_source(session_id: str) -> Optional[str]:
    res = await get(f"/session/{session_id}/source", timeout=20.0)
    if res is not None and res.status_code == 200:
        return res.json().get("value")
    return None


async def get_screenshot(session_id: str) -> Optional[str]:
    res = await get(f"/session/{session_id}/screenshot", timeout=15.0)
    if res is not None and res.status_code == 200:
        return res.json().get("value")
    return None


async def get_platform(session_id: str, cache: Dict[str, str]) -> str:
    if session_id in cache:
        return cache[session_id]
    res = await get(f"/session/{session_id}", timeout=5.0)
    if res is not None and res.status_code == 200:
        caps = res.json().get("value", {})
        platform = caps.get("platformName") or caps.get("capabilities", {}).get("platformName") or "Android"
        cache[session_id] = platform
        return platform
    return "Android"


async def get_window_size(session_id: str) -> Dict[str, int]:
    """The screen, in the units gestures are expressed in.

    `/window/rect` is the W3C endpoint and the only one XCUITest answers —
    `/window/size` is the old JSONWP one and returns 404 on iOS. That 404 used
    to fall through to the Android-shaped default below, so every gesture on an
    iPhone was computed against a 1080x2400 screen that was really 430x932:
    a scroll started and ended past the bottom edge and the screen never moved.
    """
    for path in ("window/rect", "window/size"):
        res = await get(f"/session/{session_id}/{path}", timeout=5.0)
        if res is not None and res.status_code == 200:
            val = res.json().get("value", {})
            width, height = val.get("width"), val.get("height")
            if width and height:
                return {"width": int(width), "height": int(height)}
    # Only when the device answers neither, which means the session is already
    # in trouble; a plausible size keeps the caller from dividing by nothing.
    return {"width": 1080, "height": 2400}


def extract_element_id(payload: Dict[str, Any]) -> Optional[str]:
    """Appium returns the element handle under a versioned key such as
    'element-6066-11e4-a52e-4f735466cecf' or the legacy 'ELEMENT'."""
    for key, value in payload.items():
        if "element" in key.lower():
            return value
    return None


async def find_element_by_xpath(session_id: str, xpath: str) -> Optional[str]:
    res = await post(f"/session/{session_id}/element", {"using": "xpath", "value": xpath}, timeout=10.0)
    if res is not None and res.status_code == 200:
        return extract_element_id(res.json().get("value", {}))
    return None


async def get_active_element(session_id: str) -> Optional[str]:
    res = await get(f"/session/{session_id}/element/active", timeout=5.0)
    if res is not None and res.status_code == 200:
        return extract_element_id(res.json().get("value", {}))
    return None
