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

    # Set False to stand in for a page that was closed or crashed.
    alive = True

    def is_alive(self):
        return self.alive

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
            def visible_text(self): return ["Bir ekran"]

        class Target:
            kind, session_id = "web", "written-scenario"
            def describe(self): return {"name": "t", "platform": "Web"}
            def is_alive(self): return True
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



    async def test_an_optional_step_does_not_take_the_run_down_with_it(self):
        """The step is carried out and not judged. Measured on the booker: the
        date had to be picked and reading it back was somebody else's
        scenario, and that one check failed the whole flow."""
        events = await self._run(
            [self.ASSERT, self._close("pass", "ok"),
             self.ASSERT, self._close("fail", "okuyamadim")],
            steps=[
                {"action": "Adim 1", "expected": "Beklenen 1"},
                {"action": "Adim 2", "expected": "Beklenen 2", "optional": True},
            ],
        )
        self.assertEqual(self._verdicts(events), [(1, "passed"), (2, "skipped")])
        finished = self._finished(events)
        self.assertEqual(finished["status"], "passed")
        self.assertIn("2 steps passed", finished["summary"])

    async def test_an_optional_step_that_works_is_still_a_pass(self):
        """Marking it optional lowers what a failure costs, not what a success
        is worth."""
        events = await self._run(
            [self.ASSERT, self._close("pass", "ok")],
            steps=[{"action": "Adim 1", "expected": "Beklenen 1", "optional": True}],
        )
        self.assertEqual(self._verdicts(events), [(1, "passed")])
        self.assertEqual(self._finished(events)["status"], "passed")

    async def test_a_required_step_after_an_optional_one_still_fails_the_run(self):
        """Skipping is per step. It must not become a way to make a scenario
        unfailable."""
        events = await self._run(
            [self.ASSERT, self._close("fail", "bos ver"),
             self.ASSERT, self._close("fail", "bu onemliydi")],
            steps=[
                {"action": "Adim 1", "expected": "Beklenen 1", "optional": True},
                {"action": "Adim 2", "expected": "Beklenen 2"},
            ],
        )
        self.assertEqual(self._verdicts(events), [(1, "skipped"), (2, "failed")])
        self.assertEqual(self._finished(events)["status"], "failed")

    async def test_the_model_is_told_not_to_grind_at_an_optional_check(self):
        """Otherwise it spends the step's whole budget looking for another way
        to prove something the tester has already called unnecessary."""
        prompts = []
        self_outer = self

        class Provider:
            id, label = "fake", "Fake"

            def __init__(self):
                self.i = 0

            async def stream(self, *args, **kwargs):
                # The step's own instruction is in the turns, not the system
                # prompt, so everything the provider is handed is captured.
                prompts.append(json.dumps(args, ensure_ascii=False, default=str))
                self.i += 1
                yield self_outer._close("pass", "ok")

        class Snapshot:
            snapshot_id = "s"

            def get_optimized_tree_for_llm(self):
                return {"elementId": "el_1"}

            def visible_text(self):
                return ["Bir ekran"]

        class Target:
            kind, session_id = "web", "optional-prompt"

            def describe(self):
                return {"name": "t", "platform": "Web"}

            def is_alive(self):
                return True

            async def snapshot(self):
                return Snapshot()

            async def screenshot(self):
                return None

        with patch.object(agent.providers, "get", lambda *a, **k: Provider()),              patch.object(agent.providers, "api_key_for", lambda *a, **k: "k"),              patch.object(agent.providers, "active_model", lambda *a, **k: "m"),              patch.object(agent, "_execute_action", AsyncMock(
                 return_value={"ok": True, "message": "ok", "element": None})):
            async for _ in agent.run_agent(
                Target(), "senaryo",
                steps=[{"action": "Adim", "expected": "Beklenen", "optional": True}],
                session_state=agent.AgentSession(),
            ):
                pass

        assert any("OPTIONAL" in p for p in prompts), "the step never said it was"

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
            def visible_text(self): return ["Bir ekran"]

        class Target:
            kind, session_id = "web", "replay-test"
            def describe(self): return {"name": "t", "platform": "Web"}
            def is_alive(self): return True
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


