import unittest
import json
from unittest.mock import AsyncMock, Mock, patch

import agent
import locator
from drivers import ActionResult
from mobile_dom import MobileDOMManager
from web_dom import WebSnapshot

SCREEN = """
<hierarchy rotation="0">
  <android.widget.FrameLayout bounds="[0,0][1080,2400]" displayed="true">
    <android.widget.LinearLayout bounds="[0,0][1080,2400]" displayed="true">
      <android.widget.TextView text="Welcome back" resource-id="a:id/greeting"
        bounds="[60,240][1020,340]" displayed="true"/>
      <android.widget.Button text="Sign out" resource-id="a:id/signOut"
        bounds="[60,400][1020,510]" displayed="true" clickable="true"/>
    </android.widget.LinearLayout>
  </android.widget.FrameLayout>
</hierarchy>
"""


def _manager():
    return MobileDOMManager(SCREEN, "Android", 1080, 2400)


class TestSystemPrompt(unittest.TestCase):
    """The loop is shared across targets; the vocabulary is not."""

    def test_no_placeholder_survives_substitution(self):
        """The prompt is full of JSON braces, so str.format cannot be used —
        a regression here ships a literal '{target_line}' to the model."""
        for kind in ("mobile", "web", "unknown"):
            prompt = agent.build_system_prompt(kind)
            for placeholder in ("{target_line}", "{keys}", "{target_rules}"):
                self.assertNotIn(placeholder, prompt, f"{placeholder} left in the {kind} prompt")

    def test_the_action_json_examples_survive_intact(self):
        prompt = agent.build_system_prompt("web")
        self.assertIn('{"type":"action","action":"click","elementId":"el_12"', prompt)

    def test_mobile_and_web_get_different_keys(self):
        mobile = agent.build_system_prompt("mobile")
        web = agent.build_system_prompt("web")
        self.assertIn("recents", mobile)
        self.assertNotIn("recents", web)   # a browser has no app switcher
        self.assertIn("forward", web)
        self.assertNotIn("forward", mobile)  # a phone has no browser history

    def test_web_prompt_warns_about_cookie_banners(self):
        self.assertIn("cookie banners", agent.build_system_prompt("web"))

    def test_an_unknown_kind_falls_back_to_mobile(self):
        self.assertEqual(agent.build_system_prompt("teapot"), agent.build_system_prompt("mobile"))


class TestParseAction(unittest.TestCase):

    def test_parses_a_fenced_block(self):
        reply = 'I will tap sign out.\n```json\n{"type":"action","action":"click","elementId":"el_4"}\n```'
        self.assertEqual(agent.parse_action(reply), {"type": "action", "action": "click", "elementId": "el_4"})

    def test_takes_the_last_block_when_the_model_rambles(self):
        reply = (
            '```json\n{"type":"action","action":"click","elementId":"el_1"}\n```\n'
            'Actually, better:\n```json\n{"type":"action","action":"click","elementId":"el_9"}\n```'
        )
        self.assertEqual(agent.parse_action(reply)["elementId"], "el_9")

    def test_tolerates_a_missing_fence(self):
        reply = 'Doing it now: {"type": "action", "action": "scroll", "value": "down"}'
        self.assertEqual(agent.parse_action(reply)["action"], "scroll")

    def test_skips_malformed_json_and_uses_an_earlier_valid_block(self):
        reply = (
            '```json\n{"type":"action","action":"click","elementId":"el_2"}\n```\n'
            '```json\n{"type":"action", "action": ,}\n```'
        )
        self.assertEqual(agent.parse_action(reply)["elementId"], "el_2")

    def test_prose_with_no_action_returns_none(self):
        self.assertIsNone(agent.parse_action('The scenario is complete. Everything looked right.'))

    def test_json_without_an_action_key_is_ignored(self):
        self.assertIsNone(agent.parse_action('```json\n{"type":"action","note":"thinking"}\n```'))

    def test_the_verb_is_accepted_under_type(self):
        """Observed in the wild: the model collapsed the wrapper and wrote
        {"type":"type",...}. The block became invisible and the run was
        reported as passed without a single action running."""
        reply = ('```json\n{"type":"type","elementId":"el_3",'
                 '"value":"invalid@email.com","reason":"failed login"}\n```')
        action = agent.parse_action(reply)
        self.assertIsNotNone(action)
        self.assertEqual(action["action"], "type")
        self.assertEqual(action["elementId"], "el_3")

    def test_a_bare_verb_object_without_a_wrapper_is_accepted(self):
        action = agent.parse_action('Doing it: {"type":"click","elementId":"el_9"}')
        self.assertEqual(action["action"], "click")

    def test_the_documented_shape_still_wins_over_type(self):
        action = agent.parse_action('```json\n{"type":"action","action":"scroll","value":"down"}\n```')
        self.assertEqual(action["action"], "scroll")

    def test_action_names_are_lowercased(self):
        self.assertEqual(agent.parse_action('```json\n{"action":"CLICK","elementId":"el_1"}\n```')["action"], "click")

    def test_an_unknown_verb_is_not_invented(self):
        self.assertIsNone(agent.parse_action('```json\n{"type":"teleport","elementId":"el_1"}\n```'))

    def test_an_unparseable_action_block_is_detected(self):
        """A malformed block must not be mistaken for 'the model has nothing
        more to say' — that path ends the run green."""
        self.assertTrue(agent.looks_like_an_attempted_action('```json\n{broken\n```'))
        self.assertTrue(agent.looks_like_an_attempted_action('{"action": "cli'))

    def test_plain_prose_is_not_mistaken_for_an_action(self):
        self.assertFalse(agent.looks_like_an_attempted_action("The scenario is complete."))


