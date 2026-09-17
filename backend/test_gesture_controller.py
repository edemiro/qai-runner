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


class ScreenSize(unittest.IsolatedAsyncioTestCase):
    """The screen is what every gesture is measured against.

    XCUITest answers /window/rect and returns 404 for /window/size, which is
    the old JSONWP endpoint. Reading only the old one meant an iPhone fell
    through to the Android-shaped default of 1080x2400 while really being
    430x932 — so a scroll was computed to start at y=1800, far below a screen
    that ends at 932, and nothing moved. Measured on a real iPhone: 0% of the
    screen changed with the wrong size, 8% with the right one.
    """

    import appium_client as appium

    @staticmethod
    def _response(status, payload=None):
        response = MagicMock()
        response.status_code = status
        response.json.return_value = {"value": payload or {}}
        return response

    async def _size_from(self, answers):
        """`answers` maps the path suffix to what the device replies."""
        async def fake_get(path, timeout=10.0, base_url=None):
            for suffix, response in answers.items():
                if path.endswith(suffix):
                    return response
            return None

        with patch.object(self.appium, "get", fake_get):
            return await self.appium.get_window_size("s1")

    async def test_an_iphone_is_read_from_window_rect(self):
        size = await self._size_from({
            "window/rect": self._response(200, {"x": 0, "y": 0, "width": 430, "height": 932}),
            "window/size": self._response(404),
        })
        self.assertEqual(size, {"width": 430, "height": 932})

    async def test_a_device_that_only_serves_the_old_endpoint_still_works(self):
        size = await self._size_from({
            "window/rect": self._response(404),
            "window/size": self._response(200, {"width": 1440, "height": 3120}),
        })
        self.assertEqual(size, {"width": 1440, "height": 3120})

    async def test_a_silent_device_falls_back_rather_than_crashing(self):
        # The session is already in trouble here; a plausible size keeps the
        # caller from dividing by nothing.
        self.assertEqual(
            await self._size_from({}), {"width": 1080, "height": 2400},
        )

    async def test_a_rect_missing_its_dimensions_is_not_trusted(self):
        size = await self._size_from({
            "window/rect": self._response(200, {"x": 0, "y": 0}),
            "window/size": self._response(200, {"width": 430, "height": 932}),
        })
        self.assertEqual(size, {"width": 430, "height": 932})


class ScrollGeometry(unittest.IsolatedAsyncioTestCase):
    """A scroll has to land inside the screen it was measured against."""

    async def _swipe_for(self, width, height, direction="down"):
        sent = {}

        async def capture(session_id, x1, y1, x2, y2, duration_ms=500):
            sent.update(start=(x1, y1), end=(x2, y2), duration=duration_ms)
            return True

        with patch.object(MobileGestureController, "perform_swipe", capture):
            await MobileGestureController.perform_scroll("s1", direction, width, height)
        return sent

    async def test_a_scroll_stays_within_the_screen(self):
        swipe = await self._swipe_for(430, 932)
        for x, y in (swipe["start"], swipe["end"]):
            self.assertTrue(0 <= x <= 430, f"x={x} is off the screen")
            self.assertTrue(0 <= y <= 932, f"y={y} is off the screen")

    async def test_scrolling_down_drags_upwards(self):
        # Content moves up to reveal what is below it.
        swipe = await self._swipe_for(430, 932, "down")
        self.assertGreater(swipe["start"][1], swipe["end"][1])

    async def test_scrolling_up_drags_downwards(self):
        swipe = await self._swipe_for(430, 932, "up")
        self.assertLess(swipe["start"][1], swipe["end"][1])