class WhenTheCheckIsPointedAtTheWrongElement(unittest.IsolatedAsyncioTestCase):
    """The biggest bucket of red steps in the real run history, and almost none
    of them were the product.

    The model names a wrapper that carries no text at all — 62 of the 193
    interactive elements on the home page are like that — and the answer came
    back `holds ""` about a field the screen was plainly showing a value in.
    Climbing the tree to find the words is not an option: measured on the real
    booker, one ancestor up from the origin field already pulls in the
    destination field's text, which destroys the one thing a scoped assertion
    is for. The boxes do not overlap, so geometry decides instead.
    """

    PAGE = {"nodes": [
        # An icon button drawn inside the region that carries the label.
        {"role": "group", "tag": "div", "id": "from-field", "text": None,
         "label": None, "selector": "#from-field",
         "bounds": {"x1": 0, "y1": 0, "x2": 300, "y2": 80}, "parentIndex": -1},
        {"role": "text", "tag": "span", "text": "İstanbul (IST)",
         "selector": "#from-field span",
         "bounds": {"x1": 10, "y1": 10, "x2": 290, "y2": 70}, "parentIndex": 0},
        {"role": "button", "tag": "button", "id": "from-icon", "text": None,
         "label": None, "selector": "#from-icon",
         "bounds": {"x1": 20, "y1": 20, "x2": 60, "y2": 60}, "parentIndex": -1},
        # The neighbouring field, well away from the first one.
        {"role": "group", "tag": "div", "id": "to-field", "text": None,
         "label": None, "selector": "#to-field",
         "bounds": {"x1": 400, "y1": 0, "x2": 700, "y2": 80}, "parentIndex": -1},
        {"role": "text", "tag": "span", "text": "Ankara (ESB)",
         "selector": "#to-field span",
         "bounds": {"x1": 410, "y1": 10, "x2": 690, "y2": 70}, "parentIndex": 3},
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

    async def test_a_textless_control_is_read_from_the_region_around_it(self):
        """`holds ""` was never an answer about the product. The words are
        drawn around the button the model named, which is where the tester
        reads them."""
        result = await self._assert(elementId=self.byId["from-icon"], value="IST")
        self.assertTrue(result["ok"], result["message"])
        self.assertIn("region it is drawn in", result["message"])

    async def test_the_neighbouring_field_is_still_a_failure(self):
        """The guarantee this must not trade away: origin and destination are
        different places, and a check on one must not pass on the other."""
        result = await self._assert(elementId=self.byId["from-icon"], value="ESB")
        self.assertFalse(result["ok"], result["message"])

    async def test_the_failure_says_where_the_words_actually_are(self):
        """So the next step can aim at the right element instead of dying on
        a message that only said the check failed."""
        result = await self._assert(elementId=self.byId["from-field"], value="ESB")
        self.assertFalse(result["ok"])
        self.assertIn("wrong element", result["message"])
        self.assertIn("Ankara (ESB)", result["message"])

    async def test_a_phrase_on_no_part_of_the_screen_says_that_instead(self):
        result = await self._assert(elementId=self.byId["from-field"], value="Londra")
        self.assertFalse(result["ok"])
        self.assertIn("nowhere on the screen", result["message"])

    async def test_a_region_that_is_most_of_the_screen_is_not_a_place(self):
        """"The words are inside the page" is not an answer to "which field
        holds them"."""
        page = {"nodes": [
            {"role": "group", "tag": "div", "id": "banner", "text": None,
             "label": None, "selector": "#banner",
             "bounds": {"x1": 0, "y1": 0, "x2": 1440, "y2": 880},
             "parentIndex": -1},
            {"role": "text", "tag": "p", "text": "Ankara (ESB)",
             "selector": "#banner p",
             "bounds": {"x1": 0, "y1": 0, "x2": 1440, "y2": 880},
             "parentIndex": -1},
            {"role": "button", "tag": "button", "id": "somewhere", "text": None,
             "label": None, "selector": "#somewhere",
             "bounds": {"x1": 20, "y1": 20, "x2": 60, "y2": 60},
             "parentIndex": -1},
        ], "viewport": {"width": 1440, "height": 900}}
        snapshot = WebSnapshot(page)
        target = FakeTarget(snapshot)
        target.kind = "web"
        by_id = {e.html_id: e.element_id
                 for e in snapshot.get_all_elements() if e.html_id}
        with patch("asyncio.sleep", new=AsyncMock()):
            result = await agent._execute_action(
                target,
                {"action": "assert_text", "elementId": by_id["somewhere"],
                 "value": "ESB"},
                snapshot)
        self.assertFalse(result["ok"], result["message"])

    async def test_turkish_case_does_not_decide_a_verdict(self):
        """`"İSTANBUL".lower()` is not `"istanbul"` to Python, and most
        headings on this site are capitals."""
        result = await self._assert(elementId=self.byId["from-field"],
                                    value="istanbul")
        self.assertTrue(result["ok"], result["message"])


class AnAssertionKeepsLookingForAWhile(unittest.IsolatedAsyncioTestCase):
    """A page that renders when its API answers is not finished when the click
    is, and one look 0.4s later is a coin toss that reads as a product defect
    whenever it lands wrong."""

    PAGE = {"nodes": [
        {"role": "text", "tag": "p", "text": "Aranıyor…", "selector": "p",
         "bounds": {"x1": 0, "y1": 0, "x2": 100, "y2": 20}, "parentIndex": -1},
    ]}
    ARRIVED = {"nodes": [
        {"role": "text", "tag": "p", "text": "3 uçuş bulundu", "selector": "p",
         "bounds": {"x1": 0, "y1": 0, "x2": 100, "y2": 20}, "parentIndex": -1},
        {"role": "text", "tag": "p", "text": "İstanbul - Ankara", "selector": "p+p",
         "bounds": {"x1": 0, "y1": 20, "x2": 100, "y2": 40}, "parentIndex": -1},
    ]}

    async def test_it_waits_for_the_screen_to_arrive(self):
        target = FakeTarget(WebSnapshot(self.PAGE))
        target.kind = "web"
        reads = {"n": 0}

        async def snapshot():
            reads["n"] += 1
            # The results land on the third reading, as an API answer would.
            return WebSnapshot(self.ARRIVED if reads["n"] >= 3 else self.PAGE)

        target.snapshot = snapshot
        with patch("asyncio.sleep", new=AsyncMock()), \
             patch.object(agent, "ASSERT_WAIT_SECONDS", 5.0):
            result = await agent._execute_action(
                target, {"action": "assert_text", "value": "3 uçuş bulundu"}, None)
        self.assertTrue(result["ok"], result["message"])
        self.assertGreaterEqual(reads["n"], 3, "it looked more than once")

    async def test_it_still_gives_up(self):
        target = FakeTarget(WebSnapshot(self.PAGE))
        target.kind = "web"
        with patch("asyncio.sleep", new=AsyncMock()), \
             patch.object(agent, "ASSERT_WAIT_SECONDS", 0.05):
            result = await agent._execute_action(
                target, {"action": "assert_text", "value": "3 uçuş bulundu"}, None)
        self.assertFalse(result["ok"])


class LettingTheScreenOverruleTheText(unittest.IsolatedAsyncioTestCase):
    """The tester is looking at the thing the step asked for and the check says
    it is not there.

    The real one: a 404 page that says "Hiçbir yerde var olmayan bir sayfayı
    aradınız" and never the digits 404. What keeps this from turning every red
    into green is that the overrule has to quote the screen, and the quote is
    checked against the snapshot before the verdict moves.
    """

    PAGE = {"nodes": [
        {"role": "text", "tag": "h1",
         "text": "Hiçbir yerde var olmayan bir sayfayı aradınız.",
         "selector": "h1", "bounds": {"x1": 0, "y1": 0, "x2": 400, "y2": 40},
         "parentIndex": -1},
    ]}

    def _target(self):
        target = FakeTarget(WebSnapshot(self.PAGE))
        target.kind = "web"
        return target

    async def _judge(self, reply):
        class Provider:
            async def stream(self, *a, **k):
                yield reply

        return await agent._judge_on_screen(
            self._target(), Provider(), "m", "k", None,
            asked={"action": "Sayfayı aç", "expected": "404 sayfası görünür"},
            goal="404", action={"action": "assert_text", "value": "404"},
            failure='Expected "404" on screen but it is not there.',
            screenshot="ZmFrZQ==",
        )

    async def test_an_overrule_that_quotes_the_screen_is_taken(self):
        result = await self._judge(json.dumps({
            "holds": True,
            "evidence": "Hiçbir yerde var olmayan bir sayfayı aradınız.",
            "why": "This is the not-found page, worded rather than numbered.",
        }))
        self.assertIsNotNone(result)
        self.assertTrue(result["ok"])
        self.assertTrue(result["judged"], "and it is marked as a judgement")
        self.assertIn("Judged from the screen", result["message"])

    async def test_an_overrule_with_invented_evidence_is_thrown_away(self):
        """The one failure mode worse than the one being fixed."""
        result = await self._judge(json.dumps({
            "holds": True,
            "evidence": "Hata kodu 404 sayfanın altında yazıyor",
            "why": "I can see it",
        }))
        self.assertIsNone(result, "the step keeps the answer it had")

    async def test_saying_no_changes_nothing(self):
        result = await self._judge(json.dumps({
            "holds": False, "evidence": "", "why": "The page loaded normally.",
        }))
        self.assertIsNone(result)

    async def test_an_unparseable_reply_changes_nothing(self):
        self.assertIsNone(await self._judge("I think it probably passed?"))

    async def test_a_provider_that_fails_changes_nothing(self):
        class Provider:
            async def stream(self, *a, **k):
                raise RuntimeError("provider down")
                yield ""  # pragma: no cover

        result = await agent._judge_on_screen(
            self._target(), Provider(), "m", "k", None, asked=None, goal="g",
            action={"action": "assert_text", "value": "404"},
            failure="no", screenshot="ZmFrZQ==",
        )
        self.assertIsNone(result)

    async def test_without_a_screenshot_it_does_not_guess(self):
        result = await agent._judge_on_screen(
            self._target(), None, "m", "k", None, asked=None, goal="g",
            action={"action": "assert_text", "value": "404"},
            failure="no", screenshot=None,
        )
        self.assertIsNone(result)


class NotReadingAScreenThatIsStillLoading(unittest.IsolatedAsyncioTestCase):
    """The tester watching a run said it: there is a loading panel in the
    middle of the screen and the run calls the step failed without waiting.

    Measured on the booker — pressing "Uçuş ara" puts `thy-loading-overlay` up
    at z-index 9999 over the whole viewport, then a modal loader, and they are
    still there 17.8 seconds later. Reading the page 0.3s after the click is
    reading a page that has not happened yet, and the only thing a run can
    report about a control it cannot reach is that the control is dead. That is
    where "Devam et is disabled" came from.
    """

    LOADING = {"nodes": [
        {"role": "button", "tag": "button", "id": "continue", "text": "Devam et",
         "label": None, "selector": "#continue", "enabled": False,
         "bounds": {"x1": 0, "y1": 0, "x2": 120, "y2": 40}, "parentIndex": -1},
    ], "busy": "thy-loading-overlay", "viewport": {"width": 1440, "height": 900}}

    READY = {"nodes": [
        {"role": "button", "tag": "button", "id": "continue", "text": "Devam et",
         "label": None, "selector": "#continue",
         "bounds": {"x1": 0, "y1": 0, "x2": 120, "y2": 40}, "parentIndex": -1},
    ], "viewport": {"width": 1440, "height": 900}}

    def _target(self, screens):
        """A page that hands back each screen in turn as it is asked."""
        target = FakeTarget(WebSnapshot(screens[0]))
        target.kind = "web"
        seen = {"n": 0}

        async def snapshot():
            payload = screens[min(seen["n"], len(screens) - 1)]
            seen["n"] += 1
            return WebSnapshot(payload)

        target.snapshot = snapshot
        target.reads = seen
        return target

    def test_the_overlay_is_seen_for_what_it_is(self):
        self.assertEqual(WebSnapshot(self.LOADING).busy, "thy-loading-overlay")
        self.assertIsNone(WebSnapshot(self.READY).busy)

    async def test_the_screen_is_read_after_the_panel_clears(self):
        target = self._target([self.LOADING, self.LOADING, self.READY])
        with patch("asyncio.sleep", new=AsyncMock()):
            snapshot = await agent._wait_while_busy(target)
        self.assertIsNone(snapshot.busy, "it waited for the page")
        self.assertGreaterEqual(target.reads["n"], 3)

    async def test_a_page_stuck_loading_is_handed_back_anyway(self):
        """A page behind its own spinner for ever is a finding, not a reason
        for the run to hang."""
        target = self._target([self.LOADING])
        with patch("asyncio.sleep", new=AsyncMock()), \
             patch.object(agent, "BUSY_WAIT_SECONDS", 0.05):
            snapshot = await agent._wait_while_busy(target)
        self.assertEqual(snapshot.busy, "thy-loading-overlay")

    async def test_a_control_that_comes_to_life_is_not_called_disabled(self):
        """The claim is "stays disabled", so the enabled state has to be given
        its chance to disprove it."""
        target = self._target([self.LOADING, self.READY])
        with patch("asyncio.sleep", new=AsyncMock()):
            result = await agent._execute_action(
                target, {"action": "assert_disabled", "elementId": "el_0"}, None)
        self.assertFalse(result["ok"])
        self.assertIn("still enabled", result["message"])

    async def test_a_control_that_really_stays_disabled_still_fails_it(self):
        off = json.loads(json.dumps(self.READY))
        off["nodes"][0]["enabled"] = False
        target = self._target([off])
        with patch("asyncio.sleep", new=AsyncMock()):
            result = await agent._execute_action(
                target, {"action": "assert_disabled", "elementId": "el_0"}, None)
        self.assertTrue(result["ok"])
        self.assertIn("is disabled", result["message"])


class AReplayedClickChecksWhatItIsClicking(unittest.TestCase):
    """The airport suggestions are `#booker-option-0` and up, and until they
    arrive the row at index 0 is "Tüm uçuş noktalarını gör" — which opens a
    list of every country there is and loses the scenario.

    Seventeen recorded actions across eleven scenarios click an airport by its
    position, so replaying one blind is a coin toss on how fast the list came
    back. The label recorded beside the selector settles it.
    """

    PAGE = {"nodes": [
        {"role": "button", "tag": "li", "id": "booker-option-0",
         "text": "Tüm uçuş noktalarını gör", "label": None,
         "selector": "#booker-option-0 > span:nth-of-type(2)",
         "bounds": {"x1": 0, "y1": 0, "x2": 200, "y2": 30}, "parentIndex": -1},
        {"role": "button", "tag": "button", "id": "fromPort",
         "text": "İstanbul", "label": "Nereden", "selector": "#fromPort",
         "bounds": {"x1": 0, "y1": 40, "x2": 200, "y2": 70}, "parentIndex": -1},
    ]}

    def setUp(self):
        self.snapshot = WebSnapshot(self.PAGE)

    def test_a_row_that_now_says_something_else_is_refused(self):
        moved = agent._label_moved(self.snapshot, {
            "action": "click",
            "selector": "#booker-option-0 > span:nth-of-type(2)",
            "label": "İstanbul Havalimanı (IST)",
        })
        self.assertIsNotNone(moved, "the recording must not be replayed")
        self.assertIn("Tüm uçuş", moved)

    def test_the_same_row_still_saying_the_same_thing_is_replayed(self):
        self.assertIsNone(agent._label_moved(self.snapshot, {
            "action": "click",
            "selector": "#booker-option-0 > span:nth-of-type(2)",
            "label": "Tüm uçuş noktalarını gör",
        }))

    def test_turkish_case_does_not_make_a_match_look_like_a_move(self):
        self.assertIsNone(agent._label_moved(self.snapshot, {
            "action": "click",
            "selector": "#booker-option-0 > span:nth-of-type(2)",
            "label": "TÜM UÇUŞ NOKTALARINI GÖR",
        }))

    def test_a_named_element_is_not_judged(self):
        """An id is a name the page gave the element; it does not come to mean
        something else the way a position in a list does, and judging it would
        throw away recordings for a label that was merely reworded."""
        self.assertIsNone(agent._label_moved(self.snapshot, {
            "action": "click", "selector": "#fromPort", "label": "Nereden alanı",
        }))

    def test_a_positional_click_with_no_label_is_refused(self):
        """Not hypothetical: an older generation of these recordings carried no
        label at all, and waving one through for want of anything to check
        against is exactly the blind replay this exists to stop."""
        self.assertIsNotNone(agent._label_moved(self.snapshot, {
            "action": "click", "selector": "#booker-option-0 > span:nth-of-type(2)",
        }))

    def test_a_named_selector_with_no_label_is_still_fine(self):
        """An id does not come to mean something else, so there is nothing a
        label would be protecting against."""
        self.assertIsNone(agent._label_moved(self.snapshot, {
            "action": "click", "selector": "#fromPort",
        }))

    def test_nothing_to_compare_is_not_evidence_of_a_move(self):
        self.assertIsNone(agent._label_moved(self.snapshot, {
            "action": "click", "selector": "", "label": "İstanbul",
        }))
        self.assertIsNone(agent._label_moved(None, {
            "action": "click",
            "selector": "#booker-option-0 > span:nth-of-type(2)",
            "label": "İstanbul",
        }))

    ROW_PAGE = {"nodes": [
        {"role": "button", "tag": "li", "id": "booker-option-1", "text": None,
         "label": None, "selector": "#booker-option-1",
         "bounds": {"x1": 0, "y1": 0, "x2": 200, "y2": 30}, "parentIndex": -1},
        {"role": "text", "tag": "span", "text": "İstanbul Havalimanı (IST)",
         "selector": "#booker-option-1 span",
         "bounds": {"x1": 4, "y1": 4, "x2": 196, "y2": 26}, "parentIndex": 0},
        {"role": "button", "tag": "button", "id": "plain", "text": None,
         "label": None, "selector": "#plain",
         "bounds": {"x1": 0, "y1": 40, "x2": 30, "y2": 70}, "parentIndex": -1},
    ]}

    def _row(self, html_id):
        snapshot = WebSnapshot(self.ROW_PAGE)
        return next(e for e in snapshot.get_all_elements()
                    if e.html_id == html_id)

    def test_a_control_whose_words_are_on_a_child_is_named_by_them(self):
        """Every airport pick in the suite was recorded as "button" or as
        "booker-option-1", because `describe` reads only the node's own text.
        The recording is then checked against a name that says nothing — and
        the driver builds that name itself, so fixing it anywhere but on the
        element does not reach the clicks that matter."""
        self.assertEqual(self._row("booker-option-1").describe(),
                         "İstanbul Havalimanı (IST)")
        self.assertEqual(agent._element_info(self._row("booker-option-1"))["label"],
                         "İstanbul Havalimanı (IST)")

    def test_an_element_with_no_words_anywhere_keeps_its_id(self):
        """An icon button has nothing to read; the id is the only handle there
        is, and it is better than the bare role."""
        self.assertEqual(self._row("plain").describe(), "plain")

    def test_a_row_that_has_gone_is_left_to_the_driver(self):
        """Gone is a different thing from changed, and the driver reports it
        with its own message when the action runs."""
        self.assertIsNone(agent._label_moved(self.snapshot, {
            "action": "click", "selector": "#booker-option-7 > span:nth-of-type(2)",
            "label": "İstanbul",
        }))


class ALongStepIsNotAStuckStep(unittest.IsolatedAsyncioTestCase):
    """A step is cut off for getting nowhere, not for taking a while.

    Measured on the booking scenario: the step that fills in a passenger —
    title, name, surname, date of birth, email, country code, phone, then two
    checks — spends fourteen actions and every one of them succeeds. At the old
    flat ceiling of twelve it was cut off one `step_done` from closing green,
    and the report blamed the product for the runner's own limit.
    """

    STEPS = [{"action": "Formu doldur", "expected": "Alanlar dolu"}]

    async def _run(self, actions_succeed):
        """`actions_succeed` decides what every action reports back."""
        script = ['```json\n{"type":"action","action":"click",'
                  '"elementId":"el_1","reason":"alan"}\n```'] * 30

        class Provider:
            id, label = "fake", "Fake"

            def __init__(self):
                self.i = 0

            async def stream(self, *a, **k):
                yield script[self.i] if self.i < len(script) else (
                    '```json\n{"type":"action","action":"step_done",'
                    '"value":"pass"}\n```')
                self.i += 1

        target = FakeTarget(_manager())
        target.kind = "web"
        events = []
        with patch.object(agent.providers, "get", lambda *a, **k: Provider()), \
             patch.object(agent.providers, "api_key_for", lambda *a, **k: "k"), \
             patch.object(agent.providers, "active_model", lambda *a, **k: "m"), \
             patch.object(agent, "_execute_action", AsyncMock(return_value={
                 "ok": actions_succeed, "message": "ok", "element": None})):
            async for line in agent.run_agent(
                target, "senaryo", steps=self.STEPS,
                session_state=agent.AgentSession(), use_vision=False,
            ):
                events.append(json.loads(line))
        spent = len([e for e in events if e["event"] == "step_finished"])
        closed = [e for e in events if e["event"] == "scenario_step_finished"]
        return spent, (closed[0] if closed else None)

    async def test_a_step_whose_actions_all_work_gets_room(self):
        spent, closed = await self._run(actions_succeed=True)
        self.assertGreater(spent, 12,
                           "the old ceiling would have stopped it at twelve")
        self.assertIn("24 actions", closed["message"])

    async def test_a_step_getting_nowhere_is_cut_off_sooner_than_before(self):
        """The other half: the ceiling went up, so the stuck case must not have
        gained the same rope."""
        spent, closed = await self._run(actions_succeed=False)
        self.assertLessEqual(spent, 6, "it stopped grinding early")
        self.assertIn("no progress", closed["message"])

    # That one long step must not starve the steps behind it is checked by
    # `test_a_step_that_never_closes_is_cut_off_not_left_running` above, which
    # runs a scenario whose first step never closes and asserts the second one
    # still gets its turn. Raising the per-step allowance broke that test, which
    # is how the trade-off was noticed at all.


class NotStartingAgainstAScreenThatIsNotThere(unittest.IsolatedAsyncioTestCase):
    """A closed page answered every action with "the page is gone" and the
    model kept being asked what to do about it — a whole scenario's worth of
    calls spent on a browser that was not there. And a page still painting
    looks to the model exactly like a page with nothing on it.
    """

    STEPS = [{"action": "Tek yön seç", "expected": "Tek yön seçilidir"}]

    def _provider(self, asked):
        class Provider:
            id, label = "fake", "Fake"

            async def stream(self, *a, **k):
                asked.append(1)
                yield ('```json\n{"type":"action","action":"step_done",'
                       '"value":"pass"}\n```')

        return Provider()

    async def _run(self, target):
        asked = []
        events = []
        with patch.object(agent.providers, "get", lambda *a, **k: self._provider(asked)), \
             patch.object(agent.providers, "api_key_for", lambda *a, **k: "k"), \
             patch.object(agent.providers, "active_model", lambda *a, **k: "m"), \
             patch.object(agent, "_execute_action", AsyncMock(
                 return_value={"ok": True, "message": "ok", "element": None})):
            async for line in agent.run_agent(
                target, "senaryo", steps=self.STEPS,
                session_state=agent.AgentSession(),
            ):
                events.append(json.loads(line))
        return events, asked

    async def test_a_closed_page_spends_nothing(self):
        target = FakeTarget(_manager())
        target.alive = False
        events, asked = await self._run(target)
        self.assertEqual(asked, [], "the model was never asked")
        errors = [e for e in events if e["event"] == "error"]
        self.assertTrue(errors)
        self.assertIn("no longer open", errors[0]["message"])
        self.assertFalse([e for e in events if e["event"] == "run_started"],
                         "and no run was opened for it")

    async def test_a_page_that_never_renders_spends_nothing(self):
        """Alive, but nothing on it — the case the screenshot showed."""
        class Blank:
            snapshot_id = "s"
            def get_optimized_tree_for_llm(self): return {}
            def visible_text(self): return []

        target = FakeTarget(Blank())
        events, asked = await self._run(target)
        self.assertEqual(asked, [], "the model was never asked")
        errors = [e for e in events if e["event"] == "error"]
        self.assertTrue(errors)
        self.assertIn("Nothing had rendered", errors[0]["message"])
        # Recorded, because pressing Run and getting nothing is a thing to find
        # in the history later.
        self.assertTrue([e for e in events if e["event"] == "run_started"])
        closed = [e for e in events if e["event"] == "run_closed"]
        self.assertEqual(closed[-1]["status"], "failed")

    async def test_a_page_that_dies_while_being_waited_for_says_so(self):
        target = FakeTarget(_manager())

        class Dies:
            def __init__(self): self.n = 0
            def __call__(self):
                self.n += 1
                return self.n < 2

        target.is_alive = Dies()
        with patch.object(agent, "FIRST_SCREEN_SECONDS", 5.0), \
             patch("asyncio.sleep", new=AsyncMock()):
            events, asked = await self._run(target)
        self.assertEqual(asked, [])
        errors = [e for e in events if e["event"] == "error"]
        self.assertIn("closed before the run could read it", errors[0]["message"])

    async def test_a_screen_that_is_there_runs_as_it_always_did(self):
        """The gate must not become a third way for a run to not happen."""
        events, asked = await self._run(FakeTarget(_manager()))
        self.assertTrue(asked, "the model was asked")
        self.assertTrue([e for e in events if e["event"] == "scenario_step_started"],
                        "and the scenario got as far as opening its first step")
        self.assertFalse(
            [e for e in events if e["event"] == "error"],
            "with nothing refused on the way in",
        )


class ACheckThatWasAlreadyTrueProvesNothing(unittest.TestCase):
    """Found on the swap control, and it had eight scenarios in the suite.

    Pressing swap exchanges origin and destination. A step that checks
    "İstanbul is on the screen" passes whether the swap worked or not, because
    both airports are on the screen either way — so none of those scenarios
    could ever go red. Asking the field itself is the only check that can tell
    a working swap from a broken one.
    """

    BEFORE = ["Nereden", "İstanbul", "Nereye", "Ankara Esenboğa Havalimanı",
              "(ESB)", "Uçuş ara"]

    def test_a_phrase_already_on_the_screen_is_caught(self):
        self.assertTrue(agent._already_true(
            {"action": "assert_text", "value": "Ankara"}, self.BEFORE))
        self.assertTrue(agent._already_true(
            {"action": "assert_text", "value": "İstanbul"}, self.BEFORE))

    def test_turkish_case_does_not_let_one_slip_through(self):
        self.assertTrue(agent._already_true(
            {"action": "assert_text", "value": "ISTANBUL"}, self.BEFORE))
        self.assertTrue(agent._already_true(
            {"action": "assert_text", "value": "ucus ara"}, self.BEFORE))

    def test_something_new_on_the_screen_is_a_real_check(self):
        self.assertFalse(agent._already_true(
            {"action": "assert_text", "value": "3 uçuş bulundu"}, self.BEFORE))

    def test_a_scoped_check_is_a_real_answer_about_a_real_field(self):
        """Scoping is the fix, so it must never be called worthless — a scoped
        check on a field whose value has not changed is exactly the case the
        scoped form exists to catch."""
        self.assertFalse(agent._already_true(
            {"action": "assert_text", "elementId": "el_40", "value": "Ankara"},
            self.BEFORE))
        self.assertFalse(agent._already_true(
            {"action": "assert_text", "selector": "#fromPort", "value": "Ankara"},
            self.BEFORE))

    def test_other_checks_are_not_judged_here(self):
        # assert_absent is about something leaving, and assert_visible is about
        # an element rather than a phrase; neither is the trap.
        self.assertFalse(agent._already_true(
            {"action": "assert_absent", "value": "Ankara"}, self.BEFORE))
        self.assertFalse(agent._already_true(
            {"action": "assert_visible", "elementId": "el_1"}, self.BEFORE))

    def test_the_first_step_is_not_judged(self):
        """It has no before: the screen it opens on is the screen it is about,
        so everything it proves is true on arrival by construction."""
        self.assertFalse(agent._already_true(
            {"action": "assert_text", "value": "Uçuş ara"}, self.BEFORE,
            first_step=True))

    def test_a_later_step_that_only_reads_is_still_judged(self):
        """"Check what Nereye holds now" is exactly the step that has to ask
        the field — exempting it for not having clicked would exempt the case
        this is here for."""
        self.assertTrue(agent._already_true(
            {"action": "assert_text", "value": "İstanbul"}, self.BEFORE,
            first_step=False))

    def test_nothing_to_compare_against_judges_nothing(self):
        self.assertFalse(agent._already_true(
            {"action": "assert_text", "value": "Ankara"}, None))
        self.assertFalse(agent._already_true(
            {"action": "assert_text", "value": ""}, self.BEFORE))


class HoldingAtAStepThatWentWrong(unittest.IsolatedAsyncioTestCase):
    """A run used to carry straight on from a failed step, through every step
    after it, and the tester watching could only stop it or watch it finish.

    What they want at that moment is a debugger: hold here, let me look at the
    screen, then take one more step.
    """

    ASSERT = WrittenScenarioSteps.ASSERT

    @staticmethod
    def _close(verdict, reason="r"):
        return WrittenScenarioSteps._close(verdict, reason)

    async def _run(self, script, steps, state, driver=None, attended=True,
                   execute=None):
        self_outer = self

        class Provider:
            id, label = "fake", "Fake"

            def __init__(self):
                self.i = 0

            async def stream(self, *a, **k):
                reply = (script[self.i] if self.i < len(script)
                         else self_outer._close("pass"))
                self.i += 1
                yield reply

        class Snapshot:
            snapshot_id = "s"

            def get_optimized_tree_for_llm(self):
                return {"elementId": "el_1"}

            def visible_text(self):
                return ["Bir ekran"]

        class Target:
            kind, session_id = "web", "stepping"

            def describe(self):
                return {"name": "t", "platform": "Web"}

            def is_alive(self):
                return True

            async def snapshot(self):
                return Snapshot()

            async def screenshot(self):
                return None

        events = []
        with patch.object(agent.providers, "get", lambda *a, **k: Provider()), \
             patch.object(agent.providers, "api_key_for", lambda *a, **k: "k"), \
             patch.object(agent.providers, "active_model", lambda *a, **k: "m"), \
             patch.object(agent, "_execute_action", execute or AsyncMock(
                 return_value={"ok": True, "message": "ok", "element": None})):
            async for line in agent.run_agent(
                Target(), "senaryo", steps=steps, session_state=state,
                stepping=state.stepping, attended=attended,
            ):
                event = json.loads(line)
                events.append(event)
                if driver:
                    await driver(event, state)
        return events

    @staticmethod
    def _kinds(events, *wanted):
        return [e for e in events if e["event"] in wanted]

    TWO = [
        {"action": "Adim 1", "expected": "Beklenen 1"},
        {"action": "Adim 2", "expected": "Beklenen 2"},
    ]

    async def test_a_failed_step_holds_instead_of_carrying_on(self):
        """The complaint, exactly: it should stop there and wait."""
        state = agent.AgentSession()
        seen = []

        async def watch(event, st):
            if event["event"] == "waiting":
                seen.append(event)
                # Let it take one more step, the way the button does.
                agent._sessions["stepping"] = st
                agent.resume("stepping", one_step=True)

        events = await self._run(
            [self.ASSERT, self._close("fail", "olmadi"),
             self.ASSERT, self._close("pass")],
            self.TWO, state, watch,
        )
        self.assertEqual(len(seen), 1, "it held once, at the failure")
        self.assertEqual(seen[0]["index"], 2, "before the step after the failed one")
        self.assertEqual(seen[0]["reason"], "failed")
        # And it did carry on once told to.
        verdicts = [(e["index"], e["status"])
                    for e in self._kinds(events, "scenario_step_finished")]
        self.assertEqual(verdicts, [(1, "failed"), (2, "passed")])

    async def test_debug_mode_holds_at_every_step(self):
        """Asked for before the run: click through from the first step."""
        state = agent.AgentSession()
        state.stepping = True
        held = []

        async def watch(event, st):
            if event["event"] == "waiting":
                held.append(event["index"])
                agent._sessions["stepping"] = st
                agent.resume("stepping", one_step=True)

        await self._run(
            [self.ASSERT, self._close("pass"), self.ASSERT, self._close("pass")],
            self.TWO, state, watch,
        )
        self.assertEqual(held, [2], "held before every step after the first")

    async def test_letting_it_flow_stops_the_holding(self):
        """One press of Continue and the rest of the scenario runs itself."""
        state = agent.AgentSession()
        state.stepping = True
        held = []

        async def watch(event, st):
            if event["event"] == "waiting":
                held.append(event["index"])
                agent._sessions["stepping"] = st
                agent.resume("stepping", one_step=False)

        three = self.TWO + [{"action": "Adim 3", "expected": "Beklenen 3"}]
        await self._run(
            [self.ASSERT, self._close("pass")] * 3, three, state, watch,
        )
        self.assertEqual(held, [2], "held once, then flowed to the end")

    async def test_a_flowing_run_never_waits(self):
        """The normal path must not gain a pause nobody asked for."""
        state = agent.AgentSession()
        events = await self._run(
            [self.ASSERT, self._close("pass"), self.ASSERT, self._close("pass")],
            self.TWO, state,
        )
        self.assertEqual(self._kinds(events, "waiting"), [])

    async def test_stopping_releases_a_held_run(self):
        """Otherwise Stop does nothing to a scenario sitting at a failure, and
        the only way out is to close the page."""
        state = agent.AgentSession()

        async def watch(event, st):
            if event["event"] == "waiting":
                agent._sessions["stepping"] = st
                agent.cancel("stepping")

        events = await self._run(
            [self.ASSERT, self._close("fail", "olmadi"),
             self.ASSERT, self._close("pass")],
            self.TWO, state, watch,
        )
        self.assertTrue(self._kinds(events, "cancelled"), "the run ended")
        self.assertEqual(
            [e["index"] for e in self._kinds(events, "scenario_step_started")], [1],
            "it did not open the step it was holding before",
        )

    async def test_an_unattended_run_is_never_held_by_a_failure(self):
        """A Test Set grinding through at night has nobody to press the button,
        so a hold there is a hang — the whole suite stops on one red step."""
        state = agent.AgentSession()
        events = await self._run(
            [self.ASSERT, self._close("fail", "olmadi"),
             self.ASSERT, self._close("pass")],
            self.TWO, state, attended=False,
        )
        self.assertEqual(self._kinds(events, "waiting"), [])
        self.assertFalse(state.stepping, "and it was not switched into stepping")
        verdicts = [(e["index"], e["status"])
                    for e in self._kinds(events, "scenario_step_finished")]
        self.assertEqual(verdicts, [(1, "failed"), (2, "passed")])

    async def test_a_failed_assertion_holds_rather_than_ending_the_run(self):
        """The screen that broke the assertion is the one worth looking at, and
        ending the run there is exactly what takes it away."""
        state = agent.AgentSession()
        seen = []

        async def watch(event, st):
            if event["event"] == "waiting":
                seen.append(event)
                agent._sessions["stepping"] = st
                agent.resume("stepping", one_step=True)

        # The assertion itself fails, which used to end the run on the spot.
        verdicts = iter([False, True, True])

        async def execute(*a, **k):
            ok = next(verdicts, True)
            return {"ok": ok, "message": "ok" if ok else "yok", "element": None}

        events = await self._run(
            [self.ASSERT, self.ASSERT, self._close("pass"),
             self.ASSERT, self._close("pass")],
            self.TWO, state, watch, execute=execute,
        )
        # Twice: on the assertion itself, and then at the step boundary —
        # the failure put the run into stepping, so it holds from there on.
        self.assertEqual([(e["index"], e["reason"]) for e in seen],
                         [(1, "assertion"), (2, "stepping")],
                         "on the check itself, naming the step it is inside")
        self.assertEqual(
            [(e["index"], e["status"])
             for e in self._kinds(events, "scenario_step_finished")],
            [(1, "passed"), (2, "passed")],
            "and carried on once released, instead of the run ending there",
        )

    async def test_an_unattended_failed_assertion_still_ends_the_run(self):
        """The old contract, unchanged where nobody is watching."""
        state = agent.AgentSession()

        async def execute(*a, **k):
            return {"ok": False, "message": "yok", "element": None}

        events = await self._run(
            [self.ASSERT, self._close("pass")], self.TWO, state,
            attended=False, execute=execute,
        )
        self.assertEqual(self._kinds(events, "waiting"), [])
        finished = self._kinds(events, "finished")
        self.assertEqual(finished[-1]["status"], "failed")

    async def test_the_session_is_left_flowing_for_the_next_run(self):
        state = agent.AgentSession()
        state.stepping = True

        async def watch(event, st):
            if event["event"] == "waiting":
                agent._sessions["stepping"] = st
                agent.resume("stepping", one_step=False)

        await self._run(
            [self.ASSERT, self._close("pass"), self.ASSERT, self._close("pass")],
            self.TWO, state, watch,
        )
        self.assertFalse(state.stepping)
        self.assertTrue(state.go.is_set())
