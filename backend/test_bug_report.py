"""Turning a failed run into a defect someone else can act on.

The value of this is entirely in what it writes down, so the tests are about
the content: that the cause is read from the narrowest evidence available, that
the body carries what a reader needs to reproduce it, and — most of all — that
a failure of the run rather than of the product is labelled as one. A tracker
that mixes "the app is broken" with "the scenario ran out of actions" is a
tracker whose triage is worthless.
"""

import bug_report


def run(**over):
    base = {
        "id": "run1",
        "title": "Booking | RT [IST-ESB] - search and control the availability page",
        "status": "failed",
        "app_id": "https://nuat.turkishairlines.com/",
        "priority": "Critical",
        "suite_run_id": "sr1",
        "case_id": "c1",
        "error": "",
        "steps": [],
        "scenarioSteps": [],
        "pageEvents": [],
    }
    base.update(over)
    return base


def sstep(idx, status, action, expected=None, message=None):
    return {"idx": idx, "status": status, "action": action,
            "expected": expected, "message": message}


def astep(status, action, message, screenshot=None):
    return {"status": status, "action": action, "message": message, "screenshot": screenshot}


# --- what went wrong ------------------------------------------------------- #

def test_the_app_not_doing_what_the_step_said_is_the_default():
    r = run(scenarioSteps=[sstep(1, "failed", "Tap search", "results show", "hiçbir sonuç gelmedi")])
    assert bug_report.classify(r) == "EXPECTATION_NOT_MET"
    assert bug_report.compose(r)["isAppDefect"] is True


def test_a_step_that_ran_out_of_actions_is_not_an_app_defect():
    r = run(scenarioSteps=[
        sstep(1, "failed", "Fill the ports", "IST-VIE", "Step 1 used 12 actions without reaching its expected result."),
    ])
    assert bug_report.classify(r) == "STEP_BUDGET_EXHAUSTED"
    draft = bug_report.compose(r)
    assert draft["isAppDefect"] is False
    assert "describes the run rather than the product" in draft["detail"]


def test_a_step_closed_without_proof_is_not_an_app_defect():
    r = run(scenarioSteps=[
        sstep(1, "failed", "Dismiss the banner", "banner gone", "kapandı — closed as passed without verifying the expected result."),
    ])
    assert bug_report.classify(r) == "STEP_UNPROVEN"
    assert bug_report.compose(r)["isAppDefect"] is False


def test_the_failing_action_is_more_specific_than_the_step_around_it():
    """A step runs out of budget *because* something underneath it timed out.
    The report should name the cause, not the symptom."""
    r = run(
        steps=[astep("failed", "click", "Timeout 12000ms exceeded")],
        scenarioSteps=[sstep(1, "failed", "Tap Uçuş ara", "availability opens",
                             "Step 1 used 12 actions without reaching its expected result.")],
    )
    assert bug_report.classify(r) == "ACTION_TIMEOUT"


def test_each_agent_failure_shape_gets_its_own_code():
    for message, code in [
        ('"Uçuş ara" is no longer on the page.', "ELEMENT_GONE"),
        ('"X" is on the page but not visible', "ELEMENT_NOT_VISIBLE"),
        ('Expected "IST" on screen but it is not there. Visible text: a, b', "TEXT_NOT_FOUND"),
        ("Page.goto: net::ERR_CONNECTION_REFUSED", "NAVIGATION_FAILED"),
    ]:
        assert bug_report.classify(run(steps=[astep("failed", "click", message)])) == code


def test_a_run_with_nothing_to_go_on_says_so_rather_than_guessing():
    assert bug_report.classify(run()) == "UNCLASSIFIED"


def test_a_stopped_run_is_not_a_defect_at_all():
    r = run(error="Stopped by the user.")
    assert bug_report.classify(r) == "RUN_CANCELLED"
    assert bug_report.compose(r)["isAppDefect"] is False


def test_page_errors_are_recognised_when_nothing_else_failed():
    assert bug_report.classify(
        run(error="13 page error(s) while the run was otherwise green — first: HTTP 500")
    ) == "PAGE_ERRORS"


# --- what the report says -------------------------------------------------- #