class TestVerdictSafety(unittest.TestCase):
    """A green run that verified nothing is worse than a red one: it gets
    trusted. This is the line between a test tool and a clicking robot."""

    def test_finishing_without_an_assertion_fails(self):
        status, message = agent._verdict_without_assertion(False, "All done!")
        self.assertEqual(status, "failed")
        self.assertIn("without asserting", message)

    def test_finishing_after_an_assertion_passes(self):
        status, message = agent._verdict_without_assertion(True, "All done!")
        self.assertEqual(status, "passed")
        self.assertIsNone(message)


class FakeTarget:
    """A stand-in driver. The agent's action handling is platform-neutral now,
    so it can be tested without Appium, a browser, or any network."""

    kind = "fake"

    def __init__(self, snapshot):
        self._snapshot = snapshot
        self.calls = []
        self.act_result = ActionResult(True, "acted", {"id": "signOut"})
        self.scroll_result = ActionResult(True, "Scrolled up")
        self.key_result = ActionResult(True, "Pressed back")
        self.session_id = "fake-session"

    async def snapshot(self):
        return self._snapshot

    async def screenshot(self):
        return None

    async def act(self, kind, element_id, selector, value, snapshot_id):
        self.calls.append(("act", kind, element_id, selector, value, snapshot_id))
        return self.act_result

    async def scroll(self, direction, element_id=None):
        self.calls.append(("scroll", direction, element_id))
        return self.scroll_result

    async def press_key(self, key):
        self.calls.append(("key", key))
        return self.key_result

    async def element_at(self, x, y):
        return None

    def describe(self):
        return {"kind": self.kind, "platform": "Fake", "name": "fake",
                "udid": "fake", "appId": None}

    async def close(self):
        return None


class TestExecuteAction(unittest.IsolatedAsyncioTestCase):

    def setUp(self):
        self.snapshot = _manager()
        self.target = FakeTarget(self.snapshot)

    async def test_wait_is_capped_at_ten_seconds(self):
        with patch("asyncio.sleep", new=AsyncMock()) as sleep:
            result = await agent._execute_action(self.target, {"action": "wait", "value": "600"}, self.snapshot)
        self.assertTrue(result["ok"])
        sleep.assert_awaited_once_with(10.0)

    async def test_assert_text_passes_when_the_text_is_on_screen(self):
        with patch("asyncio.sleep", new=AsyncMock()):
            result = await agent._execute_action(
                self.target, {"action": "assert_text", "value": "Welcome back"}, self.snapshot
            )
        self.assertTrue(result["ok"])
        self.assertIn("Welcome back", result["message"])

    async def test_assert_text_fails_and_lists_what_was_visible(self):
        """A failing assertion must say what was actually there, or the report
        is useless for debugging."""
        with patch("asyncio.sleep", new=AsyncMock()):
            result = await agent._execute_action(
                self.target, {"action": "assert_text", "value": "Checkout complete"}, self.snapshot
            )
        self.assertFalse(result["ok"])
        self.assertIn("Welcome back", result["message"])

    async def test_assert_text_requires_a_value(self):
        result = await agent._execute_action(self.target, {"action": "assert_text"}, self.snapshot)
        self.assertFalse(result["ok"])

    async def test_assert_text_re_reads_rather_than_trusting_the_stale_snapshot(self):
        """The screen changed a moment ago; asserting against the pre-action
        capture would pass on text that is already gone."""
        stale = _manager()
        fresh = MobileDOMManager(
            '<hierarchy><android.widget.TextView text="Goodbye" displayed="true" bounds="[0,0][100,50]"/></hierarchy>',
            "Android", 1080, 2400,
        )
        self.target._snapshot = fresh
        with patch("asyncio.sleep", new=AsyncMock()):
            result = await agent._execute_action(
                self.target, {"action": "assert_text", "value": "Welcome back"}, stale
            )
        self.assertFalse(result["ok"])

    async def test_scroll_is_delegated_to_the_driver(self):
        result = await agent._execute_action(self.target, {"action": "scroll", "value": "up"}, self.snapshot)
        self.assertTrue(result["ok"])
        self.assertIn(("scroll", "up", None), self.target.calls)

    async def test_swipe_is_treated_as_scroll(self):
        await agent._execute_action(self.target, {"action": "swipe", "value": "left"}, self.snapshot)
        self.assertIn(("scroll", "left", None), self.target.calls)

    async def test_key_failure_is_reported_verbatim(self):
        self.target.key_result = ActionResult(False, "Key 'menu' is not supported on iOS")
        result = await agent._execute_action(self.target, {"action": "key", "value": "menu"}, self.snapshot)
        self.assertFalse(result["ok"])
        self.assertIn("iOS", result["message"])

    async def test_key_defaults_to_back_when_no_value_is_given(self):
        await agent._execute_action(self.target, {"action": "key"}, self.snapshot)
        self.assertIn(("key", "back"), self.target.calls)

    async def test_element_actions_pass_the_snapshot_id_through(self):
        """The snapshot id is what stops an action resolving against a tree that
        was renumbered by a concurrent refresh."""
        await agent._execute_action(
            self.target, {"action": "click", "elementId": "el_4"}, self.snapshot
        )
        call = next(c for c in self.target.calls if c[0] == "act")
        self.assertEqual(call[1], "click")
        self.assertEqual(call[2], "el_4")
        self.assertEqual(call[5], self.snapshot.snapshot_id)

    async def test_driver_failure_is_surfaced_as_a_failed_step(self):
        """An ambiguous or missing element must become a failed step, not an
        exception that aborts the whole run."""
        self.target.act_result = ActionResult(False, "Ambiguous target: 3 elements match")
        result = await agent._execute_action(
            self.target, {"action": "click", "elementId": "el_4"}, self.snapshot
        )
        self.assertFalse(result["ok"])
        self.assertIn("Ambiguous", result["message"])

    async def test_selector_alias_is_accepted(self):
        await agent._execute_action(
            self.target, {"action": "click", "selector": "#login"}, self.snapshot
        )
        call = next(c for c in self.target.calls if c[0] == "act")
        self.assertEqual(call[3], "#login")

    async def test_a_pixel_comparison_is_refused_on_a_phone(self):
        """Measured on the real set: a step whose screen was correct failed at
        7.18% of pixels differing, because the status bar clock had moved. The
        run was recorded as a defect nobody could reproduce by hand."""
        self.target.kind = "mobile"
        result = await agent._execute_action(
            self.target, {"action": "assert_visual", "value": "one-way-tab"}, self.snapshot
        )
        self.assertFalse(result["ok"])
        self.assertIn("assert_text", result["message"])

    async def test_a_pixel_comparison_is_still_offered_on_the_web(self):
        """A browser page has no status bar; a layout regression there is
        exactly what a baseline is good for."""
        self.target.kind = "web"
        result = await agent._execute_action(
            self.target, {"action": "assert_visual"}, self.snapshot
        )
        self.assertFalse(result["ok"])
        self.assertIn("baseline name", result["message"])


