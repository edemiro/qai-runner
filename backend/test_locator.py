import unittest
from unittest.mock import AsyncMock, patch

import locator
from mobile_dom import MobileDOMManager

SINGLE_BUTTON = """
<hierarchy rotation="0">
    <android.widget.Button bounds="[100,100][300,200]" displayed="true" text="Confirm" resource-id="confirm_btn"/>
</hierarchy>
"""

SHIFTED_BUTTON = """
<hierarchy rotation="0">
    <android.widget.FrameLayout bounds="[0,0][1080,2400]" displayed="true">
        <android.widget.Button bounds="[150,150][350,250]" displayed="true" text="Confirm" resource-id="confirm_btn"/>
    </android.widget.FrameLayout>
</hierarchy>
"""

# One "Delete" row, captured before the list grew.
ONE_DELETE_ROW = """
<hierarchy rotation="0">
    <android.widget.FrameLayout bounds="[0,0][1080,2400]" displayed="true">
        <android.widget.Button bounds="[0,100][1080,200]" displayed="true" text="Delete"/>
    </android.widget.FrameLayout>
</hierarchy>
"""

# The same list after a second identical row appeared and the container changed,
# so no candidate keeps the original xpath. Nothing distinguishes the two rows.
TWO_IDENTICAL_ROWS = """
<hierarchy rotation="0">
    <android.widget.LinearLayout bounds="[0,0][1080,2400]" displayed="true">
        <android.widget.Button bounds="[0,100][1080,200]" displayed="true" text="Delete"/>
        <android.widget.Button bounds="[0,300][1080,400]" displayed="true" text="Delete"/>
    </android.widget.LinearLayout>
</hierarchy>
"""


def _manager(xml: str) -> MobileDOMManager:
    return MobileDOMManager(xml, "Android", 1080, 2400)


class TestSnapshotStore(unittest.TestCase):
    def setUp(self):
        locator.snapshots = locator.SnapshotStore()

    def test_snapshot_is_addressable_by_id(self):
        first = locator.snapshots.add("s1", _manager(SINGLE_BUTTON))
        second = locator.snapshots.add("s1", _manager(SHIFTED_BUTTON))

        self.assertIs(locator.snapshots.get("s1", first.snapshot_id), first)
        self.assertIs(locator.snapshots.get("s1", second.snapshot_id), second)
        self.assertIs(locator.snapshots.latest("s1"), second)

    def test_unknown_snapshot_id_is_not_silently_swapped(self):
        """A stale id must not fall back to the newest tree — that was the race
        that let an action land on a renumbered element."""
        locator.snapshots.add("s1", _manager(SINGLE_BUTTON))
        self.assertIsNone(locator.snapshots.get("s1", "does-not-exist"))

    def test_ring_buffer_is_bounded(self):
        for _ in range(locator.SNAPSHOTS_PER_SESSION + 5):
            locator.snapshots.add("s1", _manager(SINGLE_BUTTON))
        self.assertEqual(len(locator.snapshots._by_session["s1"]), locator.SNAPSHOTS_PER_SESSION)


class TestElementIds(unittest.TestCase):
    def test_ids_are_stable_across_serializers(self):
        """Both serializers used to reset the counter, so the frontend and the
        model could end up with different ids for the same node."""
        manager = _manager(SHIFTED_BUTTON)
        inspector_tree = manager.get_optimized_tree()
        llm_tree = manager.get_optimized_tree_for_llm()
        self.assertEqual(inspector_tree["elementId"], llm_tree["elementId"])
        self.assertEqual(manager.elements_by_id[inspector_tree["elementId"]].text, "Confirm")

    def test_inspector_payload_carries_role_and_id(self):
        manager = _manager(SINGLE_BUTTON)
        tree = manager.get_optimized_tree()
        self.assertEqual(tree["role"], "button")
        self.assertEqual(tree["id"], "confirm_btn")
        self.assertEqual(tree["nativeClass"], "android.widget.Button")


