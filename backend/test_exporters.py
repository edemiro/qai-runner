import ast
import unittest

import exporters

RUN = {
    "title": 'Login "quoted" flow',
    "goal": "Log in and verify the home screen",
    "platform": "Android",
    "device_udid": "ABC123",
    "app_id": "com.example.app",
    "steps": [
        {
            "action": "click", "status": "passed", "target": "Login",
            "reason": "Open the login\nform",
            "element": {"id": "loginBtn", "text": "Login", "xpath": "/x/y[1]"},
        },
        {
            "action": "type", "status": "passed", "target": "Email", "value": 'te"st@test.com',
            "reason": "Fill the email field",
            "element": {"id": "emailInput", "xpath": "/x/y[2]"},
        },
        {
            "action": "scroll", "status": "passed", "value": "down", "reason": "Reveal the button",
            "element": {},
        },
        {
            "action": "key", "status": "passed", "value": "back", "reason": "Dismiss the keyboard",
            "element": {},
        },
        {
            "action": "assert_visible", "status": "passed", "target": 'Home "tab"',
            "reason": "Login landed on home",
            "element": {"id": "homeTab", "xpath": "/x/y[3]"},
        },
        {
            "action": "assert_text", "status": "passed", "value": 'Welcome "back"',
            "reason": "Greeting is shown", "element": {},
        },
        {
            "action": "click", "status": "failed", "target": "Never happened",
            "element": {"id": "ghost", "xpath": "/x/y[9]"},
        },
    ],
}


class TestExporters(unittest.TestCase):

    def test_pytest_output_is_valid_python(self):
        """Quotes inside labels and values used to produce a file that would not
        even parse, which makes the export worthless."""
        out = exporters.export(RUN, "pytest")
        ast.parse(out["content"])
        self.assertTrue(out["filename"].startswith("test_"))
        self.assertTrue(out["filename"].endswith(".py"))

    def test_failed_steps_are_excluded(self):
        content = exporters.export(RUN, "pytest")["content"]
        self.assertNotIn("ghost", content)

    def test_multiline_reason_stays_on_one_comment_line(self):
        content = exporters.export(RUN, "pytest")["content"]
        self.assertIn("# Open the login form", content)

    def test_locator_prefers_resource_id(self):
        content = exporters.export(RUN, "pytest")["content"]
        self.assertIn('AppiumBy.ID, "loginBtn"', content)

    def test_ios_run_uses_xcuitest_options(self):
        ios_run = {**RUN, "platform": "iOS"}
        content = exporters.export(ios_run, "pytest")["content"]
        ast.parse(content)
        self.assertIn("XCUITestOptions", content)
        self.assertIn("bundle_id", content)

    def test_webdriverio_output_contains_each_action(self):
        content = exporters.export(RUN, "webdriverio")["content"]
        for fragment in ("click()", "setValue(", "toBeDisplayed()", "toContain(", "pressKeyCode("):
            self.assertIn(fragment, content)

    def test_gherkin_uses_then_for_assertions(self):
        content = exporters.export(RUN, "gherkin")["content"]
        self.assertIn("Feature:", content)
        self.assertIn("Scenario:", content)
        self.assertIn('Then I should see "Welcome "back""', content)

    def test_empty_run_still_produces_a_parseable_file(self):
        empty = {"title": "Nothing happened", "goal": "n/a", "platform": "Android", "steps": []}
        ast.parse(exporters.export(empty, "pytest")["content"])

    def test_unknown_format_is_rejected(self):
        with self.assertRaises(ValueError):
            exporters.export(RUN, "cucumber-ruby")


WEB_RUN = {
    "title": "Search a flight",
    "goal": "Search IST to LHR and verify results",
    "platform": "Web",
    "app_id": "https://example.test/",
    "device_name": "https://example.test/",
    "steps": [
        {
            "action": "type", "status": "passed", "target": "Nereden", "value": "İstanbul",
            "reason": "Fill the origin", "element": {"xpath": '[data-testid="origin"]'},
        },
        {
            "action": "click", "status": "passed", "target": "Ara", "reason": "Submit the search",
            "element": {"xpath": '[data-testid="search"]'},
        },
        {
            "action": "scroll", "status": "passed", "value": "down", "reason": "Reveal results",
            "element": {},
        },
        {
            "action": "key", "status": "passed", "value": "back", "reason": "Go back", "element": {},
        },
        {
            "action": "assert_text", "status": "passed", "value": "Uçuş bulundu",
            "reason": "Results arrived", "element": {},
        },
    ],
}


class TestWebExporters(unittest.TestCase):

    def test_a_web_run_offers_playwright_not_appium(self):
        """Offering an Appium script for a browser run would produce a file
        that cannot run at all."""
        formats = exporters.formats_for(WEB_RUN)
        self.assertIn("playwright-python", formats)
        self.assertNotIn("pytest", formats)
        self.assertNotIn("webdriverio", formats)

    def test_a_mobile_run_still_offers_appium(self):
        formats = exporters.formats_for(RUN)
        self.assertIn("pytest", formats)
        self.assertNotIn("playwright-python", formats)

    def test_playwright_python_is_valid_python(self):
        out = exporters.export(WEB_RUN, "playwright-python")
        ast.parse(out["content"])
        self.assertTrue(out["filename"].endswith(".py"))

    def test_playwright_python_uses_the_recorded_selector(self):
        content = exporters.export(WEB_RUN, "playwright-python")["content"]
        self.assertIn('page.locator("[data-testid=\\"origin\\"]").fill', content)

    def test_playwright_python_starts_at_the_recorded_url(self):
        content = exporters.export(WEB_RUN, "playwright-python")["content"]
        self.assertIn('START_URL = "https://example.test/"', content)
        self.assertIn("page.goto(START_URL)", content)

    def test_playwright_maps_back_to_history_navigation(self):
        content = exporters.export(WEB_RUN, "playwright-python")["content"]
        self.assertIn("page.go_back()", content)

    def test_playwright_ts_contains_each_action(self):
        content = exporters.export(WEB_RUN, "playwright-ts")["content"]
        for fragment in (".fill(", ".click()", "page.mouse.wheel", "page.goBack()", "toBeVisible()"):
            self.assertIn(fragment, content)
        self.assertTrue(exporters.export(WEB_RUN, "playwright-ts")["filename"].endswith(".spec.ts"))

    def test_a_web_run_defaults_to_playwright(self):
        self.assertEqual(exporters.export(WEB_RUN, "")["format"], "playwright-python")

    def test_a_mobile_run_defaults_to_pytest(self):
        self.assertEqual(exporters.export(RUN, "")["format"], "pytest")


if __name__ == "__main__":
    unittest.main()