class TestSessionControl(unittest.TestCase):

    def setUp(self):
        agent.reset("s1")

    def test_cancel_is_a_no_op_when_nothing_is_running(self):
        self.assertFalse(agent.cancel("s1"))

    def test_cancel_signals_a_running_session(self):
        state = agent.get_session("s1")
        state.running = True
        self.assertTrue(agent.cancel("s1"))
        self.assertTrue(state.cancel.is_set())

    def test_reset_clears_the_session(self):
        agent.get_session("s1").running = True
        agent.reset("s1")
        self.assertFalse(agent.is_running("s1"))


if __name__ == "__main__":
    unittest.main()


class ResolveEffort(unittest.TestCase):
    """The picker is a convenience, so a bad value must not cost a run."""

    def test_each_supported_level_survives(self):
        for level in agent.EFFORTS:
            self.assertEqual(agent._resolve_effort(level), level)

    def test_case_and_padding_are_forgiven(self):
        self.assertEqual(agent._resolve_effort("  HIGH "), "high")

    def test_anything_unrecognised_falls_back_rather_than_raising(self):
        for value in (None, "", "turbo", "1", "fastest"):
            self.assertEqual(agent._resolve_effort(value), agent.DEFAULT_EFFORT)


class NestedRunReentrancy(unittest.IsolatedAsyncioTestCase):
    """`run_test_set` can be invoked as a chat action while the chat's own
    run_agent call is still in progress on the same device. That nested call
    used to share agent._sessions[session_id] with the outer one — the
    reentrancy guard refused it outright, so every case in the Test Set
    "failed" in milliseconds with no run ever created, and the counts alone
    (e.g. "0 passed, 20 failed of 20") gave no hint why. `session_state` lets
    the nested call carry its own throwaway state instead."""

    def _target_for(self, session_id):
        target = Mock()
        target.session_id = session_id
        return target

    async def test_a_plain_nested_call_is_refused(self):
        # Establishes the failure this regresses: without the escape hatch, a
        # second run_agent on a session already marked running is an instant,
        # silent no-op — exactly what made every case in the Test Set vanish.
        session_id = "reentrancy-plain"
        outer = agent.get_session(session_id)
        outer.running = True
        try:
            events = [
                json.loads(line)
                async for line in agent.run_agent(self._target_for(session_id), "goal")
            ]
        finally:
            outer.running = False
            agent.reset(session_id)
        self.assertEqual(events[0]["event"], "error")
        self.assertIn("already in progress", events[0]["message"])

    async def test_an_isolated_nested_call_reaches_past_the_guard(self):
        session_id = "reentrancy-isolated"
        outer = agent.get_session(session_id)
        outer.running = True
        try:
            with patch.object(
                agent, "_resolve_provider",
                side_effect=RuntimeError("stopped right after the guard"),
            ):
                events = [
                    json.loads(line)
                    async for line in agent.run_agent(
                        self._target_for(session_id), "goal",
                        session_state=agent.AgentSession(),
                    )
                ]
            # The guard did not fire — the call got far enough to hit the
            # (mocked) provider step instead of bouncing off reentrancy.
            self.assertEqual(events[0]["event"], "error")
            self.assertNotIn("already in progress", events[0]["message"])
            self.assertIn("stopped right after the guard", events[0]["message"])
            # And the outer run's own state was never touched.
            self.assertTrue(outer.running)
        finally:
            outer.running = False
            agent.reset(session_id)


