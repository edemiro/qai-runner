"""Suites, data-driven expansion, CI reports, healing and visual baselines."""

import io
import json
import os
import tempfile
from unittest.mock import patch
from xml.etree import ElementTree as ET

import pytest
from PIL import Image, ImageDraw

import healing
import reporters
import storage
import suite_runner as runner
import visual


# --------------------------------------------------------------------------- #
# Isolated database per test, so ordering can never matter
# --------------------------------------------------------------------------- #

@pytest.fixture(autouse=True)
def temp_db(monkeypatch, tmp_path):
    monkeypatch.setattr(storage, "DB_PATH", str(tmp_path / "test.db"))
    monkeypatch.setattr(storage, "_schema_ready", False)
    storage.init_db()
    yield


@pytest.fixture
def suite_with_cases():
    suite_id = storage.create_suite("Checkout", tags=["smoke"])
    plain = storage.add_case(suite_id, "Sepete ekle", "bir urun ekle", url="http://x/", tags=["smoke"])
    driven = storage.add_case(
        suite_id, "Kart tipleri", "{{kart}} ile ode", url="http://x/pay", tags=["payment"],
        dataset=[{"kart": "visa"}, {"kart": "mastercard"}],
    )
    return suite_id, plain, driven


# --------------------------------------------------------------------------- #
# Dataset substitution
# --------------------------------------------------------------------------- #

def test_placeholders_are_filled_from_the_row():
    assert runner.substitute("{{a}} ve {{b}}", {"a": "x", "b": "y"}) == "x ve y"


def test_unknown_placeholder_is_left_visible():
    """Blanking it would hide the typo; leaving it shows up in the report."""
    assert runner.substitute("{{emial}}", {"email": "a@b"}) == "{{emial}}"


def test_substitution_without_a_row_is_a_no_op():
    assert runner.substitute("plain text", None) == "plain text"


def test_non_string_values_are_stringified():
    assert runner.substitute("{{n}} adet", {"n": 3}) == "3 adet"


def test_a_case_without_a_dataset_runs_once():
    cases = [{"id": "c", "name": "One", "goal": "g", "dataset": None}]
    assert len(runner.expand_cases(cases)) == 1


def test_a_dataset_becomes_one_execution_per_row():
    cases = [{"id": "c", "name": "Driven", "goal": "g",
              "dataset": [{"k": 1}, {"k": 2}, {"k": 3}]}]
    executions = runner.expand_cases(cases)
    assert len(executions) == 3
    # Each label must be distinguishable in the report.
    assert len({e["label"] for e in executions}) == 3


def test_malformed_dataset_rows_are_skipped():
    cases = [{"id": "c", "name": "D", "goal": "g", "dataset": [{"k": 1}, "nope", None]}]
    assert len(runner.expand_cases(cases)) == 1


# --------------------------------------------------------------------------- #
# Suites and tag selection
# --------------------------------------------------------------------------- #

def test_a_suite_reports_its_enabled_case_count(suite_with_cases):
    suite_id, _, _ = suite_with_cases
    assert storage.get_suite(suite_id)["case_count"] == 2


def test_tags_select_cases(suite_with_cases):
    suite_id, plain, _ = suite_with_cases
    selected = storage.select_cases(suite_id, ["smoke"])
    assert [c["id"] for c in selected] == [plain]


def test_a_disabled_case_is_not_selected(suite_with_cases):
    suite_id, plain, _ = suite_with_cases
    storage.update_case(plain, enabled=False)
    assert plain not in [c["id"] for c in storage.select_cases(suite_id)]


def test_tag_matching_does_not_match_a_prefix(suite_with_cases):
    """'smoke' must not match a case tagged 'smoketest'."""
    suite_id, _, _ = suite_with_cases
    storage.add_case(suite_id, "Other", "g", tags=["smoketest"])
    assert len(storage.select_cases(suite_id, ["smoke"])) == 1


def test_deleting_a_suite_takes_its_cases(suite_with_cases):
    suite_id, plain, _ = suite_with_cases
    storage.delete_suite(suite_id)
    assert storage.get_case(plain) is None


def test_run_tags_are_stored_and_filterable():
    run_id = storage.create_run("g", tags=["Smoke", "web"])
    assert set(storage.get_run(run_id)["tags"]) == {"smoke", "web"}
    assert [r["id"] for r in storage.list_runs(tag="smoke")] == [run_id]
    assert storage.list_runs(tag="nope") == []


