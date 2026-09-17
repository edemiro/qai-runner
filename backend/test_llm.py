import json
import os
import tempfile
import unittest
from unittest.mock import patch

import config
from llm import ProviderError, Turn
from llm.claude import ClaudeProvider, _messages as claude_messages, _request as claude_request
from llm.gemini import _contents as gemini_contents
from llm.openai_provider import _messages as openai_messages

SHOT = "iVBORw0KGgo="  # not a real PNG; the providers only pass it through


class _FakeTypes:
    """Stands in for google.genai.types so the mapping can be tested offline."""

    class Part:
        @staticmethod
        def from_bytes(data, mime_type):
            return {"inline_data": {"mime_type": mime_type, "bytes": len(data)}}


TURNS = [
    Turn(role="user", text="Log in"),
    Turn(role="assistant", text="I will tap Sign in."),
    Turn(role="user", text="Step 1 succeeded.", image_b64=SHOT),
]


class TestMessageMapping(unittest.TestCase):
    """Each provider names the assistant role and packs images differently;
    getting either wrong fails only at request time, against a paid API."""

    def test_gemini_renames_assistant_to_model(self):
        contents = gemini_contents(TURNS, _FakeTypes)
        self.assertEqual([c["role"] for c in contents], ["user", "model", "user"])

    def test_gemini_attaches_the_image_as_a_part(self):
        contents = gemini_contents(TURNS, _FakeTypes)
        self.assertEqual(len(contents[2]["parts"]), 2)
        self.assertIn("inline_data", contents[2]["parts"][1])

    def test_claude_keeps_assistant_and_uses_base64_source(self):
        messages = claude_messages(TURNS)
        self.assertEqual([m["role"] for m in messages], ["user", "assistant", "user"])
        image_block = messages[2]["content"][0]
        self.assertEqual(image_block["type"], "image")
        self.assertEqual(image_block["source"]["type"], "base64")
        self.assertEqual(image_block["source"]["data"], SHOT)

    def test_claude_text_only_turn_stays_a_plain_string(self):
        self.assertEqual(claude_messages(TURNS)[0]["content"], "Log in")

    def test_openai_puts_system_first_and_uses_a_data_uri(self):
        messages = openai_messages("SYS", TURNS)
        self.assertEqual(messages[0], {"role": "system", "content": "SYS"})
        self.assertEqual([m["role"] for m in messages[1:]], ["user", "assistant", "user"])
        image_block = messages[3]["content"][1]
        self.assertEqual(image_block["type"], "image_url")
        self.assertTrue(image_block["image_url"]["url"].startswith("data:image/png;base64,"))


class TestClaudeRequest(unittest.TestCase):

    def test_no_sampling_parameters_are_sent(self):
        """temperature/top_p are rejected on Opus 5 and the 4.6+ family — the
        other providers' 0.2 default must not leak into this request."""
        payload = claude_request("SYS", TURNS, "claude-opus-5")
        self.assertNotIn("temperature", payload)
        self.assertNotIn("top_p", payload)

    def test_adaptive_thinking_without_a_token_budget(self):
        payload = claude_request("SYS", TURNS, "claude-opus-5")
        self.assertEqual(payload["thinking"], {"type": "adaptive"})
        self.assertNotIn("budget_tokens", payload["thinking"])

    def test_effort_is_nested_inside_output_config(self):
        payload = claude_request("SYS", TURNS, "claude-opus-5")
        self.assertEqual(payload["output_config"]["effort"], "medium")
        self.assertNotIn("effort", payload)

    def test_effort_is_chosen_per_run(self):
        for effort in ("low", "medium", "high"):
            payload = claude_request("SYS", TURNS, "claude-opus-5", effort)
            self.assertEqual(payload["output_config"]["effort"], effort)

    def test_system_prompt_is_a_top_level_parameter(self):
        payload = claude_request("SYS", TURNS, "claude-opus-5")
        self.assertEqual([block["text"] for block in payload["system"]], ["SYS"])
        self.assertNotIn("system", [m["role"] for m in payload["messages"]])

    def test_settled_history_is_marked_for_caching(self):
        # A run appends to history and never rewrites it, so everything before
        # the newest screen is a stable prefix worth reading from cache.
        turns = [
            Turn(role="user", text="step 1 screen"),
            Turn(role="assistant", text="clicked Menu"),
            Turn(role="user", text="step 2 screen"),
        ]
        payload = claude_request("SYS", turns, "claude-opus-5")
        history_end = payload["messages"][-2]["content"]
        self.assertEqual(history_end[-1]["cache_control"], {"type": "ephemeral"})

    def test_the_newest_screen_is_left_out_of_the_cache(self):
        # Its tree and frame differ every step; marking it would only write an
        # entry that the next step can never read.
        turns = [
            Turn(role="user", text="step 1 screen"),
            Turn(role="assistant", text="clicked Menu"),
            Turn(role="user", text="step 2 screen"),
        ]
        payload = claude_request("SYS", turns, "claude-opus-5")
        newest = payload["messages"][-1]["content"]
        blocks = [newest] if isinstance(newest, str) else newest
        for block in blocks:
            if isinstance(block, dict):
                self.assertNotIn("cache_control", block)

    def test_a_first_step_has_no_history_to_cache(self):
        payload = claude_request("SYS", [Turn(role="user", text="only screen")], "claude-opus-5")
        self.assertEqual(len(payload["messages"]), 1)
        content = payload["messages"][0]["content"]
        self.assertNotIn("cache_control", json.dumps(content))

    def test_system_prompt_is_marked_for_caching(self):
        # It is identical on every step of a run, and a cache read costs about a
        # tenth of a fresh one. The marker only fits on a block, which is why
        # the system prompt is not sent as a bare string.
        payload = claude_request("SYS", TURNS, "claude-opus-5")
        self.assertEqual(
            payload["system"][0]["cache_control"], {"type": "ephemeral"}
        )


