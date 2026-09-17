"""Async wrapper around the Appium/W3C REST API.

Everything here is async so a ten second auto-wait loop does not block the
FastAPI event loop the way the previous synchronous `requests` calls did.
"""

import httpx
from typing import Any, Dict, Optional

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