# --------------------------------------------------------------------------- #
# Page events change the verdict
# --------------------------------------------------------------------------- #

def test_page_events_are_recorded_against_the_run():
    run_id = storage.create_run("g")
    storage.add_page_events(run_id, [
        {"kind": "console", "level": "error", "text": "boom"},
        {"kind": "httperror", "level": "error", "text": "HTTP 500", "url": "/api", "status": 500},
    ])
    events = storage.get_run(run_id)["pageEvents"]
    assert len(events) == 2
    assert events[1]["status"] == 500


def test_recording_no_events_is_harmless():
    run_id = storage.create_run("g")
    assert storage.add_page_events(run_id, []) == 0


# --------------------------------------------------------------------------- #
# CI reports
# --------------------------------------------------------------------------- #

def _finished_run(status="passed", **kwargs):
    run_id = storage.create_run(kwargs.pop("goal", "do a thing"), **kwargs)
    storage.add_step(run_id, "click", "passed", target="Ara", message="Clicked")
    storage.finish_run(run_id, status, error="it broke" if status == "failed" else None)
    return storage.get_run(run_id)


def test_junit_is_well_formed_xml():
    xml = reporters.junit_xml([_finished_run(), _finished_run("failed")], "Nightly")
    suite = ET.fromstring(xml).find("testsuite")
    assert suite.get("tests") == "2"
    assert suite.get("failures") == "1"


def test_a_failed_run_carries_its_reason_into_the_report():
    xml = reporters.junit_xml([_finished_run("failed")])
    failure = ET.fromstring(xml).find("testsuite/testcase/failure")
    assert failure is not None
    assert "it broke" in failure.text


def test_control_characters_do_not_break_the_report():
    """A NUL in a page's error text would otherwise make the XML unparseable."""
    run = _finished_run("failed")
    run["title"] = "bad\x00title"
    run["error"] = "boom\x07\x0b"
    ET.fromstring(reporters.junit_xml([run]))  # must not raise


def test_an_unfinished_run_is_reported_as_skipped():
    run_id = storage.create_run("never finished")
    xml = reporters.junit_xml([storage.get_run(run_id)])
    assert ET.fromstring(xml).find("testsuite/testcase/skipped") is not None


def test_the_json_report_summarises_the_batch():
    report = reporters.json_report([_finished_run(), _finished_run("failed")], "Nightly")
    assert report["summary"]["total"] == 2
    assert report["summary"]["passed"] == 1
    assert report["cases"][0]["steps"][0]["action"] == "click"


def test_reports_can_be_written_to_disk():
    with tempfile.TemporaryDirectory() as directory:
        junit = os.path.join(directory, "r.xml")
        js = os.path.join(directory, "r.json")
        reporters.write_reports([_finished_run()], "S", junit, js)
        ET.parse(junit)
        with open(js, encoding="utf-8") as handle:
            assert json.load(handle)["summary"]["total"] == 1


# --------------------------------------------------------------------------- #
# Suite runs and history
# --------------------------------------------------------------------------- #

def test_a_suite_run_aggregates_its_cases(suite_with_cases):
    suite_id, plain, _ = suite_with_cases
    suite_run_id = storage.create_suite_run(suite_id, workers=2)
    for status in ("passed", "failed"):
        run_id = storage.create_run("g", suite_run_id=suite_run_id, case_id=plain)
        storage.finish_run(run_id, status)
    storage.finish_suite_run(suite_run_id, "failed")

    result = storage.get_suite_run(suite_run_id)
    assert (result["passed"], result["failed"]) == (1, 1)
    assert result["suite_name"] == "Checkout"


def test_flakiness_ranks_a_flipping_case_above_a_broken_one(suite_with_cases):
    suite_id, steady, flaky = suite_with_cases
    for status in ["failed"] * 6:                       # consistently broken
        storage.finish_run(storage.create_run("g", case_id=steady), status)
    for status in ["passed", "failed"] * 3:             # flips every time
        storage.finish_run(storage.create_run("g", case_id=flaky), status)

    report = storage.flakiness_report()
    assert report[0]["case_id"] == flaky
    assert report[0]["flakiness"] > report[1]["flakiness"]