class TestResolve(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        locator.snapshots = locator.SnapshotStore()

    async def _resolve_against(self, cached_xml, current_xml, **kwargs):
        cached = locator.snapshots.add("s1", _manager(cached_xml))
        current = _manager(current_xml)
        with patch.object(locator, "capture_snapshot", new=AsyncMock(return_value=current)):
            return await locator.resolve(
                "s1", {}, snapshot_id=cached.snapshot_id, timeout=1.0, poll_interval=0.05, **kwargs
            )

    async def test_finds_element_that_moved_in_the_tree(self):
        resolved = await self._resolve_against(SINGLE_BUTTON, SHIFTED_BUTTON, element_id="el_1")
        self.assertIsNotNone(resolved)
        self.assertEqual(resolved.element.text, "Confirm")
        self.assertEqual(resolved.element.bounds["cx"], 250)

    async def test_exact_xpath_still_resolves(self):
        current = _manager(SINGLE_BUTTON)
        with patch.object(locator, "capture_snapshot", new=AsyncMock(return_value=current)):
            resolved = await locator.resolve(
                "s1", {}, xpath="/hierarchy[1]/android.widget.Button[1]",
                timeout=1.0, poll_interval=0.05,
            )
        self.assertIsNotNone(resolved)
        self.assertEqual(resolved.element.text, "Confirm")

    async def test_ambiguous_target_is_reported_not_guessed(self):
        """Two indistinguishable rows must raise rather than let document order
        silently pick one of them."""
        with self.assertRaises(locator.AmbiguousElementError) as ctx:
            await self._resolve_against(ONE_DELETE_ROW, TWO_IDENTICAL_ROWS, element_id="el_2")
        self.assertIn("Ambiguous", str(ctx.exception))

    async def test_unchanged_xpath_disambiguates_identical_siblings(self):
        """When the original xpath still resolves, an identical sibling is not
        enough to call the match ambiguous."""
        resolved = await self._resolve_against(TWO_IDENTICAL_ROWS, TWO_IDENTICAL_ROWS, element_id="el_2")
        self.assertIsNotNone(resolved)
        self.assertEqual(resolved.element.bounds["y1"], 100)

    async def test_missing_element_times_out_instead_of_matching_anything(self):
        resolved = await self._resolve_against(
            SINGLE_BUTTON,
            '<hierarchy rotation="0"><android.widget.TextView bounds="[0,0][10,10]" text="Nothing here"/></hierarchy>',
            element_id="el_1",
        )
        self.assertIsNone(resolved)


class TestSemanticSimilarity(unittest.TestCase):
    def test_matching_identity_beats_matching_position(self):
        cached = _manager(SINGLE_BUTTON).elements_by_id["el_1"]
        shifted = _manager(SHIFTED_BUTTON).elements_by_id["el_2"]

        wrong = _manager(
            '<hierarchy rotation="0">'
            '<android.widget.Button bounds="[100,100][300,200]" displayed="true" text="Cancel" resource-id="cancel_btn"/>'
            "</hierarchy>"
        ).elements_by_id["el_1"]

        score_correct = locator.calculate_semantic_similarity(cached, shifted)
        score_wrong = locator.calculate_semantic_similarity(cached, wrong)

        self.assertGreater(score_correct, score_wrong)
        self.assertGreaterEqual(score_correct, locator.ACCEPTANCE_THRESHOLD)
        self.assertLess(score_wrong, locator.ACCEPTANCE_THRESHOLD)


if __name__ == "__main__":
    unittest.main()


class AReplayFindsWhatItNamed(unittest.IsolatedAsyncioTestCase):
    """A recording carries a locator; the screen it replays onto has moved.

    Recordings used to carry the element's position in the view tree. On the
    Android one-way set every element-addressed replay failed — same build,
    same device, same session — because a banner or an animation frame moves
    every path beneath it. They carry what the element is now, and both shapes
    have to keep working: there are recordings on disk from before this.
    """

    def setUp(self):
        locator.snapshots = locator.SnapshotStore()

    async def _find(self, xml, selector):
        current = _manager(xml)
        with patch.object(locator, "capture_snapshot",
                          new=AsyncMock(return_value=current)):
            return await locator.resolve(
                "s1", {}, xpath=selector, timeout=1.0, poll_interval=0.05)

    async def test_a_named_element_is_found_where_its_position_moved(self):
        """The case that failed on the phone: same element, deeper in the tree."""
        resolved = await self._find(SHIFTED_BUTTON,
                                    '//*[@resource-id="confirm_btn"]')
        self.assertIsNotNone(resolved)
        self.assertEqual(resolved.element.text, "Confirm")

    async def test_the_words_on_a_control_will_do(self):
        resolved = await self._find(SHIFTED_BUTTON, '//*[@text="Confirm"]')
        self.assertIsNotNone(resolved)
        self.assertEqual(resolved.element.resource_id, "confirm_btn")

    async def test_a_recording_made_before_this_still_replays(self):
        """Positional locators are still understood, or every recording on
        disk would have to be earned again."""
        resolved = await self._find(
            SINGLE_BUTTON, "/hierarchy[1]/android.widget.Button[1]")
        self.assertIsNotNone(resolved)
        self.assertEqual(resolved.element.text, "Confirm")

    async def test_a_name_two_elements_now_share_is_refused(self):
        """It was this element's alone when recorded and is not any more —
        a second row appeared. Tapping whichever comes first is the blind
        replay the label check exists to stop."""
        self.assertIsNone(
            await self._find(TWO_IDENTICAL_ROWS, '//*[@text="Delete"]'))

    async def test_a_name_nothing_answers_to_is_not_forced_onto_something(self):
        self.assertIsNone(
            await self._find(SINGLE_BUTTON, '//*[@resource-id="gone"]'))