def test_the_title_names_the_scenario_and_the_step():
    r = run(scenarioSteps=[
        sstep(1, "passed", "Open the app"),
        sstep(2, "failed", "Set the second segment to London", "LHR shows as origin", "olmadı"),
    ])
    title = bug_report.compose(r)["title"]
    assert "Booking" in title and "step 2" in title and "London" in title


def test_reproduction_stops_at_the_failure():
    """Everything after the failed step is what the agent did next, which is
    not part of reproducing it."""
    r = run(scenarioSteps=[
        sstep(1, "passed", "Open the app", "home shows"),
        sstep(2, "failed", "Tap search", "results show", "hiç sonuç yok"),
        sstep(3, "passed", "Something afterwards"),
    ])
    detail = bug_report.compose(r)["detail"]
    assert "Open the app" in detail
    assert "Tap search" in detail
    assert "Something afterwards" not in detail


def test_expected_and_actual_are_both_written_down():
    r = run(scenarioSteps=[sstep(1, "failed", "Tap search", "a result list appears", "boş liste döndü")])
    detail = bug_report.compose(r)["detail"]
    assert "a result list appears" in detail
    assert "boş liste döndü" in detail


def test_the_failing_action_is_shown_when_it_differs_from_the_step():
    r = run(
        steps=[astep("failed", "click", "Timeout 12000ms exceeded")],
        scenarioSteps=[sstep(1, "failed", "Tap search", "results", "used 12 actions without reaching its expected result")],
    )
    detail = bug_report.compose(r)["detail"]
    assert "Failing action" in detail and "Timeout" in detail


def test_third_party_page_errors_stay_out_of_the_report():
    r = run(
        scenarioSteps=[sstep(1, "failed", "Tap search", "results", "olmadı")],
        pageEvents=[
            {"level": "error", "thirdParty": True, "kind": "httperror", "text": "HTTP 500", "url": "https://tiktok.test/x"},
            {"level": "error", "thirdParty": False, "kind": "httperror", "text": "HTTP 500 on the booking API", "url": "https://nuat.test/api"},
            {"level": "warning", "thirdParty": False, "kind": "console", "text": "a notice"},
        ],
    )
    detail = bug_report.compose(r)["detail"]
    assert "booking API" in detail
    assert "tiktok.test" not in detail
    assert "a notice" not in detail


def test_severity_follows_the_scenario_rather_than_defaulting_to_medium():
    assert bug_report.compose(run(priority="Critical"))["severity"] == "Critical"
    assert bug_report.compose(run(priority=None))["severity"] == "Medium"


# --- the picture ----------------------------------------------------------- #

def test_the_frame_is_the_one_the_failure_happened_on():
    r = run(steps=[
        astep("passed", "click", "ok", screenshot="early"),
        astep("failed", "click", "gone", screenshot="atTheFailure"),
        astep("passed", "click", "later", screenshot="after"),
    ])
    assert bug_report.screenshot_for(r) == "atTheFailure"


def test_it_walks_back_when_the_failing_step_kept_no_frame():
    """Frames are not stored when they show what the last step already showed,
    so the nearest earlier one is the screen the failure happened on anyway."""
    r = run(steps=[
        astep("passed", "click", "ok", screenshot="theScreen"),
        astep("failed", "assert_text", "not found", screenshot=None),
    ])
    assert bug_report.screenshot_for(r) == "theScreen"


def test_no_frame_at_all_is_not_an_error():
    assert bug_report.screenshot_for(run(steps=[astep("failed", "click", "x")])) is None


def test_every_code_it_can_return_is_described():
    """A code with no description reads as an internal token in the UI."""
    r = run()
    for code in bug_report.CODES:
        assert bug_report.CODES[code], code
    assert bug_report.NOT_APP_DEFECTS <= set(bug_report.CODES)
    assert bug_report.classify(r) in bug_report.CODES


# --- raised by the run itself ---------------------------------------------- #

class RaisedAutomatically:
    """A scenario that fails during an execution files what it found, while it
    is still known — but only when what it found was the app's fault.

    The line matters more than the convenience. A tracker that mixes "the app
    is broken" with "the scenario ran out of actions" is one whose triage is
    worthless, and QAi is wrong often enough — an expected string no page
    renders, a limit a scenario invented — that an automatic bug is a lead
    rather than a finding.
    """


