"""Low-level touch and key gestures, expressed as W3C action payloads.

All calls are async and go through appium_client so they never block the event
loop. Key events use Appium 2's `mobile:` execute-script endpoints first, with
the legacy JSONWP routes only as a fallback.
"""

from typing import Any, Dict

import appium_client as appium

ANDROID_KEYCODES = {
    "home": 3,
    "back": 4,
    "menu": 82,
    "recents": 187,
    "app_switch": 187,
    "enter": 66,
    "power": 26,
    "volume_up": 24,
    "volume_down": 25,
    "delete": 67,
    "search": 84,
}

IOS_BUTTONS = {
    "home": "home",
    "volume_up": "volumeUp",
    "volume_down": "volumeDown",
}


def _pointer(actions: list) -> Dict[str, Any]:
    return {
        "actions": [
            {
                "type": "pointer",
                "id": "finger1",
                "parameters": {"pointerType": "touch"},
                "actions": actions,
            }
        ]
    }


class MobileGestureController:
    """Platform-aware touchscreen gestures and key events."""

    @staticmethod
    async def _send_actions(session_id: str, payload: Dict[str, Any]) -> bool:
        res = await appium.post(f"/session/{session_id}/actions", payload)
        if res is None:
            return False
        if res.status_code != 200:
            print(f"[gesture] W3C actions failed ({res.status_code}): {res.text[:300]}")
            return False
        return True

    @staticmethod
    async def _execute(session_id: str, script: str, args: Any) -> bool:
        res = await appium.post(
            f"/session/{session_id}/execute/sync",
            {"script": script, "args": args if isinstance(args, list) else [args]},
        )
        return res is not None and res.status_code == 200

    @classmethod
    async def perform_tap(cls, session_id: str, x: int, y: int) -> bool:
        return await cls._send_actions(session_id, _pointer([
            {"type": "pointerMove", "duration": 0, "x": x, "y": y},
            {"type": "pointerDown", "button": 0},
            {"type": "pause", "duration": 60},
            {"type": "pointerUp", "button": 0},
        ]))

    @classmethod
    async def perform_double_tap(cls, session_id: str, x: int, y: int) -> bool:
        return await cls._send_actions(session_id, _pointer([
            {"type": "pointerMove", "duration": 0, "x": x, "y": y},
            {"type": "pointerDown", "button": 0},
            {"type": "pointerUp", "button": 0},
            {"type": "pause", "duration": 100},
            {"type": "pointerDown", "button": 0},
            {"type": "pointerUp", "button": 0},
        ]))

    @classmethod
    async def perform_long_press(cls, session_id: str, x: int, y: int, duration_ms: int = 1000) -> bool:
        return await cls._send_actions(session_id, _pointer([
            {"type": "pointerMove", "duration": 0, "x": x, "y": y},
            {"type": "pointerDown", "button": 0},
            {"type": "pause", "duration": duration_ms},
            {"type": "pointerUp", "button": 0},
        ]))

    @classmethod
    async def perform_swipe(cls, session_id: str, x1: int, y1: int, x2: int, y2: int, duration_ms: int = 500) -> bool:
        return await cls._send_actions(session_id, _pointer([
            {"type": "pointerMove", "duration": 0, "x": x1, "y": y1},
            {"type": "pointerDown", "button": 0},
            {"type": "pause", "duration": 60},
            {"type": "pointerMove", "duration": duration_ms, "origin": "viewport", "x": x2, "y": y2},
            {"type": "pointerUp", "button": 0},
        ]))

    @classmethod
    async def perform_scroll(
        cls, session_id: str, direction: str, width: int, height: int,
        origin_x: int = 0, origin_y: int = 0,
    ) -> bool:
        """Scroll one 'page' in the given direction, within the rectangle at
        (origin_x, origin_y) sized (width, height).

        Defaults to the whole screen. A caller scrolling a specific container
        (a dropdown, a picker wheel) passes that element's own bounds instead —
        a full-screen swipe can miss a small container or scroll whatever is
        behind it rather than the container itself.
        """
        mid_x, mid_y = origin_x + width // 2, origin_y + height // 2
        near, far = 0.25, 0.75

        direction = (direction or "down").lower()
        if direction == "down":
            start, end = (mid_x, origin_y + int(height * far)), (mid_x, origin_y + int(height * near))
        elif direction == "up":
            start, end = (mid_x, origin_y + int(height * near)), (mid_x, origin_y + int(height * far))
        elif direction == "left":
            start, end = (origin_x + int(width * far), mid_y), (origin_x + int(width * near), mid_y)
        elif direction == "right":
            start, end = (origin_x + int(width * near), mid_y), (origin_x + int(width * far), mid_y)
        else:
            return False

        return await cls.perform_swipe(session_id, start[0], start[1], end[0], end[1], duration_ms=700)

    @classmethod
    async def perform_key_event(cls, session_id: str, platform: str, key_name: str) -> bool:
        """Hardware / system navigation keys, per platform."""
        plat = (platform or "").lower()
        key = (key_name or "").lower()

        if plat == "android":
            keycode = ANDROID_KEYCODES.get(key)
            if keycode is None:
                print(f"[gesture] Unsupported Android key: {key_name}")
                return False

            # Appium 2 preferred route.
            if await cls._execute(session_id, "mobile: pressKey", {"keycode": keycode}):
                return True

            # Legacy JSONWP route, still served by uiautomator2 for compatibility.
            res = await appium.post(
                f"/session/{session_id}/appium/device/press_keycode", {"keycode": keycode}
            )
            return res is not None and res.status_code == 200

        if plat == "ios":
            button = IOS_BUTTONS.get(key)
            if button:
                return await cls._execute(session_id, "mobile: pressButton", {"name": button})
            if key == "back":
                # iOS has no back button; the edge-swipe gesture is the equivalent.
                size = await appium.get_window_size(session_id)
                mid_y = size["height"] // 2
                return await cls.perform_swipe(session_id, 8, mid_y, int(size["width"] * 0.6), mid_y, 400)
            if key == "enter":
                return await cls.perform_type_text(session_id, "\n")
            print(f"[gesture] Unsupported iOS key: {key_name}")
            return False

        return False

    @classmethod
    async def perform_type_text(cls, session_id: str, text: str) -> bool:
        """Type into the focused field, with layered fallbacks."""
        # Strategy 1: send keys to the active element (W3C).
        element_id = await appium.get_active_element(session_id)
        if element_id:
            res = await appium.post(
                f"/session/{session_id}/element/{element_id}/value",
                {"text": text, "value": list(text)},
            )
            if res is not None and res.status_code == 200:
                return True

        # Strategy 2: W3C key actions against the focused field.
        key_actions = []
        for char in text:
            key_actions.append({"type": "keyDown", "value": char})
            key_actions.append({"type": "keyUp", "value": char})

        if await cls._send_actions(
            session_id, {"actions": [{"type": "key", "id": "keyboard", "actions": key_actions}]}
        ):
            return True

        # Strategy 3: legacy /keys endpoint, still present on some drivers.
        res = await appium.post(f"/session/{session_id}/keys", {"value": list(text)})
        return res is not None and res.status_code == 200

    @classmethod
    async def hide_keyboard(cls, session_id: str) -> bool:
        return await cls._execute(session_id, "mobile: hideKeyboard", {})
