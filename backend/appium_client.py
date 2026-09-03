"""Async wrapper around the Appium/W3C REST API.

Everything here is async so a ten second auto-wait loop does not block the
FastAPI event loop the way the previous synchronous `requests` calls did.
"""

import httpx
from typing import Any, Dict, Optional

from config import APPIUM_HOST

_client: Optional[httpx.AsyncClient] = None


def get_client() -> httpx.AsyncClient:
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(base_url=APPIUM_HOST, timeout=30.0)
    return _client


async def close_client() -> None:
    global _client
    if _client is not None and not _client.is_closed:
        await _client.aclose()
    _client = None


async def _request(method: str, path: str, **kwargs) -> Optional[httpx.Response]:
    try:
        return await get_client().request(method, path, **kwargs)
    except Exception as exc:  # network error, Appium down, timeout
        print(f"[appium] {method} {path} failed: {exc}")
        return None


async def get(path: str, timeout: float = 10.0) -> Optional[httpx.Response]:
    return await _request("GET", path, timeout=timeout)


async def post(path: str, json: Any = None, timeout: float = 15.0) -> Optional[httpx.Response]:
    return await _request("POST", path, json=json if json is not None else {}, timeout=timeout)


async def delete(path: str, timeout: float = 15.0) -> Optional[httpx.Response]:
    return await _request("DELETE", path, timeout=timeout)


async def is_server_running() -> bool:
    res = await get("/status", timeout=2.0)
    return res is not None and res.status_code == 200


async def create_session(capabilities: Dict[str, Any]) -> httpx.Response:
    res = await post("/session", capabilities, timeout=120.0)
    if res is None:
        raise ConnectionError("Could not reach the Appium server on " + APPIUM_HOST)
    return res


async def delete_session(session_id: str) -> bool:
    res = await delete(f"/session/{session_id}")
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
    res = await get(f"/session/{session_id}/window/size", timeout=5.0)
    if res is not None and res.status_code == 200:
        val = res.json().get("value", {})
        width, height = val.get("width"), val.get("height")
        if width and height:
            return {"width": int(width), "height": int(height)}
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
