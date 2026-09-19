import json
import unittest

import scenario_writer as writer

GOOD = ("Booking - Payment | INT [USA] OW 2 ADT 1 CHD - Pay with Klarna [USD] "
        "and control PNR and ticket number")


def _reply(*entries):
    return "```json\n" + json.dumps(list(entries), ensure_ascii=False) + "\n```"


def _entry(**overrides):
    entry = {
        "title": GOOD,
        "layer": "E2E",
        "priority": "Critical",
        "goal": "Complete the payment and verify the PNR appears.",
        "rationale": "Ana yolculuk tamamlanamazsa satış durur.",
    }
    entry.update(overrides)
    return entry


class Parsing(unittest.TestCase):
    def test_reads_a_well_formed_scenario(self):
        [scenario], rejected, _q = writer.parse(_reply(_entry()))
        self.assertEqual(rejected, [])
        self.assertEqual(scenario["title"], GOOD)
        self.assertEqual(scenario["layer"], "E2E")
        self.assertEqual(scenario["priority"], "Critical")
        self.assertEqual(scenario["goal"], "Complete the payment and verify the PNR appears.")

    def test_a_bare_array_without_a_fence_is_accepted(self):
        scenarios, _, _q = writer.parse(json.dumps([_entry()]))
        self.assertEqual(len(scenarios), 1)

    def test_prose_around_the_array_is_ignored(self):
        noisy = "Here are the scenarios you asked for:\n" + _reply(_entry()) + "\nHope that helps!"
        scenarios, _, _q = writer.parse(noisy)
        self.assertEqual(len(scenarios), 1)

    def test_a_wrapped_object_is_unwrapped(self):
        scenarios, _, _q = writer.parse(json.dumps({"scenarios": [_entry()]}))
        self.assertEqual(len(scenarios), 1)

    def test_non_json_is_reported_not_raised(self):
        scenarios, rejected, _q = writer.parse("I could not do that.")
        self.assertEqual(scenarios, [])
        self.assertEqual(len(rejected), 1)


class FormatEnforcement(unittest.TestCase):
    """A scenario that breaks the format is worse than no scenario: it lands in
    a Test Set and the next person copies it."""

    def test_a_title_without_the_pipe_is_rejected(self):
        scenarios, rejected, _q = writer.parse(
            _reply(_entry(title="Booking - Payment INT OW 1 ADT - Pay with Klarna [USD]"))
        )
        self.assertEqual(scenarios, [])
        self.assertEqual(len(rejected), 1)

    def test_a_title_without_a_submodule_is_rejected(self):
        scenarios, rejected, _q = writer.parse(
            _reply(_entry(title="Booking | INT OW 1 ADT - Pay and control PNR"))
        )
        self.assertEqual(scenarios, [])
        self.assertEqual(len(rejected), 1)

    def test_a_content_half_without_an_action_split_is_rejected(self):
        scenarios, rejected, _q = writer.parse(
            _reply(_entry(title="Booking - Payment | INT OW 1 ADT pay with Klarna"))
        )
        self.assertEqual(scenarios, [])
        self.assertEqual(len(rejected), 1)

    def test_good_and_bad_are_separated_rather_than_all_dropped(self):
        scenarios, rejected, _q = writer.parse(
            _reply(_entry(), _entry(title="nonsense"), _entry())
        )
        self.assertEqual(len(scenarios), 2)
        self.assertEqual(len(rejected), 1)

    def test_a_title_is_normalised_not_rejected_for_whitespace(self):
        [scenario], _, _q = writer.parse(_reply(_entry(title="  Booking - Payment |  INT OW 1 ADT  -  Pay and control PNR  ")))
        self.assertEqual(scenario["title"], "Booking - Payment | INT OW 1 ADT - Pay and control PNR")


class PriorityRules(unittest.TestCase):
    def test_each_allowed_band_survives(self):
        for band in ("Critical", "High", "Medium"):
            [scenario], _, _q = writer.parse(_reply(_entry(priority=band)))
            self.assertEqual(scenario["priority"], band)

    def test_casing_is_forgiven(self):
        [scenario], _, _q = writer.parse(_reply(_entry(priority="critical")))
        self.assertEqual(scenario["priority"], "Critical")

    def test_an_unknown_band_falls_back_to_medium(self):
        for value in ("Blocker", "P1", "", None):
            [scenario], _, _q = writer.parse(_reply(_entry(priority=value)))
            self.assertEqual(scenario["priority"], "Medium")

    def test_low_is_lifted_at_e2e(self):
        # The standard calls Low at E2E discouraged and says the finding belongs
        # in the Component layer, so a Low E2E scenario is a mis-layered one.
        [scenario], _, _q = writer.parse(_reply(_entry(layer="E2E", priority="Low")))
        self.assertEqual(scenario["priority"], "Medium")

    def test_low_is_kept_at_component(self):
        [scenario], _, _q = writer.parse(_reply(_entry(layer="Component", priority="Low")))
        self.assertEqual(scenario["priority"], "Low")