class TestRegistry(unittest.TestCase):

    def setUp(self):
        self._env = dict(os.environ)
        handle, self.env_path = tempfile.mkstemp(suffix=".env")
        os.close(handle)
        for key in ("LLM_PROVIDER", "LLM_MODEL", "DEFAULT_MODEL",
                    "GEMINI_API_KEY", "ANTHROPIC_API_KEY", "OPENAI_API_KEY"):
            os.environ.pop(key, None)
        self._patch = patch.object(config, "ENV_PATH", self.env_path)
        self._patch.start()

    def tearDown(self):
        self._patch.stop()
        os.environ.clear()
        os.environ.update(self._env)
        os.unlink(self.env_path)

    def _registry(self):
        from llm import registry
        return registry

    def test_keys_are_stored(self):
        registry = self._registry()
        registry.save("claude", "claude-opus-5", "sk-ant-test")
        self.assertEqual(registry.api_key_for("claude"), "sk-ant-test")
        self.assertEqual(registry.active_provider_id(), "claude")

    def test_switching_models_needs_no_retyped_key(self):
        registry = self._registry()
        registry.save("claude", "claude-opus-5", "sk-ant-test")
        registry.save("claude", "claude-sonnet-5", None)  # no key re-entered

        self.assertEqual(registry.active_model(), "claude-sonnet-5")
        self.assertEqual(registry.api_key_for("claude"), "sk-ant-test")

    def test_selecting_with_no_key_is_refused(self):
        registry = self._registry()
        with self.assertRaises(ProviderError):
            registry.save("claude", "claude-opus-5", None)

    def test_blank_model_falls_back_to_the_provider_default(self):
        registry = self._registry()
        registry.save("claude", "", "sk-ant-test")
        self.assertEqual(registry.active_model(), "claude-opus-5")

    def test_unknown_provider_is_rejected(self):
        registry = self._registry()
        with self.assertRaises(ProviderError):
            registry.save("llama", "whatever", "key")

    def test_catalog_reports_which_keys_are_saved(self):
        registry = self._registry()
        registry.save("claude", "claude-opus-5", "sk-ant-test")
        by_id = {entry["id"]: entry for entry in registry.catalog()}
        self.assertTrue(by_id["claude"]["configured"])

    def test_catalog_only_offers_claude(self):
        registry = self._registry()
        self.assertEqual([entry["id"] for entry in registry.catalog()], ["claude"])

    def test_env_write_preserves_unrelated_lines(self):
        with open(self.env_path, "w", encoding="utf-8") as f:
            f.write("# a comment\nAPPIUM_HOST=http://localhost:4723\n")

        config.write_env({"LLM_PROVIDER": "claude"})

        with open(self.env_path, encoding="utf-8") as f:
            body = f.read()
        self.assertIn("# a comment", body)
        self.assertIn("APPIUM_HOST=http://localhost:4723", body)
        self.assertIn("LLM_PROVIDER=claude", body)

    def test_env_write_replaces_rather_than_appends(self):
        config.write_env({"LLM_MODEL": "gpt-4o"})
        config.write_env({"LLM_MODEL": "claude-opus-5"})
        with open(self.env_path, encoding="utf-8") as f:
            body = f.read()
        self.assertEqual(body.count("LLM_MODEL="), 1)
        self.assertIn("LLM_MODEL=claude-opus-5", body)


class TestGeminiErrorTranslation(unittest.TestCase):
    """The SDK raises raw JSON dumps. Showing those in the UI leaves a tester
    guessing; each one has a different thing to do about it."""

    def _translate(self, message):
        from llm.gemini import _translate
        return _translate(Exception(message))

    def test_quota_exhaustion_says_what_to_do(self):
        error = self._translate(
            "429 RESOURCE_EXHAUSTED. {'error': {'code': 429, 'message': 'Quota exceeded'}}"
        )
        self.assertEqual(error.kind, "rate_limit")
        self.assertIn("quota", str(error).lower())
        self.assertIn("Settings", str(error))
        self.assertNotIn("RESOURCE_EXHAUSTED", str(error))

    def test_a_bad_key_is_named_as_a_bad_key(self):
        error = self._translate("400 API_KEY_INVALID: API key not valid")
        self.assertEqual(error.kind, "auth")

    def test_a_safety_block_suggests_turning_vision_off(self):
        error = self._translate("Response blocked by SAFETY setting")
        self.assertEqual(error.kind, "refusal")
        self.assertIn("Vision", str(error))

    def test_an_unknown_error_is_passed_through_but_truncated(self):
        error = self._translate("x" * 900)
        self.assertLess(len(str(error)), 350)


