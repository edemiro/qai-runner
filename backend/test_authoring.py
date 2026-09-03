import unittest
from unittest.mock import AsyncMock, patch

import agent
import authoring

TREE = {"class": "Button", "text": "Book a flight", "elementId": "el_1"}

WRITTEN = {
    "scenarios": [{
        "title": "Booking - Home | TK Mobile guest - tap Book a flight and control the form opens",
        "layer": "E2E", "priority": "Critical", "goal": "Tap and verify.", "rationale": "Ana akış.",
    }],
    "rejected": [],
    "questions": [],
}


class AgentVocabulary(unittest.TestCase):
    """The chat is where a tester describes work, so producing scenarios has to
    be sayable there — not only on another screen."""

    def test_authoring_actions_are_recognised(self):
        for action in ("write_scenarios", "run_test_set"):
            self.assertIn(action, agent.KNOWN_ACTIONS)
            self.assertIn(action, agent.AUTHORING_ACTIONS)

    def test_they_are_not_treated_as_assertions(self):
        # Writing scenarios proves nothing about the app, so it must not be
        # able to make a run "pass".
        self.assertFalse(agent.AUTHORING_ACTIONS & agent.ASSERTION_ACTIONS)

    def test_they_do_not_end_the_run(self):
        self.assertFalse(agent.AUTHORING_ACTIONS & agent.TERMINAL_ACTIONS)

    def test_the_prompt_documents_both(self):
        prompt = agent.build_system_prompt("web")
        self.assertIn("write_scenarios", prompt)
        self.assertIn("run_test_set", prompt)
        self.assertIn("only way to do that", prompt)


class SuiteMatching(unittest.TestCase):
    """A tester names a Test Set the way they remember it, not the way it is
    stored."""

    def setUp(self):
        self.suites = [
            {"id": "a", "name": "Homepage Regression"},
            {"id": "b", "name": "Payment"},
        ]

    def test_an_exact_name_matches(self):
        with patch.object(authoring.storage, "list_suites", lambda: self.suites):
            self.assertEqual(authoring._match_suite("Payment")["id"], "b")

    def test_case_and_spacing_are_forgiven(self):
        with patch.object(authoring.storage, "list_suites", lambda: self.suites):
            self.assertEqual(authoring._match_suite("  payment ")["id"], "b")

    def test_a_partial_name_matches(self):
        with patch.object(authoring.storage, "list_suites", lambda: self.suites):
            self.assertEqual(authoring._match_suite("Homepage")["id"], "a")

    def test_an_unknown_name_matches_nothing(self):
        with patch.object(authoring.storage, "list_suites", lambda: self.suites):
            self.assertIsNone(authoring._match_suite("Check-in"))

    def test_an_empty_name_matches_nothing(self):
        with patch.object(authoring.storage, "list_suites", lambda: self.suites):
            self.assertIsNone(authoring._match_suite("   "))