class LayerRules(unittest.TestCase):
    def test_component_is_recognised(self):
        [scenario], _, _q = writer.parse(_reply(_entry(layer="component")))
        self.assertEqual(scenario["layer"], "Component")

    def test_an_api_layer_is_not_produced(self):
        # QAi drives a UI; an API scenario it can never run would be dead weight.
        [scenario], _, _q = writer.parse(_reply(_entry(layer="API")))
        self.assertEqual(scenario["layer"], "E2E")
        self.assertIn(scenario["layer"], writer.LAYERS)


class GoalFallback(unittest.TestCase):
    def test_a_missing_goal_falls_back_to_the_title(self):
        [scenario], _, _q = writer.parse(_reply(_entry(goal="")))
        self.assertEqual(scenario["goal"], GOOD)


class PromptContract(unittest.TestCase):
    def test_the_prompt_states_the_format_and_every_priority_band(self):
        prompt = writer.SYSTEM_PROMPT
        self.assertIn("Module - Submodule | Data&Precondition - Action&Expected Result", prompt)
        for band in writer.PRIORITIES:
            self.assertIn(band, prompt)

    def test_the_prompt_carries_the_standard_codes(self):
        for code in ("OW", "RT", "MC", "DOM", "INT", "ADT", "CHD", "XBAG", "PETC"):
            self.assertIn(code, writer.SYSTEM_PROMPT)

    def test_a_live_screen_reaches_the_model(self):
        turns = writer.build_turns(
            kind="web", tree={"role": "button", "text": "Book a flight"},
            url="https://turkishairlines.com", screenshot="Zm9v",
        )
        self.assertEqual(len(turns), 1)
        self.assertIn("Book a flight", turns[0].text)
        self.assertIn("turkishairlines.com", turns[0].text)
        self.assertEqual(turns[0].image_b64, "Zm9v")

    def test_a_text_brief_works_without_a_screen(self):
        turns = writer.build_turns(kind="web", brief="Booking - Payment, INT OW 1 ADT")
        self.assertIn("Booking - Payment", turns[0].text)
        self.assertIsNone(turns[0].image_b64)
        self.assertNotIn("ELEMENT TREE", turns[0].text)

    def test_web_scenarios_are_told_not_to_write_a_cookie_step(self):
        """The browser accepts the banner before the first step, so a step for
        it would arrive with nothing to do — and 49 of the 62 scenarios already
        written carry one."""
        turns = writer.build_turns(kind="web", brief="Booking")
        self.assertIn("cookie consent banner", turns[0].text)
        self.assertIn("Do not write a step", turns[0].text)

    def test_mobile_scenarios_are_not(self):
        """Nothing dismisses an in-app banner on a phone, so telling a mobile
        scenario to skip the step would leave it unable to get past one."""
        turns = writer.build_turns(kind="mobile", brief="Booking")
        self.assertNotIn("Do not write a step", turns[0].text)


if __name__ == "__main__":
    unittest.main()


class AskingInsteadOfGuessing(unittest.TestCase):
    """A vague brief should produce questions, not invented scenarios the team
    would have to rewrite."""

    def test_questions_are_returned_instead_of_scenarios(self):
        reply = '```json\n{"questions": ["Hangi rota?", "Hangi ödeme tipi?"]}\n```'
        scenarios, rejected, questions = writer.parse(reply)
        self.assertEqual(scenarios, [])
        self.assertEqual(rejected, [])
        self.assertEqual(questions, ["Hangi rota?", "Hangi ödeme tipi?"])

    def test_questions_work_without_a_fence(self):
        _s, _r, questions = writer.parse('{"questions": ["Hangi kanal?"]}')
        self.assertEqual(questions, ["Hangi kanal?"])

    def test_at_most_three_questions_are_kept(self):
        _s, _r, questions = writer.parse(
            '{"questions": ["a", "b", "c", "d", "e"]}'
        )
        self.assertEqual(len(questions), 3)

    def test_blank_questions_are_dropped(self):
        _s, _r, questions = writer.parse('{"questions": ["", "  ", "Gerçek soru"]}')
        self.assertEqual(questions, ["Gerçek soru"])

    def test_scenarios_still_win_when_both_could_parse(self):
        scenarios, _r, questions = writer.parse(_reply(_entry()))
        self.assertEqual(len(scenarios), 1)
        self.assertEqual(questions, [])

    def test_answers_are_carried_back_into_the_prompt(self):
        turns = writer.build_turns(
            kind="web", brief="Booking", answers="Rota: INT OW, ödeme: Klarna",
        )
        self.assertIn("Klarna", turns[0].text)
        self.assertIn("earlier questions", turns[0].text)