def test_a_case_with_one_run_is_not_called_flaky(suite_with_cases):
    _, plain, _ = suite_with_cases
    storage.finish_run(storage.create_run("g", case_id=plain), "failed")
    assert storage.flakiness_report() == []


def test_trend_groups_runs_by_day():
    for status in ("passed", "passed", "failed"):
        storage.finish_run(storage.create_run("g"), status)
    days = storage.trend(7)
    assert len(days) == 1
    assert (days[0]["passed"], days[0]["failed"]) == (2, 1)


# --------------------------------------------------------------------------- #
# Self-healing
# --------------------------------------------------------------------------- #

def test_identical_labels_score_one():
    assert healing._similarity("ara", "ara") == 1.0


def test_reordered_words_still_match():
    assert healing._similarity("ucus ara", "ara ucus") == 1.0


def test_unrelated_labels_score_zero():
    assert healing._similarity("giris yap", "cikis") == 0.0


def test_intent_describes_the_step_in_words_not_selectors():
    intent = healing.describe_intent({
        "action": "click", "target": "Ara",
        "element": {"label": "Ara", "role": "button", "id": "search"},
    })
    assert "click" in intent and "Ara" in intent and "button" in intent


def test_a_heal_reply_is_parsed_from_a_fenced_block():
    parsed = healing.parse_heal_reply('ok\n```json\n{"elementId":"e3","confidence":0.9}\n```')
    assert parsed["elementId"] == "e3"


def test_a_heal_reply_without_json_is_treated_as_no_match():
    assert healing.parse_heal_reply("I could not find it")["elementId"] is None


class _FakeElement:
    def __init__(self, element_id, text, role, resource_id=None):
        self.element_id, self.text, self.role = element_id, text, role
        self.resource_id, self.name = resource_id, ""
        self.selector = f"#{element_id}"

    def describe(self):
        return self.text


class _FakeSnapshot:
    def __init__(self, elements):
        self._elements = elements
        self.elements_by_id = {e.element_id: e for e in elements}

    def get_all_elements(self):
        return self._elements


def test_a_stable_id_wins_over_everything():
    snapshot = _FakeSnapshot([
        _FakeElement("e1", "Something else", "button", resource_id="search-btn"),
        _FakeElement("e2", "Ara", "button"),
    ])
    step = {"element": {"label": "Ara", "role": "button", "id": "search-btn"}}
    assert healing._semantic_match(snapshot, step).element_id == "e1"


def test_a_relabelled_button_is_rematched_without_a_model():
    snapshot = _FakeSnapshot([
        _FakeElement("e1", "Ucus ara", "button"),
        _FakeElement("e2", "Iptal", "button"),
    ])
    step = {"element": {"label": "Ara ucus", "role": "button"}}
    assert healing._semantic_match(snapshot, step).element_id == "e1"


def test_two_equally_good_matches_are_not_guessed_at():
    """An ambiguous repair must defer to the model rather than pick one."""
    snapshot = _FakeSnapshot([
        _FakeElement("e1", "Ara", "button"),
        _FakeElement("e2", "Ara", "button"),
    ])
    step = {"element": {"label": "Ara", "role": "button"}}
    assert healing._semantic_match(snapshot, step) is None


def test_a_wrong_role_lowers_the_score_enough_to_reject():
    snapshot = _FakeSnapshot([_FakeElement("e1", "Ara", "text")])
    step = {"element": {"label": "Tamamen baska bir sey", "role": "button"}}
    assert healing._semantic_match(snapshot, step) is None


# --------------------------------------------------------------------------- #
# Visual baselines
# --------------------------------------------------------------------------- #

@pytest.fixture(autouse=True)
def temp_baselines(monkeypatch, tmp_path):
    directory = tmp_path / "baselines"
    directory.mkdir()
    monkeypatch.setattr(visual, "BASELINE_DIR", str(directory))
    yield


def _png(color=(15, 47, 79), box=(20, 20, 200, 80), size=(300, 200)):
    image = Image.new("RGB", size, (245, 246, 249))
    ImageDraw.Draw(image).rectangle(box, fill=color)
    buffer = io.BytesIO()
    image.save(buffer, "PNG")
    return buffer.getvalue()


def test_the_first_screenshot_becomes_the_baseline_and_passes():
    result = visual.compare("home", _png())
    assert result["status"] == "created"
    assert result["passed"]


def test_an_identical_screenshot_passes():
    visual.compare("home", _png())
    assert visual.compare("home", _png())["passed"]