class WritingScenarios(unittest.IsolatedAsyncioTestCase):
    async def test_an_unreadable_screen_is_reported_not_guessed(self):
        outcome = await authoring.write_scenarios(
            name="X", tree=None, screenshot=None, kind="web",
        )
        self.assertFalse(outcome["ok"])
        self.assertIn("could not be read", outcome["message"])

    async def test_a_missing_test_set_is_created_rather_than_refused(self):
        with patch.object(authoring.storage, "list_suites", lambda: []), \
             patch.object(authoring.storage, "create_suite", lambda **k: "new") as _c, \
             patch.object(authoring.storage, "get_suite", lambda i: {"id": "new", "name": "Homepage"}), \
             patch.object(authoring.storage, "add_case", lambda *a, **k: "case"), \
             patch.object(authoring.scenario_writer, "generate", AsyncMock(return_value=WRITTEN)):
            outcome = await authoring.write_scenarios(
                name="Homepage", tree=TREE, screenshot=None, kind="mobile",
            )
        self.assertTrue(outcome["ok"])
        self.assertEqual(outcome["written"], 1)
        self.assertIn("new Test Set", outcome["message"])

    async def test_questions_come_back_instead_of_invented_scenarios(self):
        asked = {"scenarios": [], "rejected": [], "questions": ["Hangi rota?"]}
        with patch.object(authoring.storage, "list_suites", lambda: [{"id": "a", "name": "X"}]), \
             patch.object(authoring.storage, "get_suite", lambda i: {"id": "a", "name": "X"}), \
             patch.object(authoring.scenario_writer, "generate", AsyncMock(return_value=asked)):
            outcome = await authoring.write_scenarios(
                name="X", tree=TREE, screenshot=None, kind="web",
            )
        self.assertFalse(outcome["ok"])
        self.assertEqual(outcome["questions"], ["Hangi rota?"])

    async def test_every_scenario_is_saved_with_its_priority_and_layer(self):
        saved = []
        with patch.object(authoring.storage, "list_suites", lambda: [{"id": "a", "name": "X"}]), \
             patch.object(authoring.storage, "get_suite", lambda i: {"id": "a", "name": "X"}), \
             patch.object(authoring.storage, "add_case",
                          lambda *a, **k: saved.append(k) or "case"), \
             patch.object(authoring.scenario_writer, "generate", AsyncMock(return_value=WRITTEN)):
            await authoring.write_scenarios(name="X", tree=TREE, screenshot=None, kind="web")
        self.assertEqual(saved[0]["priority"], "Critical")
        self.assertEqual(saved[0]["layer"], "E2E")


