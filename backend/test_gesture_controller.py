import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from gesture_controller import MobileGestureController


def _ok(status_code=200):
    response = MagicMock()
    response.status_code = status_code
    response.text = ""
    return response


class TestGestureController(unittest.IsolatedAsyncioTestCase):

    async def test_perform_tap(self):
        with patch("appium_client.post", new=AsyncMock(return_value=_ok())) as post:
            self.assertTrue(await MobileGestureController.perform_tap("s1", 500, 600))

        path, payload = post.call_args[0]
        self.assertEqual(path, "/session/s1/actions")
        actions = payload["actions"][0]["actions"]
        self.assertEqual(actions[0]["type"], "pointerMove")
        self.assertEqual(actions[0]["x"], 500)
        self.assertEqual(actions[0]["y"], 600)
        self.assertEqual(actions[1]["type"], "pointerDown")
        self.assertEqual(actions[-1]["type"], "pointerUp")

    async def test_perform_double_tap(self):
        with patch("appium_client.post", new=AsyncMock(return_value=_ok())) as post:
            self.assertTrue(await MobileGestureController.perform_double_tap("s1", 100, 200))

        actions = post.call_args[0][1]["actions"][0]["actions"]
        self.assertEqual([a["type"] for a in actions[1:]],
                         ["pointerDown", "pointerUp", "pause", "pointerDown", "pointerUp"])

    async def test_perform_long_press_holds_for_duration(self):
        with patch("appium_client.post", new=AsyncMock(return_value=_ok())) as post:
            self.assertTrue(await MobileGestureController.perform_long_press("s1", 100, 200, 1500))

        actions = post.call_args[0][1]["actions"][0]["actions"]
        pause = next(a for a in actions if a["type"] == "pause")
        self.assertEqual(pause["duration"], 1500)

    async def test_perform_swipe(self):
        with patch("appium_client.post", new=AsyncMock(return_value=_ok())) as post:
            self.assertTrue(await MobileGestureController.perform_swipe("s1", 100, 200, 300, 400, 800))

        actions = post.call_args[0][1]["actions"][0]["actions"]
        move = [a for a in actions if a["type"] == "pointerMove"][-1]
        self.assertEqual((move["x"], move["y"], move["duration"]), (300, 400, 800))

    async def test_scroll_down_swipes_upward(self):
        with patch.object(MobileGestureController, "perform_swipe", new=AsyncMock(return_value=True)) as swipe:
            self.assertTrue(await MobileGestureController.perform_scroll("s1", "down", 1080, 2400))

        _session, _x1, start_y, _x2, end_y = swipe.call_args[0]
        self.assertGreater(start_y, end_y)

    async def test_scroll_rejects_unknown_direction(self):
        self.assertFalse(await MobileGestureController.perform_scroll("s1", "sideways", 1080, 2400))

    async def test_android_key_prefers_mobile_press_key(self):
        with patch("appium_client.post", new=AsyncMock(return_value=_ok())) as post:
            self.assertTrue(await MobileGestureController.perform_key_event("s1", "Android", "home"))

        path, payload = post.call_args[0]
        self.assertEqual(path, "/session/s1/execute/sync")
        self.assertEqual(payload["script"], "mobile: pressKey")
        self.assertEqual(payload["args"][0]["keycode"], 3)

    async def test_android_key_falls_back_to_legacy_route(self):
        # First call (mobile: pressKey) fails, second (legacy) succeeds.
        post = AsyncMock(side_effect=[_ok(404), _ok(200)])
        with patch("appium_client.post", new=post):
            self.assertTrue(await MobileGestureController.perform_key_event("s1", "Android", "back"))

        path, payload = post.call_args_list[1][0]
        self.assertEqual(path, "/session/s1/appium/device/press_keycode")
        self.assertEqual(payload["keycode"], 4)

    async def test_android_key_rejects_unknown_name(self):
        with patch("appium_client.post", new=AsyncMock(return_value=_ok())):
            self.assertFalse(await MobileGestureController.perform_key_event("s1", "Android", "teleport"))

    async def test_ios_home_uses_press_button(self):
        with patch("appium_client.post", new=AsyncMock(return_value=_ok())) as post:
            self.assertTrue(await MobileGestureController.perform_key_event("s1", "iOS", "home"))

        path, payload = post.call_args[0]
        self.assertEqual(path, "/session/s1/execute/sync")
        self.assertEqual(payload["script"], "mobile: pressButton")
        self.assertEqual(payload["args"][0]["name"], "home")

    async def test_type_text_uses_active_element_first(self):
        with patch("appium_client.get_active_element", new=AsyncMock(return_value="el-42")), \
             patch("appium_client.post", new=AsyncMock(return_value=_ok())) as post:
            self.assertTrue(await MobileGestureController.perform_type_text("s1", "Hello"))

        path, payload = post.call_args[0]
        self.assertEqual(path, "/session/s1/element/el-42/value")
        self.assertEqual(payload["value"], ["H", "e", "l", "l", "o"])

    async def test_type_text_falls_back_to_key_actions(self):
        post = AsyncMock(side_effect=[_ok(404), _ok(200)])
        with patch("appium_client.get_active_element", new=AsyncMock(return_value="el-42")), \
             patch("appium_client.post", new=post):
            self.assertTrue(await MobileGestureController.perform_type_text("s1", "Hi"))

        path, payload = post.call_args_list[1][0]
        self.assertEqual(path, "/session/s1/actions")
        self.assertEqual(payload["actions"][0]["type"], "key")


if __name__ == "__main__":
    unittest.main()