def test_a_moved_element_is_caught_and_a_diff_is_written():
    visual.compare("home", _png())
    result = visual.compare("home", _png(box=(20, 120, 200, 180)))
    assert not result["passed"]
    assert result["diffRatio"] > 0.01
    assert os.path.exists(result["diffPath"])


def test_a_changed_viewport_is_reported_as_a_size_mismatch():
    visual.compare("home", _png())
    result = visual.compare("home", _png(size=(400, 200)))
    assert result["status"] == "size-mismatch"
    assert not result["passed"]


def test_noise_below_the_tolerance_still_passes():
    """A blinking caret must not fail a whole visual check."""
    visual.compare("home", _png())
    assert visual.compare("home", _png(box=(20, 20, 201, 80)))["passed"]


def test_a_baseline_can_be_replaced_and_removed():
    visual.compare("home", _png())
    visual.save_baseline("home", _png(color=(200, 0, 0)))
    assert visual.compare("home", _png(color=(200, 0, 0)))["passed"]
    assert visual.delete_baseline("home")
    assert visual.list_baselines() == []


def test_a_baseline_name_cannot_escape_its_directory():
    path = visual.baseline_path("../../etc/passwd")
    assert os.path.dirname(path) == visual.BASELINE_DIR


def test_an_empty_baseline_name_is_rejected():
    with pytest.raises(ValueError):
        visual.baseline_path("   ")



# --------------------------------------------------------------------------- #
# Pass rate by priority
# --------------------------------------------------------------------------- #

def test_every_priority_band_is_reported_even_when_empty():
    """A missing band is an answer too: "no Critical ran" should be visible
    rather than looking like the band does not exist."""
    bands = [row["priority"] for row in storage.priority_breakdown(3650)]
    for band in storage.PRIORITY_ORDER:
        assert band in bands


def test_an_empty_band_has_no_pass_rate_rather_than_zero():
    # 0% and "nothing ran" are different findings; conflating them would report
    # a healthy band as a failing one.
    for row in storage.priority_breakdown(3650):
        if row["total"] == 0:
            assert row["pass_rate"] is None
            assert row["passed"] == 0 and row["failed"] == 0


def test_pass_rate_is_a_percentage_of_that_band_only():
    for row in storage.priority_breakdown(3650):
        if row["total"]:
            assert row["pass_rate"] == round(row["passed"] / row["total"] * 100)
            assert row["passed"] + row["failed"] <= row["total"]


def test_ungraded_runs_are_kept_separate_from_the_bands():
    """A chat run belongs to no Test Set, so bucketing it as Medium would
    misreport both it and Medium."""
    rows = storage.priority_breakdown(3650)
    graded = [r for r in rows if r["priority"] is not None]
    assert len(graded) == len(storage.PRIORITY_ORDER)
    for row in graded:
        assert row["priority"] in storage.PRIORITY_ORDER


# --------------------------------------------------------------------------- #
# Searching and paging the run history
# --------------------------------------------------------------------------- #

def test_an_empty_search_changes_nothing():
    """Blank input must not be read as "match the empty string"."""
    assert storage.list_runs(50, search=None) == storage.list_runs(50, search="")
    assert storage.list_runs(50, search="   ") == storage.list_runs(50)


def test_search_narrows_rather_than_reorders():
    everything = storage.list_runs(200)
    if not everything:
        return
    needle = (everything[0]["title"] or "")[:8]
    if not needle.strip():
        return
    found = storage.list_runs(200, search=needle)
    assert len(found) <= len(everything)
    ids = {run["id"] for run in everything}
    assert all(run["id"] in ids for run in found)


def test_search_is_case_insensitive():
    everything = storage.list_runs(200)
    if not everything:
        return
    needle = (everything[0]["title"] or "")[:8]
    if not needle.strip():
        return
    assert len(storage.list_runs(200, search=needle.lower())) \
        == len(storage.list_runs(200, search=needle.upper()))


def test_offset_walks_the_history_without_repeating_a_run():
    first = storage.list_runs(2, offset=0)
    second = storage.list_runs(2, offset=2)
    assert not ({r["id"] for r in first} & {r["id"] for r in second})


def test_paging_covers_the_same_runs_as_one_big_page():
    whole = storage.list_runs(100)
    paged = storage.list_runs(5, offset=0) + storage.list_runs(5, offset=5)
    assert [r["id"] for r in paged] == [r["id"] for r in whole[:len(paged)]]