class AuthoringActionDispatch(unittest.IsolatedAsyncioTestCase):
    """The chat composer sends labeled Test Set / Execution / Açıklama fields
    as separate `value`/`execution`/`brief` action fields precisely so these
    reach the backend as what the tester typed, not the model's retelling of
    it. This is the wiring that has to keep that promise."""

    def _target(self, kind="web"):
        target = Mock()
        target.kind = kind
        target.describe = Mock(return_value={"appId": "app.example", "name": "Example"})
        return target

    async def test_brief_is_taken_from_the_action_not_the_whole_goal(self):
        with patch.object(agent.authoring, "write_scenarios",
                          AsyncMock(return_value={"ok": True, "message": "done"})) as ws:
            await agent._run_authoring_action(
                "write_scenarios",
                {"action": "write_scenarios", "value": "Booking", "brief": "ödeme akışı"},
                self._target(), None, None,
                goal="Test Set: \"Booking\"\nAçıklama: ödeme akışı",
            )
        self.assertEqual(ws.call_args.kwargs["brief"], "ödeme akışı")
        self.assertEqual(ws.call_args.kwargs["name"], "Booking")

    async def test_brief_falls_back_to_the_goal_when_the_model_omits_it(self):
        with patch.object(agent.authoring, "write_scenarios",
                          AsyncMock(return_value={"ok": True, "message": "done"})) as ws:
            await agent._run_authoring_action(
                "write_scenarios",
                {"action": "write_scenarios", "value": "Booking"},
                self._target(), None, None, goal="senaryolari yaz",
            )
        self.assertEqual(ws.call_args.kwargs["brief"], "senaryolari yaz")

    async def test_an_empty_test_set_name_is_passed_through_not_invented(self):
        # Regression: this dispatcher used to substitute "Chat Test Set" for a
        # blank value, which pre-empted authoring.write_scenarios's own
        # fallback (naming the set after the app/screen) and put every unnamed
        # run under the same unhelpful label.
        with patch.object(agent.authoring, "write_scenarios",
                          AsyncMock(return_value={"ok": True, "message": "done"})) as ws:
            await agent._run_authoring_action(
                "write_scenarios", {"action": "write_scenarios"},
                self._target(), None, None, goal="senaryolari yaz",
            )
        self.assertEqual(ws.call_args.kwargs["name"], "")

    async def test_execution_name_reaches_run_test_set(self):
        with patch.object(agent.authoring, "run_test_set",
                          AsyncMock(return_value={"ok": True, "message": "done"})) as rts:
            await agent._run_authoring_action(
                "run_test_set",
                {"action": "run_test_set", "value": "Booking", "execution": "Regresyon"},
                self._target(), None, None, goal="ignored",
            )
        rts.assert_awaited_once_with(name="Booking", execution_name="Regresyon")

    async def test_a_blank_execution_name_is_omitted_not_passed_as_empty_string(self):
        with patch.object(agent.authoring, "run_test_set",
                          AsyncMock(return_value={"ok": True, "message": "done"})) as rts:
            await agent._run_authoring_action(
                "run_test_set", {"action": "run_test_set", "value": "Booking"},
                self._target(), None, None, goal="ignored",
            )
        rts.assert_awaited_once_with(name="Booking", execution_name=None)