class RunningATestSet(unittest.IsolatedAsyncioTestCase):
    async def test_an_unknown_name_lists_what_does_exist(self):
        with patch.object(authoring.storage, "list_suites",
                          lambda: [{"id": "a", "name": "Homepage"}]):
            outcome = await authoring.run_test_set(name="Nope")
        self.assertFalse(outcome["ok"])
        self.assertIn("Homepage", outcome["message"])

    async def test_an_empty_test_set_says_so_rather_than_running_nothing(self):
        suite = {"id": "a", "name": "Homepage", "kind": "web"}
        with patch.object(authoring.storage, "list_suites", lambda: [suite]), \
             patch.object(authoring.storage, "get_suite", lambda i: {**suite, "cases": []}):
            outcome = await authoring.run_test_set(name="Homepage")
        self.assertFalse(outcome["ok"])
        self.assertIn("no enabled scenario", outcome["message"])

    async def test_a_mobile_test_set_runs_like_any_other(self):
        # Mobile executions run sequentially on the connected device; the runner
        # owns that rule, so authoring must not pre-emptively refuse them.
        suite = {"id": "a", "name": "Phone", "kind": "mobile"}
        cases = [{"id": "c", "enabled": True}]
        summary = {"status": "passed", "passed": 1, "failed": 0, "total": 1, "suiteRunId": "sr2"}
        with patch.object(authoring.storage, "list_suites", lambda: [suite]), \
             patch.object(authoring.storage, "get_suite", lambda i: {**suite, "cases": cases}), \
             patch.object(authoring.suite_runner, "run_suite_collect",
                          AsyncMock(return_value=summary)):
            outcome = await authoring.run_test_set(name="Phone")
        self.assertTrue(outcome["ok"])
        self.assertEqual(outcome["suiteRunId"], "sr2")

    async def test_a_finished_run_reports_the_tally(self):
        suite = {"id": "a", "name": "Homepage", "kind": "web"}
        cases = [{"id": "c", "enabled": True}]
        summary = {"status": "passed", "passed": 3, "failed": 1, "total": 4, "suiteRunId": "sr1"}
        with patch.object(authoring.storage, "list_suites", lambda: [suite]), \
             patch.object(authoring.storage, "get_suite", lambda i: {**suite, "cases": cases}), \
             patch.object(authoring.suite_runner, "run_suite_collect",
                          AsyncMock(return_value=summary)):
            outcome = await authoring.run_test_set(name="Homepage")
        self.assertTrue(outcome["ok"])
        self.assertEqual(outcome["suiteRunId"], "sr1")
        self.assertIn("3 passed, 1 failed of 4", outcome["message"])

    async def test_the_execution_name_is_handed_to_the_runner(self):
        # Distinct from the Test Set's own name: a set can be run many times,
        # and this is what lets each run be told apart in Test Executions.
        suite = {"id": "a", "name": "Homepage", "kind": "web"}
        cases = [{"id": "c", "enabled": True}]
        summary = {"status": "passed", "passed": 1, "failed": 0, "total": 1, "suiteRunId": "sr3"}
        collect = AsyncMock(return_value=summary)
        with patch.object(authoring.storage, "list_suites", lambda: [suite]), \
             patch.object(authoring.storage, "get_suite", lambda i: {**suite, "cases": cases}), \
             patch.object(authoring.suite_runner, "run_suite_collect", collect):
            await authoring.run_test_set(name="Homepage", execution_name="Regresyon")
        self.assertEqual(collect.call_args.kwargs["name"], "Regresyon")

    async def test_a_total_wipeout_surfaces_the_likely_cause(self):
        # "0 passed, 20 failed of 20" alone is not something a tester can act
        # on. When every case fails, the shared reason(s) are worth more than
        # the count.
        suite = {"id": "a", "name": "Homepage", "kind": "mobile"}
        cases = [{"id": "c", "enabled": True}]
        results = [
            {"status": "failed", "error": "The Anthropic account has no credit."},
            {"status": "failed", "error": "The Anthropic account has no credit."},
            {"status": "failed", "error": "A different, rarer failure."},
        ]
        summary = {
            "status": "failed", "passed": 0, "failed": 3, "total": 3,
            "suiteRunId": "sr4", "results": results,
        }
        with patch.object(authoring.storage, "list_suites", lambda: [suite]), \
             patch.object(authoring.storage, "get_suite", lambda i: {**suite, "cases": cases}), \
             patch.object(authoring.suite_runner, "run_suite_collect",
                          AsyncMock(return_value=summary)):
            outcome = await authoring.run_test_set(name="Homepage")
        self.assertFalse(outcome["ok"])
        self.assertIn("no credit", outcome["message"])
        self.assertIn("(2x)", outcome["message"])

    async def test_a_mixed_result_is_still_reported_ok(self):
        # Some cases failing is a normal, useful outcome — real bugs were
        # found — not a sign the mechanism itself is broken.
        suite = {"id": "a", "name": "Homepage", "kind": "web"}
        cases = [{"id": "c", "enabled": True}]
        summary = {
            "status": "failed", "passed": 3, "failed": 1, "total": 4,
            "suiteRunId": "sr5",
            "results": [{"status": "failed", "error": "a selector stopped matching"}],
        }
        with patch.object(authoring.storage, "list_suites", lambda: [suite]), \
             patch.object(authoring.storage, "get_suite", lambda i: {**suite, "cases": cases}), \
             patch.object(authoring.suite_runner, "run_suite_collect",
                          AsyncMock(return_value=summary)):
            outcome = await authoring.run_test_set(name="Homepage")
        self.assertTrue(outcome["ok"])
        self.assertIn("a selector stopped matching", outcome["message"])


class NamingAnUnnamedTestSet(unittest.TestCase):
    """"Chat Test Set" says only where it was made — the least useful fact about
    it. Three of those and nobody can tell which is which."""

    def test_the_app_name_on_screen_is_used(self):
        tree = {"text": "Turkish Airlines", "elementId": "el_1"}
        self.assertEqual(authoring._name_from_screen(tree, None), "Turkish Airlines")

    def test_the_content_description_is_a_fallback(self):
        tree = {"content-desc": "TK Mobile"}
        self.assertEqual(authoring._name_from_screen(tree, None), "TK Mobile")

    def test_the_host_is_used_when_the_screen_is_unnamed(self):
        name = authoring._name_from_screen({}, "https://www.turkishairlines.com/tr-tr/")
        self.assertEqual(name, "turkishairlines.com")

    def test_a_long_name_is_trimmed_rather_than_stored_whole(self):
        tree = {"text": "x" * 200}
        self.assertEqual(len(authoring._name_from_screen(tree, None)), 60)

    def test_nothing_to_go_on_still_yields_a_name(self):
        self.assertEqual(authoring._name_from_screen(None, None), "Untitled Test Set")


if __name__ == "__main__":
    unittest.main()