# --------------------------------------------------------------------------- #
# Mobile executions
# --------------------------------------------------------------------------- #

def test_a_mobile_suite_needs_a_connected_device():
    """No device means no execution, said plainly — the alternative is opening a
    second Appium session that collides with the one already on the phone."""
    import asyncio

    suite = {"id": "m", "name": "Phone", "kind": "mobile"}
    cases = [{"id": "c", "idx": 1, "name": "n", "goal": "g", "enabled": True,
              "tags": [], "dataset": None, "url": None}]

    async def collect():
        events = []
        with patch.object(runner.storage, "get_suite", lambda i: {**suite, "cases": cases}), \
             patch.object(runner.storage, "select_cases", lambda i, t=None: cases), \
             patch.object(runner, "_connected_device", lambda: None):
            async for line in runner.run_suite("m"):
                events.append(json.loads(line))
        return events

    events = asyncio.run(collect())
    assert events[0]["event"] == "error"
    assert "bağlı cihaz yok" in events[0]["message"]


def test_a_mobile_suite_runs_one_case_at_a_time():
    """One phone, one session: fanning out would have two runs fighting over the
    same device."""
    import asyncio

    suite = {"id": "m", "name": "Phone", "kind": "mobile"}
    cases = [
        {"id": f"c{i}", "idx": i, "name": f"n{i}", "goal": "g", "enabled": True,
         "tags": [], "dataset": None, "url": None}
        for i in (1, 2, 3)
    ]
    concurrent, peak = 0, 0

    async def fake_case(execution, target, suite_run_id, options, emit):
        nonlocal concurrent, peak
        concurrent += 1
        peak = max(peak, concurrent)
        await asyncio.sleep(0)
        concurrent -= 1
        return {"caseId": execution["case"]["id"], "runId": None,
                "label": "l", "status": "passed", "error": None, "durationMs": 1}

    async def collect():
        with patch.object(runner.storage, "get_suite", lambda i: {**suite, "cases": cases}), \
             patch.object(runner.storage, "select_cases", lambda i, t=None: cases), \
             patch.object(runner.storage, "create_suite_run", lambda *a, **k: "sr"), \
             patch.object(runner.storage, "finish_suite_run", lambda *a, **k: None), \
             patch.object(runner, "_connected_device", lambda: _phone()), \
             patch.object(runner, "_run_mobile_case", fake_case):
            return [json.loads(line) async for line in runner.run_suite("m", workers=4)]

    events = asyncio.run(collect())
    assert peak == 1, f"cases overlapped on one device (peak {peak})"

    finished = next(e for e in events if e["event"] == "suite_finished")
    # The sequential task returns a list of results where the web path returns
    # one dict each; if that is not flattened the suite reports nothing at all.
    assert finished["total"] == 3
    assert finished["passed"] == 3


# --------------------------------------------------------------------------- #
# Executions are records, not children of a Test Set
# --------------------------------------------------------------------------- #

def test_deleting_a_test_set_keeps_its_executions():
    """An execution is a record of something that happened. Tidying up the
    working document it was drawn from must not destroy the history."""
    suite_id = storage.create_suite("Homepage")
    case_id = storage.add_case(suite_id, "n", "g", priority="Critical", layer="E2E")
    run_id = storage.create_run("g")
    execution_id = storage.create_suite_run(suite_id, workers=1)
    storage.link_run_to_suite(run_id, execution_id, case_id,
                              priority="Critical", layer="E2E", case_idx=1)
    storage.finish_run(run_id, "passed")
    storage.finish_suite_run(execution_id, "passed")

    storage.delete_suite(suite_id)

    survived = storage.get_suite_run(execution_id)
    assert survived is not None
    assert survived["passed"] == 1
    # The verdict still reads with its priority, because it was copied onto the
    # run rather than joined from a row that is now gone.
    assert survived["runs"][0]["priority"] == "Critical"
    assert survived["runs"][0]["layer"] == "E2E"
    assert survived["suite_name"] == "Homepage"
    assert survived["suite_exists"] is False


def test_a_test_set_can_have_many_executions():
    suite_id = storage.create_suite("Homepage")
    ids = [storage.create_suite_run(suite_id, workers=1) for _ in range(3)]
    listed = {row["id"] for row in storage.list_suite_runs(suite_id)}
    assert listed == set(ids)