class WrittenScenarioSteps(unittest.IsolatedAsyncioTestCase):
    """A scenario written as steps is judged step by step. That is the whole
    point of writing it out: the report has to answer "did step 3 pass", not
    only "did the run pass"."""

    STEPS = [
        {"action": "Adim 1", "expected": "Beklenen 1"},
        {"action": "Adim 2", "expected": "Beklenen 2"},
    ]

    ASSERT = '```json\n{"type":"action","action":"assert_visible","elementId":"el_1","reason":"r"}\n```'

    @staticmethod
    def _close(verdict, reason):
        return ('```json\n{"type":"action","action":"step_done","value":"%s",'
                '"reason":"%s"}\n```' % (verdict, reason))

    async def _run(self, script, steps=None):
        class Provider:
            id, label = "fake", "Fake"
            def __init__(self): self.i = 0
            async def stream(self, *a, **k):
                reply = script[self.i] if self.i < len(script) else self_outer._close("fail", "script ran out")
                self.i += 1
                yield reply

        self_outer = self

        class Snapshot:
            snapshot_id = "s"
            def get_optimized_tree_for_llm(self): return {"elementId": "el_1"}

        class Target:
            kind, session_id = "web", "written-scenario"
            def describe(self): return {"name": "t", "platform": "Web"}
            async def snapshot(self): return Snapshot()
            async def screenshot(self): return None

        events = []
        with patch.object(agent.providers, "get", lambda *a, **k: Provider()), \
             patch.object(agent.providers, "api_key_for", lambda *a, **k: "k"), \
             patch.object(agent.providers, "active_model", lambda *a, **k: "m"), \
             patch.object(agent, "_execute_action", AsyncMock(
                 return_value={"ok": True, "message": "ok", "element": None})):
            async for line in agent.run_agent(
                Target(), "senaryo", steps=steps or self.STEPS,
                session_state=agent.AgentSession(),
            ):
                events.append(json.loads(line))
        return events

    @staticmethod
    def _finished(events):
        return next(e for e in events if e["event"] == "finished")

    @staticmethod
    def _verdicts(events):
        return [
            (e["index"], e["status"])
            for e in events if e["event"] == "scenario_step_finished"
        ]

    async def test_each_step_is_reported_on_its_own(self):
        events = await self._run([
            self.ASSERT, self._close("pass", "ok"),
            self.ASSERT, self._close("pass", "ok"),
        ])
        self.assertEqual(self._verdicts(events), [(1, "passed"), (2, "passed")])
        self.assertEqual(self._finished(events)["status"], "passed")

    async def test_one_failed_step_fails_the_run_and_names_itself(self):
        events = await self._run([
            self.ASSERT, self._close("pass", "ok"),
            self.ASSERT, self._close("fail", "beklenen cikmadi"),
        ])
        self.assertEqual(self._verdicts(events), [(1, "passed"), (2, "failed")])
        finished = self._finished(events)
        self.assertEqual(finished["status"], "failed")
        self.assertIn("Step 2", finished["summary"])



    async def test_a_step_that_proves_nothing_cannot_pass(self):
        # The same trap as a green run that asserted nothing, one scale down:
        # a step with an expected result has to actually check it.
        events = await self._run([self._close("pass", "kanit yok"), self.ASSERT,
                                  self._close("pass", "ok")])
        self.assertEqual(self._verdicts(events)[0], (1, "failed"))

    async def test_a_step_without_an_expected_result_may_close_unproven(self):
        # Nothing was claimed, so nothing has to be proved — the step is
        # reported as carried out rather than verified.
        events = await self._run(
            [self._close("pass", "yapildi")],
            steps=[{"action": "Sadece git", "expected": ""}],
        )
        self.assertEqual(self._verdicts(events), [(1, "passed")])

    async def test_a_step_that_never_closes_is_cut_off_not_left_running(self):
        # Otherwise it spends the whole run's budget and starves the steps
        # behind it.
        events = await self._run([self.ASSERT] * 40, steps=[
            {"action": "Bitmeyen adim", "expected": "hic gelmez"},
            {"action": "Sonraki adim", "expected": "yine de kosmali"},
        ])
        verdicts = self._verdicts(events)
        self.assertEqual(verdicts[0], (1, "failed"))
        # The second step still got its turn rather than inheriting an
        # exhausted ceiling.
        self.assertGreaterEqual(len(verdicts), 2)

    async def test_a_run_without_steps_keeps_the_open_ended_prompt(self):
        self.assertNotIn("step_done", agent.build_system_prompt("web"))
        self.assertIn("step_done", agent.build_system_prompt("web", stepwise=True))