def test_only_a_defect_of_the_app_is_filed_without_being_read():
    for message, filed in [
        ("hiçbir sonuç gelmedi", True),
        ("Step 1 used 12 actions without reaching its expected result.", False),
        ("kapandı — closed as passed without verifying the expected result.", False),
    ]:
        draft = bug_report.compose(run(scenarioSteps=[
            sstep(1, "failed", "Tap search", "results show", message)]))
        assert draft["isAppDefect"] is filed, message


def test_a_stopped_run_files_nothing():
    """Someone pressed stop. There is no finding in that."""
    assert bug_report.compose(run(error="Stopped by the user."))["isAppDefect"] is False


def test_a_draft_carries_what_a_reader_needs_to_act():
    """Filed unread, so it has to stand on its own: what was expected, what
    happened instead, and the screen it happened on."""
    draft = bug_report.compose(run(scenarioSteps=[
        sstep(1, "failed", "Tap Uçuş ara", "a result list appears", "boş liste döndü")]))
    assert "a result list appears" in draft["detail"]
    assert "boş liste döndü" in draft["detail"]
    assert draft["title"]
    assert draft["code"] in bug_report.CODES


class TestOneBugPerScenarioNotPerRun:
    """A bug belongs to the scenario it was found in, not to the run that
    happened to catch it.

    Measured: three turns of the same measurement over the same broken
    scenario filed three identical rows, because the duplicate check matched
    on the run id and every turn is a new run. A tracker is worth reading in
    proportion to how few duplicates are in it.
    """

    @staticmethod
    def _db(tmp_path, monkeypatch):
        import storage
        monkeypatch.setattr(storage, "DB_PATH", str(tmp_path / "bugs.db"))
        monkeypatch.setattr(storage, "_schema_ready", False)
        storage.init_db()
        return storage

    def test_the_second_run_of_a_broken_scenario_finds_the_first_bug(
        self, tmp_path, monkeypatch,
    ):
        db = self._db(tmp_path, monkeypatch)
        first = db.create_run("g", case_id="case-1")
        db.finish_run(first, "failed")
        raised = db.create_bug(title="it broke", code="EXPECTATION_NOT_MET",
                               run_id=first, case_id="case-1")

        found = db.open_bug_for_case("case-1", "EXPECTATION_NOT_MET")
        assert found is not None and found["id"] == raised

    def test_a_different_failure_of_the_same_scenario_is_its_own_bug(
        self, tmp_path, monkeypatch,
    ):
        """Two things can be wrong with one scenario, and collapsing them
        loses the second."""
        db = self._db(tmp_path, monkeypatch)
        db.create_bug(title="it broke", code="EXPECTATION_NOT_MET", case_id="case-1")

        assert db.open_bug_for_case("case-1", "ELEMENT_NOT_FOUND") is None

    def test_a_scenario_that_breaks_again_after_a_fix_is_news(
        self, tmp_path, monkeypatch,
    ):
        """The fix did not hold, which is exactly the case worth a fresh row —
        so a settled bug must not go on suppressing new ones for ever."""
        db = self._db(tmp_path, monkeypatch)
        bug = db.create_bug(title="it broke", code="EXPECTATION_NOT_MET",
                            case_id="case-1")
        db.update_bug(bug, status="fixed")

        assert db.open_bug_for_case("case-1", "EXPECTATION_NOT_MET") is None

    def test_a_triaged_bug_still_suppresses_a_duplicate(self, tmp_path, monkeypatch):
        """Somebody has it; filing it again tells them nothing."""
        db = self._db(tmp_path, monkeypatch)
        bug = db.create_bug(title="it broke", code="EXPECTATION_NOT_MET",
                            case_id="case-1")
        db.update_bug(bug, status="triaged")

        found = db.open_bug_for_case("case-1", "EXPECTATION_NOT_MET")
        assert found is not None and found["id"] == bug

    def test_a_scenario_with_no_id_is_not_matched_against_every_other(
        self, tmp_path, monkeypatch,
    ):
        """An ad-hoc run from the chat belongs to no scenario. Treating that
        as "the same scenario" would suppress every bug after the first."""
        db = self._db(tmp_path, monkeypatch)
        db.create_bug(title="it broke", code="EXPECTATION_NOT_MET", case_id=None)

        assert db.open_bug_for_case(None, "EXPECTATION_NOT_MET") is None