def test_cases_can_be_picked_across_several_test_sets():
    """Picking a few scenarios out of one set and combining two sets are the
    same operation, so selection is by case id rather than by suite."""
    a = storage.create_suite("Booking")
    b = storage.create_suite("Check-in")
    first = storage.add_case(a, "a1", "g")
    storage.add_case(a, "a2", "g")
    second = storage.add_case(b, "b1", "g")

    picked = storage.cases_by_id([second, first])
    assert [c["id"] for c in picked] == [second, first], "the chosen order is kept"
    assert {c["suite_name"] for c in picked} == {"Booking", "Check-in"}


def test_an_execution_records_every_test_set_that_fed_it():
    a = storage.create_suite("Booking")
    b = storage.create_suite("Check-in")
    execution_id = storage.create_suite_run(
        None, workers=1, name="Regresyon",
        sources=[{"suite_id": a}, {"suite_id": b}],
    )
    detail = storage.get_suite_run(execution_id)
    assert detail["suite_name"] == "Regresyon"
    assert {s["suite_name"] for s in detail["sources"]} == {"Booking", "Check-in"}


def test_an_unknown_case_id_is_skipped_not_fatal():
    suite_id = storage.create_suite("Booking")
    real = storage.add_case(suite_id, "a1", "g")
    assert [c["id"] for c in storage.cases_by_id([real, "gone"])] == [real]


# --------------------------------------------------------------------------- #
# Platform on the execution
# --------------------------------------------------------------------------- #

def test_an_execution_records_the_platform_it_ran_on():
    """Read off the Test Set at the moment it starts. Joining back later cannot
    work: the set may be deleted, and a multi-set execution has no single set
    to ask."""
    suite_run_id = storage.create_suite_run(kind="mobile", name="Phone run")
    try:
        found = next(r for r in storage.list_suite_runs() if r["id"] == suite_run_id)
        assert found["kind"] == "mobile"
        assert storage.get_suite_run(suite_run_id)["kind"] == "mobile"
    finally:
        with storage._connect() as conn:
            conn.execute("DELETE FROM suite_runs WHERE id = ?", (suite_run_id,))


def test_an_execution_defaults_to_web_when_no_platform_is_given():
    # Older callers and hand-assembled runs must land somewhere rather than
    # falling outside both platform filters and disappearing from the list.
    suite_run_id = storage.create_suite_run(name="Unspecified")
    try:
        assert storage.get_suite_run(suite_run_id)["kind"] == "web"
    finally:
        with storage._connect() as conn:
            conn.execute("DELETE FROM suite_runs WHERE id = ?", (suite_run_id,))


# --- scenario steps -------------------------------------------------------
#
# Steps are what turns a scenario from "did it pass" into "which step failed",
# so they have to survive the round trip to the database intact and in order.


def _suite_with_steps(steps):
    suite_id = storage.create_suite("Adimli", kind="web")
    case_id = storage.add_case(suite_id, name="Senaryo", goal="g", steps=steps)
    return storage.get_case(case_id)


def test_steps_keep_their_order_and_expected_results():
    case = _suite_with_steps([
        {"action": "Ucus ara", "expected": "Sonuc listesi gelir"},
        {"action": "Ilk ucusu sec", "expected": "Yolcu formu acilir"},
    ])
    assert [s["action"] for s in case["steps"]] == ["Ucus ara", "Ilk ucusu sec"]
    assert case["steps"][1]["expected"] == "Yolcu formu acilir"


def test_a_step_with_no_action_is_dropped_not_stored():
    # An empty row in the editor is not a step the runner could carry out.
    case = _suite_with_steps([
        {"action": "Giris yap", "expected": "Ana sayfa"},
        {"action": "   ", "expected": "bos"},
        {"action": "", "expected": ""},
    ])
    assert len(case["steps"]) == 1


def test_a_step_may_omit_its_expected_result():
    case = _suite_with_steps([{"action": "Sadece git"}])
    assert case["steps"] == [{"action": "Sadece git", "expected": ""}]


def test_a_plain_string_is_accepted_as_a_step():
    case = _suite_with_steps(["Ucus ara"])
    assert case["steps"] == [{"action": "Ucus ara", "expected": ""}]