class ReplayingARecording(unittest.IsolatedAsyncioTestCase):
    """The second run of an unchanged scenario should not ask the model how to
    do what the first one already did.

    A green run's actions are kept on the scenario's steps, and a step that has
    them carries them out instead of reasoning its way there — which is the
    whole saving, since a model call is what an action costs. Everything here
    is about the two halves of that being true at once: that the model really
    is skipped, and that a recording which no longer fits the page hands the
    step straight back rather than driving on through a screen it does not
    describe.
    """

    ASSERT = ('```json\n{"type":"action","action":"assert_visible",'
              '"elementId":"el_1","reason":"r"}\n```')
    CLOSE = ('```json\n{"type":"action","action":"step_done","value":"pass",'
             '"reason":"ok"}\n```')

    async def _run(self, steps, script=None, fails=()):
        """Run the scenario, counting what the model was asked and what ran.

        `fails` names the actions `_execute_action` should refuse *when they
        were replayed*, which is how a recording that no longer fits the page
        is expressed. The model's own attempt at the same action succeeds —
        that is the point of handing the step back to it.
        """
        script = list(script or [])
        asked = []
        ran = []

        class Provider:
            id, label = "fake", "Fake"

            async def stream(self, _system, turns, *a, **k):
                asked.append(turns)
                yield script.pop(0) if script else (
                    '```json\n{"type":"action","action":"step_done",'
                    '"value":"fail","reason":"script ran out"}\n```'
                )

        class Snapshot:
            snapshot_id = "s"
            def get_optimized_tree_for_llm(self): return {"elementId": "el_1"}

        class Target:
            kind, session_id = "web", "replay-test"
            def describe(self): return {"name": "t", "platform": "Web"}
            async def snapshot(self): return Snapshot()
            async def screenshot(self): return None

        async def execute(_target, action, _snapshot):
            kind = (action.get("action") or "").lower()
            reason = action.get("reason") or ""
            ran.append((kind, action.get("selector"), reason))
            ok = not (kind in fails and "replayed" in reason)
            return {"ok": ok, "message": "ok" if ok else "gone", "element": None}

        events = []
        with patch.object(agent.providers, "get", lambda *a, **k: Provider()), \
             patch.object(agent.providers, "api_key_for", lambda *a, **k: "k"), \
             patch.object(agent.providers, "active_model", lambda *a, **k: "m"), \
             patch.object(agent, "_execute_action", execute):
            async for line in agent.run_agent(
                Target(), "senaryo", steps=steps, session_state=agent.AgentSession(),
            ):
                events.append(json.loads(line))
        return events, asked, ran

    @staticmethod
    def _verdicts(events):
        return [(e["index"], e["status"])
                for e in events if e["event"] == "scenario_step_finished"]

    RECORDED = [
        {"action": "click", "selector": "#search", "value": None, "label": "Uçuş ara"},
        {"action": "assert_visible", "selector": "#results", "value": None, "label": "Results"},
    ]

    async def test_a_recorded_step_costs_no_model_call_at_all(self):
        """The point of the whole thing. One step, fully recorded, ending in an
        assertion that holds — the provider is never reached."""
        events, asked, ran = await self._run([
            {"action": "Search", "expected": "Results", "recorded": self.RECORDED},
        ])
        self.assertEqual(asked, [], "the model should not have been asked anything")
        self.assertEqual([kind for kind, _, _ in ran], ["click", "assert_visible"])
        self.assertEqual(self._verdicts(events), [(1, "passed")])

    async def test_the_recorded_selector_is_what_gets_acted_on(self):
        _, _, ran = await self._run([
            {"action": "Search", "expected": "Results", "recorded": self.RECORDED},
        ])
        self.assertEqual([selector for _, selector, _ in ran], ["#search", "#results"])

    async def test_a_replayed_action_says_where_it_came_from(self):
        """It goes into the run's own record, so a reader can tell a step that
        was worked out from one that was repeated."""
        _, _, ran = await self._run([
            {"action": "Search", "expected": "Results", "recorded": self.RECORDED},
        ])
        self.assertIn("replayed", (ran[0][2] or "").lower())

    async def test_a_recording_that_misses_hands_the_step_back(self):
        """The page moved. Everything still queued was recorded against a state
        it is no longer in, so the model takes over from where the replay got
        to rather than driving on through it."""
        events, asked, ran = await self._run(
            [{"action": "Search", "expected": "Results", "recorded": self.RECORDED}],
            script=[self.ASSERT, self.CLOSE],
            fails={"click"},
        )
        self.assertTrue(asked, "the model should have been asked to finish the step")
        self.assertNotIn("assert_visible", [kind for kind, _, r in ran if r and "replayed" in r])
        self.assertEqual(self._verdicts(events), [(1, "passed")])

    async def test_a_stale_recorded_assertion_does_not_fail_the_run(self):
        """A recorded assertion that no longer holds says the recording is out
        of date, which is the case the model is there for. Ending the run on it
        would make a scenario fail for having passed before."""
        events, asked, _ = await self._run(
            [{"action": "Search", "expected": "Results", "recorded": self.RECORDED}],
            script=[self.ASSERT, self.CLOSE],
            fails={"assert_visible"},
        )
        # The replayed assertion fails, the model is asked, and its own
        # assertion (which this harness lets through) closes the step.
        self.assertTrue(asked)
        self.assertEqual(self._verdicts(events), [(1, "passed")])

    async def test_a_recording_that_proves_nothing_still_goes_to_the_model(self):
        """Clicks alone are not a passed step. Without an assertion behind it
        the step is handed over exactly as an unrecorded one would be."""
        events, asked, ran = await self._run(
            [{"action": "Search", "expected": "Results",
              "recorded": [{"action": "click", "selector": "#search"}]}],
            script=[self.ASSERT, self.CLOSE],
        )
        self.assertTrue(asked, "a recording with no assertion cannot close a step")
        self.assertEqual(ran[0][0], "click", "but its actions still ran")
        self.assertEqual(self._verdicts(events), [(1, "passed")])

    async def test_a_replayed_action_records_the_selector_it_used(self):
        """Measured on a real suite: a run that replayed a recording perfectly
        then erased it. A replayed action is given a selector rather than an
        elementId, so the driver resolves it without a snapshot and has no
        element to hand back — and the selector, derived from that element, came
        out empty. An action with no selector cannot be replayed, so promotion
        threw the whole step's recording away and the third run paid full price
        again.
        """
        recorded = []
        with patch.object(agent.storage, "add_step",
                          side_effect=lambda *a, **k: recorded.append(k) or 1):
            await self._run([{
                "action": "Search", "expected": "Results", "recorded": self.RECORDED,
            }])
        assert recorded, "nothing was recorded at all"
        self.assertEqual(
            [entry.get("selector") for entry in recorded],
            ["#search", "#results"],
        )

    async def test_a_step_with_no_recording_behaves_as_it_always_did(self):
        events, asked, _ = await self._run(
            [{"action": "Search", "expected": "Results"}],
            script=[self.ASSERT, self.CLOSE],
        )
        self.assertEqual(len(asked), 2)
        self.assertEqual(self._verdicts(events), [(1, "passed")])

    async def test_a_recorded_step_and_an_unrecorded_one_in_the_same_scenario(self):
        """The mixed case is the ordinary one while a suite is warming up."""
        events, asked, _ = await self._run(
            [
                {"action": "Search", "expected": "Results", "recorded": self.RECORDED},
                {"action": "Pick a flight", "expected": "Passenger page"},
            ],
            script=[self.ASSERT, self.CLOSE],
        )
        self.assertEqual(len(asked), 2, "only the unrecorded step should cost calls")
        self.assertEqual(self._verdicts(events), [(1, "passed"), (2, "passed")])