class TestAnthropicWorkspace(unittest.TestCase):
    """Identity-linked Anthropic keys are rejected outright unless the request
    names the workspace it acts in. Classic keys must not be given the header."""

    def setUp(self):
        self._previous = os.environ.pop("ANTHROPIC_WORKSPACE_ID", None)

    def tearDown(self):
        os.environ.pop("ANTHROPIC_WORKSPACE_ID", None)
        if self._previous is not None:
            os.environ["ANTHROPIC_WORKSPACE_ID"] = self._previous

    def test_no_workspace_id_means_no_header(self):
        from llm.claude import _client
        captured = {}

        class FakeSDK:
            @staticmethod
            def AsyncAnthropic(**kwargs):
                captured.update(kwargs)
                return object()

        _client(FakeSDK, "sk-ant-x")
        self.assertIsNone(captured["default_headers"])

    def test_a_workspace_id_is_sent_as_a_header(self):
        from llm.claude import _client
        os.environ["ANTHROPIC_WORKSPACE_ID"] = "wrkspc_abc123"
        captured = {}

        class FakeSDK:
            @staticmethod
            def AsyncAnthropic(**kwargs):
                captured.update(kwargs)
                return object()

        _client(FakeSDK, "sk-ant-x")
        self.assertEqual(captured["default_headers"], {"anthropic-workspace-id": "wrkspc_abc123"})

    def test_the_missing_workspace_error_says_where_to_find_it(self):
        from llm.claude import _explain

        class FakeError(Exception):
            message = (
                "anthropic-workspace-id is required when authenticating with an "
                "identity-linked API key; send the id of the workspace this request acts in."
            )
            status_code = 400

        error = _explain(None, FakeError())
        self.assertEqual(error.kind, "auth")
        self.assertIn("wrkspc_", str(error))
        self.assertIn("Workspace ID", str(error))

    def test_an_empty_credit_balance_is_named(self):
        from llm.claude import _explain

        class FakeError(Exception):
            message = "Your credit balance is too low to access the Anthropic API"
            status_code = 400

        self.assertIn("credit", str(_explain(None, FakeError())).lower())


class TestStorageResilience(unittest.TestCase):
    """The database file can disappear under a running server — a cleanup
    script, a restore, a stray delete. Creating the schema only at startup
    turns that into a crash mid-request, which the browser can only report as
    an unexplained network error."""

    def setUp(self):
        import storage
        self.storage = storage
        handle, self.db_path = tempfile.mkstemp(suffix=".db")
        os.close(handle)
        os.unlink(self.db_path)
        self._patch = patch.object(storage, "DB_PATH", self.db_path)
        self._patch.start()

    def tearDown(self):
        self._patch.stop()
        if os.path.exists(self.db_path):
            os.unlink(self.db_path)

    def test_writing_to_a_missing_database_recreates_the_schema(self):
        run_id = self.storage.create_run("first run")
        self.assertTrue(run_id)

    def test_a_deleted_database_does_not_break_the_next_run(self):
        self.storage.create_run("before")
        os.unlink(self.db_path)          # exactly what a cleanup script does
        run_id = self.storage.create_run("after")
        self.assertTrue(run_id)
        self.assertEqual(len(self.storage.list_runs()), 1)

    def test_reads_also_survive_a_missing_database(self):
        os.path.exists(self.db_path) and os.unlink(self.db_path)
        self.assertEqual(self.storage.list_runs(), [])
        self.assertIsNone(self.storage.get_run("nope"))

    def test_a_step_reports_that_it_has_a_screenshot_without_shipping_it(self):
        """The report lists steps before anyone opens a frame; if the flag is
        only computed in the include-everything branch, the list always says
        there are no screenshots."""
        run_id = self.storage.create_run("run")
        self.storage.add_step(run_id, "click", "passed", screenshot="ZmFrZS1wbmc=")
        self.storage.add_step(run_id, "wait", "passed")

        run = self.storage.get_run(run_id)  # default: no payloads
        with_shot, without_shot = run["steps"]
        self.assertTrue(with_shot["hasScreenshot"])
        self.assertIsNone(with_shot["screenshot"])
        self.assertFalse(without_shot["hasScreenshot"])

    def test_the_frame_itself_is_available_on_request(self):
        run_id = self.storage.create_run("run")
        step_id = self.storage.add_step(run_id, "click", "passed", screenshot="ZmFrZS1wbmc=")
        self.assertEqual(self.storage.get_step_screenshot(run_id, step_id), "ZmFrZS1wbmc=")


if __name__ == "__main__":
    unittest.main()