def test_junk_steps_leave_the_case_without_any():
    assert _suite_with_steps("not a list")["steps"] == []
    assert _suite_with_steps([None, 42])["steps"] == []


def test_editing_a_case_can_replace_its_steps():
    suite_id = storage.create_suite("Adimli", kind="web")
    case_id = storage.add_case(suite_id, name="S", goal="g",
                               steps=[{"action": "Eski", "expected": "x"}])
    storage.update_case(case_id, steps=[{"action": "Yeni", "expected": "y"}])
    assert storage.get_case(case_id)["steps"] == [{"action": "Yeni", "expected": "y"}]


def test_step_results_are_recorded_against_the_run_in_order():
    run_id = storage.create_run(goal="g", kind="web")
    first = storage.start_scenario_step(run_id, 1, "Ucus ara", "Sonuc listesi")
    storage.finish_scenario_step(first, "passed", "liste gorundu", actions_used=3)
    second = storage.start_scenario_step(run_id, 2, "Sec", "Form acilir")
    storage.finish_scenario_step(second, "failed", "form acilmadi", actions_used=12)

    steps = storage.list_scenario_steps(run_id)
    assert [(s["idx"], s["status"]) for s in steps] == [(1, "passed"), (2, "failed")]
    assert steps[0]["expected"] == "Sonuc listesi"
    assert steps[1]["message"] == "form acilmadi"
    assert steps[1]["actions_used"] == 12


def test_a_run_carries_its_scenario_steps_to_the_report():
    run_id = storage.create_run(goal="g", kind="web")
    storage.finish_scenario_step(
        storage.start_scenario_step(run_id, 1, "Adim", "Beklenen"), "passed", "oldu",
    )
    assert len(storage.get_run(run_id)["scenarioSteps"]) == 1


def test_a_run_without_steps_reports_an_empty_list_not_an_error():
    run_id = storage.create_run(goal="g", kind="web")
    assert storage.get_run(run_id)["scenarioSteps"] == []


# --- scenario steps in the exported reports -------------------------------


def _run_with_scenario_steps():
    run_id = storage.create_run(goal="Ucus ara", kind="web")
    storage.finish_scenario_step(
        storage.start_scenario_step(run_id, 1, "Kalkis gir", "Alan Istanbul olur"),
        "passed", "alan Istanbul",
    )
    storage.finish_scenario_step(
        storage.start_scenario_step(run_id, 2, "Ara butonuna bas", "Sonuc listesi gelir"),
        "failed", "liste gelmedi",
    )
    storage.finish_run(run_id, "failed", error="Step 2 failed: liste gelmedi")
    return storage.get_run(run_id)


def test_junit_names_the_scenario_step_that_failed():
    # Someone reading this in Jenkins should not have to open QAi to find out
    # where the scenario broke.
    xml = reporters.junit_xml([_run_with_scenario_steps()], "TK")
    assert "Scenario step 2 failed: Ara butonuna bas" in xml
    assert "expected: Sonuc listesi gelir" in xml
    assert "got: liste gelmedi" in xml


def test_junit_prints_the_scenario_step_by_step():
    xml = reporters.junit_xml([_run_with_scenario_steps()], "TK")
    assert "PASS 1. Kalkis gir" in xml
    assert "FAIL 2. Ara butonuna bas" in xml


def test_the_json_report_carries_the_scenario_steps():
    report = reporters.json_report([_run_with_scenario_steps()], "TK")
    steps = report["cases"][0]["scenarioSteps"]
    assert [(s["idx"], s["status"]) for s in steps] == [(1, "passed"), (2, "failed")]
    assert steps[1]["expected"] == "Sonuc listesi gelir"


def test_a_run_without_scenario_steps_reports_exactly_as_before():
    run_id = storage.create_run(goal="Serbest kosum", kind="web")
    storage.finish_run(run_id, "passed")
    run = storage.get_run(run_id)
    assert reporters.json_report([run], "TK")["cases"][0]["scenarioSteps"] == []
    assert "scenario:" not in reporters.junit_xml([run], "TK")


class _phone:
    """A device that answers the two questions the runner asks of one: what is
    on it, and which session it belongs to. The runner restarts the app
    between mobile cases, so a stand-in with neither used to take the whole
    sequential task down with it."""

    session_id = "device-session"

    def describe(self):
        return {"appId": "com.thy.reg", "platform": "iOS", "name": "iPhone"}