class LongListsOnAPhone(unittest.TestCase):
    """A phone renders the rows on screen and nothing else.

    Measured on the real app: the airport picker opens on Abidjan, the element
    tree carries the thirty-odd rows visible, and Istanbul — three hundred rows
    down — is simply not there. Four of five iOS scenarios failed on it, each
    asserting an airport name that could not appear until someone searched for
    it. The screen has a search field; the agent was not told to prefer it.
    """

    def test_the_mobile_prompt_says_to_search_rather_than_scroll(self):
        prompt = agent.build_system_prompt("mobile")
        self.assertIn("search or filter field", prompt)
        self.assertIn("Scroll only when there is no such field", prompt)

    def test_the_web_prompt_is_left_alone(self):
        """A browser hands over the whole document, so the row is in the tree
        whether or not it is on screen — the same advice there would send the
        agent hunting for a search box it does not need."""
        self.assertNotIn("three hundred rows", agent.build_system_prompt("web"))


class WhatAGreenErrorCheckActuallySays(unittest.IsolatedAsyncioTestCase):
    """`assert_no_errors` judges errors, and every 4xx is a warning here on
    purpose — a live airline site answers 4xx all through a perfectly good
    booking, and failing on those used to fail real runs constantly.

    But the step reported "No console or network errors on this page" over
    sixteen recorded first-party failures, one of them an HTTP 400 on the
    page's own origin. A tester reading that report concluded the page was
    clean. It was not: it was a page whose failures this assertion does not
    judge, and those are different sentences.
    """

    def setUp(self):
        self.snapshot = _manager()
        self.target = FakeTarget(self.snapshot)

    def _events(self, events):
        self.target.peek_events = lambda: events

    async def test_a_clean_page_still_says_so_plainly(self):
        self._events([])
        result = await agent._execute_action(
            self.target, {"action": "assert_no_errors"}, self.snapshot)
        self.assertTrue(result["ok"])
        self.assertEqual(result["message"], "No console or network errors on this page")

    async def test_warnings_are_counted_in_the_message(self):
        self._events([
            {"level": "warning", "kind": "httperror", "text": "HTTP 400",
             "thirdParty": False},
            {"level": "warning", "kind": "httperror", "text": "HTTP 404",
             "thirdParty": False},
            {"level": "warning", "kind": "console", "text": "third party noise",
             "thirdParty": True},
        ])
        result = await agent._execute_action(
            self.target, {"action": "assert_no_errors"}, self.snapshot)
        self.assertTrue(result["ok"], "a 4xx must still not fail the run")
        self.assertIn("3 warning", result["message"])
        self.assertIn("2 from this site", result["message"])
        self.assertIn("HTTP 400", result["message"])

    async def test_someone_elses_outage_is_not_counted_against_this_site(self):
        self._events([
            {"level": "warning", "kind": "httperror", "text": "HTTP 403",
             "thirdParty": True},
        ])
        result = await agent._execute_action(
            self.target, {"action": "assert_no_errors"}, self.snapshot)
        self.assertTrue(result["ok"])
        self.assertIn("1 warning", result["message"])
        self.assertNotIn("from this site", result["message"])

    async def test_a_real_error_still_fails_the_step(self):
        """The whole point of the check, and the warnings must not dilute it."""
        self._events([
            {"level": "warning", "kind": "httperror", "text": "HTTP 404",
             "thirdParty": False},
            {"level": "error", "kind": "pageerror", "text": "TypeError: x is not a function",
             "thirdParty": False},
        ])
        result = await agent._execute_action(
            self.target, {"action": "assert_no_errors"}, self.snapshot)
        self.assertFalse(result["ok"])
        self.assertIn("TypeError", result["message"])