class HowManyContract(unittest.TestCase):
    def test_the_prompt_does_not_demand_a_fixed_count(self):
        # The count field was removed from the UI: the model covers what is
        # there rather than padding to a number.
        turns = writer.build_turns(kind="web", brief="Booking")
        self.assertNotIn("Write 5", turns[0].text)
        self.assertIn("warrants", turns[0].text)
        self.assertIn("Twenty is", writer.SYSTEM_PROMPT)


class MobileSuggestions(unittest.TestCase):
    """The three prompts above the composer are the first thing a tester reads,
    so they have to be about the app in front of them and runnable on it."""

    def test_web_only_scans_are_not_offered_on_a_device(self):
        import suggestions
        ids = {s["id"] for s in suggestions.for_snapshot(None, "mobile")}
        # links and a11y read a Playwright page; explore drives one. All three
        # can only produce an error message on a device.
        self.assertNotIn("links", ids)
        self.assertNotIn("a11y", ids)
        self.assertNotIn("explore", ids)

    def test_the_scans_are_still_offered_on_a_page(self):
        import suggestions
        ids = {s["id"] for s in suggestions.for_snapshot(None, "web")}
        self.assertIn("explore", ids)

    def test_a_device_gets_suggestions_it_can_actually_run(self):
        import suggestions
        entries = suggestions.for_snapshot(None, "mobile")
        self.assertTrue(entries)
        for entry in entries:
            self.assertEqual(entry["kind"], "prompt")

    def test_a_device_summary_survives_having_no_title_or_url(self):
        import suggestions

        class DeviceSnapshot:
            def get_all_elements(self):
                return []

        page = suggestions.describe_page(DeviceSnapshot())
        self.assertIsNone(page["title"])
        self.assertIsNone(page["url"])
        self.assertEqual(page["controls"], 0)


class GeneratedSteps(unittest.TestCase):
    """A generated scenario carries the steps it will be run by.

    The generator is how most scenarios reach a Test Set, so if it writes only a
    title and a goal, the step-by-step report has nothing to report on."""

    TITLE = "Booking - Availability | RT DOM 1 ADT - search a flight and control the results"

    def _parse(self, **overrides):
        entry = {
            "title": self.TITLE,
            "layer": "E2E",
            "priority": "Critical",
            "goal": "Search a flight.",
            "steps": [
                {"action": "Enter Istanbul as origin", "expected": "Origin reads Istanbul"},
                {"action": "Press Search", "expected": "The results list appears"},
            ],
        }
        entry.update(overrides)
        scenarios, _, _ = writer.parse(json.dumps([entry]))
        return scenarios[0]

    def test_steps_survive_parsing_in_order(self):
        steps = self._parse()["steps"]
        assert [s["action"] for s in steps] == ["Enter Istanbul as origin", "Press Search"]
        assert steps[1]["expected"] == "The results list appears"

    def test_a_scenario_without_steps_is_still_written(self):
        # It runs open-ended and is judged as a whole, exactly as before steps
        # existed — losing the scenario would be the worse outcome.
        assert self._parse(steps=None)["steps"] == []

    def test_junk_steps_do_not_reach_the_test_set(self):
        assert self._parse(steps=[{"expected": "no action"}, "  ", 7])["steps"] == []

    def test_absurdly_many_steps_are_capped(self):
        # Otherwise one malformed scenario ties up a device for hours: the
        # runner budgets its actions per step.
        many = [{"action": f"Adim {i}", "expected": "x"} for i in range(200)]
        assert len(self._parse(steps=many)["steps"]) == 40

    def test_the_prompt_asks_for_steps_with_expected_results(self):
        assert '"steps"' in writer.SYSTEM_PROMPT
        assert "expected" in writer.SYSTEM_PROMPT