class AskingAboutOneFieldRatherThanTheWholeScreen(unittest.IsolatedAsyncioTestCase):
    """`assert_text` searches everything on screen, which cannot prove which
    field holds a value — and most of a booking form is that question.

    Measured on the swap control: origin and destination trade places, and
    every reading of the screen as a whole passes before and after, because
    both airports are on it either way. The scenario for it could not be made
    honest until the assertion could say *where*.
    """

    def setUp(self):
        self.snapshot = _manager()
        self.target = FakeTarget(self.snapshot)

    async def _assert(self, **action):
        with patch("asyncio.sleep", new=AsyncMock()):
            return await agent._execute_action(
                self.target, {"action": "assert_text", **action}, self.snapshot)

    async def test_the_field_that_holds_it_passes(self):
        element = next(iter(self.snapshot.elements_by_id.values()))
        element.text = "ESB - Ankara Esenboğa"
        result = await self._assert(elementId=element.element_id, value="ESB")
        self.assertTrue(result["ok"])
        self.assertIn("contains", result["message"])

    async def test_a_field_that_does_not_fails_even_though_the_screen_has_it(self):
        """The whole point: the word is on the screen, in the other field.

        Two leaves, not the first two entries — the tree's first entries are
        its root and its wrappers, and asserting on those *is* asserting on
        the whole screen, which this is here to tell apart.
        """
        leaves = [element for element in self.snapshot.elements_by_id.values()
                  if not element.children]
        leaves[0].text = "IST - İstanbul"
        leaves[1].text = "ESB - Ankara"
        result = await self._assert(elementId=leaves[0].element_id, value="ESB")
        self.assertFalse(result["ok"])
        self.assertIn("holds", result["message"])
        # And unscoped, the same screen passes — which is what made the swap
        # scenario green whether or not the swap worked.
        self.assertTrue((await self._assert(value="ESB"))["ok"])

    async def test_a_wrapper_holds_everything_inside_it(self):
        """A container's subtree is the region it draws, so asserting on one
        is asserting on all of it. That is the right answer — and the reason
        the scenario has to name the field, not the panel around it."""
        root = next(iter(self.snapshot.elements_by_id.values()))
        self.assertTrue(root.children, "the first entry is a wrapper")
        result = await self._assert(elementId=root.element_id, value="Sign out")
        self.assertTrue(result["ok"])

    async def test_a_field_that_has_gone_is_said_so_not_silently_passed(self):
        result = await self._assert(elementId="el_does_not_exist", value="ESB")
        self.assertFalse(result["ok"])
        self.assertIn("not on the screen", result["message"])

    async def test_without_an_element_it_still_reads_the_whole_screen(self):
        """The unscoped form is most of the suite and must not change."""
        result = await self._assert(value="Welcome back")
        self.assertTrue(result["ok"])
        self.assertIn("on screen", result["message"])


class AControlsWordsAreOftenNotOnTheControl(unittest.IsolatedAsyncioTestCase):
    """The extractor gives a node only its own text — direct text children —
    and on the web a button's label routinely sits two divs down.

    Measured on the booker: the date field showed "28 Eyl Pazartesi" on screen
    and asserting on its button came back `Expected "button" to contain "28"
    but it holds ""`. The node's own text really was empty. The date was in
    its descendants, which is where anyone reading the screen saw it.
    """

    PAGE = {"nodes": [
        {"role": "button", "tag": "button", "id": "booker-date",
         "text": None, "label": None, "selector": "#booker-date",
         "bounds": {"x1": 0, "y1": 0, "x2": 200, "y2": 60}, "parentIndex": -1},
        {"role": "text", "tag": "span", "text": "Gidiş", "selector": "#booker-date span",
         "bounds": {"x1": 0, "y1": 0, "x2": 100, "y2": 20}, "parentIndex": 0},
        {"role": "text", "tag": "span", "text": "28 Eyl Pazartesi",
         "selector": "#booker-date span:nth-of-type(2)",
         "bounds": {"x1": 0, "y1": 20, "x2": 200, "y2": 60}, "parentIndex": 0},
        {"role": "button", "tag": "button", "id": "empty-box",
         "text": None, "label": None, "selector": "#empty-box",
         "bounds": {"x1": 0, "y1": 80, "x2": 200, "y2": 120}, "parentIndex": -1},
    ]}

    def setUp(self):
        self.snapshot = WebSnapshot(self.PAGE)
        self.target = FakeTarget(self.snapshot)
        self.target.kind = "web"
        self.byId = {
            element.html_id: element.element_id
            for element in self.snapshot.get_all_elements() if element.html_id
        }

    async def _assert(self, **action):
        with patch("asyncio.sleep", new=AsyncMock()):
            return await agent._execute_action(
                self.target, {"action": "assert_text", **action}, self.snapshot)

    async def test_a_value_in_a_child_counts_as_the_field_holding_it(self):
        result = await self._assert(elementId=self.byId["booker-date"], value="28")
        self.assertTrue(result["ok"], result["message"])

    async def test_the_whole_phrase_works_too(self):
        result = await self._assert(
            elementId=self.byId["booker-date"], value="28 Eyl Pazartesi")
        self.assertTrue(result["ok"], result["message"])

    async def test_a_field_that_really_is_empty_says_so_plainly(self):
        """`it holds ""` told the reader nothing. Say there is no text."""
        result = await self._assert(elementId=self.byId["empty-box"], value="28")
        self.assertFalse(result["ok"])
        self.assertIn("no text in it", result["message"])

    async def test_a_value_in_another_field_still_fails(self):
        """The whole point of scoping: the date is on the screen, and it is
        not in this box."""
        result = await self._assert(elementId=self.byId["empty-box"], value="Eyl")
        self.assertFalse(result["ok"])

    async def test_the_failure_shows_what_the_field_does_hold(self):
        result = await self._assert(elementId=self.byId["booker-date"], value="29")
        self.assertFalse(result["ok"])
        self.assertIn("28 Eyl Pazartesi", result["message"])
