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


def astep(status, action, message, screenshot=None, scenario_idx=None):
    return {"status": status, "action": action, "message": message, "screenshot": screenshot,
            "scenario_idx": scenario_idx}


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


def test_a_timeout_under_a_verdict_is_how_the_model_found_out():
    """TC-32 on the hotel set: the + at the limit was greyed out, so pressing
    it timed out, and the model judged the step — the limit held, but the
    warning the analysis asks for never appeared. Read from the timeout, the
    finding was filed as "an action timed out", not the app's, and dropped."""
    verdict = ("Oda 2 çocuk sayısı 0'da kaldı ve artırma butonu devre dışıydı, ancak "
               "beklenen 'max. kişi sayısına ulaştınız.' uyarı metni ekranda gösterilmedi")
    r = run(
        steps=[astep("passed", "click", "ok", scenario_idx=4),
               astep("failed", "click", "Timeout 12000ms exceeded", scenario_idx=5)],
        scenarioSteps=[sstep(4, "passed", "Fill room 1"),
                       sstep(5, "failed", "Add a child to room 2", "the warning shows", verdict)],
    )
    assert bug_report.classify(r) == "EXPECTATION_NOT_MET"
    draft = bug_report.compose(r)
    assert draft["isAppDefect"] is True
    assert draft["title"] == ("Beklenen 'max. kişi sayısına ulaştınız.' uyarı metni "
                              "ekranda gösterilmedi")


def test_a_retry_that_failed_in_an_earlier_step_is_no_evidence_about_a_later_one():
    r = run(
        steps=[astep("failed", "click", '"Oda Ekle" is no longer on the page', scenario_idx=1),
               astep("passed", "click", "ok", scenario_idx=1)],
        scenarioSteps=[sstep(1, "passed", "Add a room"),
                       sstep(2, "failed", "Check the total", "2 rooms", "toplam 1 oda gösteriyor")],
    )
    assert bug_report.classify(r) == "EXPECTATION_NOT_MET"
    assert "Failing action" not in bug_report.compose(r)["detail"]


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


def test_a_session_the_site_refused_is_not_a_defect_whatever_step_it_was_on():
    """The first purchase run on the web was refused at its fourth step and
    filed as "the step's expected result did not hold" — a defect, on a page
    it never reached. Nothing about the product was tested."""
    r = run(
        blocked=1,
        error="The site's bot protection refused this session. First refused: "
              "https://nuat.turkishairlines.com/api/v1/availability/price-calendar.",
        scenarioSteps=[sstep(4, "failed", "Tarih alanını aç", "gün yazar",
                             "The run ended before this step closed.")],
    )
    assert bug_report.classify(r) == "SITE_BLOCKED"
    assert bug_report.compose(r)["isAppDefect"] is False


def test_page_errors_are_recognised_when_nothing_else_failed():
    assert bug_report.classify(
        run(error="13 page error(s) while the run was otherwise green — first: HTTP 500")
    ) == "PAGE_ERRORS"


class TestAModelThatDidNotAnswerIsNotTheApp:
    """Fourteen hotel search bugs said only "Anthropic rate limit reached",
    and every one was filed against the app as an expectation that did not
    hold. The step was closed without anybody looking at the screen."""

    def test_a_rate_limited_step_is_the_model_not_the_app(self):
        r = run(error="Anthropic rate limit reached. Wait and retry.", scenarioSteps=[
            sstep(1, "passed", "Open the page"),
            sstep(2, "failed", "Add a room", "3 rooms",
                  "Anthropic rate limit reached. Wait and retry."),
        ])
        assert bug_report.classify(r) == "MODEL_UNAVAILABLE"
        draft = bug_report.compose(r)
        assert draft["isAppDefect"] is False
        assert "did not answer" in draft["detail"]

    def test_a_spent_gateway_budget_is_the_model_not_the_app(self):
        said = ("The model gateway's spending budget is used up (spent 201.04 of "
                "201.00). Waiting will not lift it: raise the budget or wait for it "
                "to reset, then run again.")
        r = run(error=said, scenarioSteps=[sstep(3, "failed", "Pick a date", "shown", said)])
        assert bug_report.classify(r) == "MODEL_UNAVAILABLE"

    def test_a_finding_before_the_outage_still_decides(self):
        """A failed check no longer ends the run, so a real finding can sit in
        an earlier step than the outage that stopped it. The outage must not
        hide it — and the bug is named after the finding."""
        r = run(scenarioSteps=[
            sstep(1, "failed", "Type ant", "Antalya is suggested",
                  "'ant' için Antalya önerisi listede yer almıyor"),
            sstep(2, "failed", "Pick it", "Antalya shows",
                  "Anthropic rate limit reached. Wait and retry."),
        ])
        assert bug_report.classify(r) == "EXPECTATION_NOT_MET"
        draft = bug_report.compose(r)
        assert draft["title"] == "'ant' için Antalya önerisi listede yer almıyor"
        assert "Antalya is suggested" in draft["detail"]

    def test_a_step_left_open_when_the_run_ended_proves_nothing(self):
        r = run(scenarioSteps=[sstep(4, "failed", "Open the calendar", "it opens",
                                     "The run ended before this step closed.")])
        assert bug_report.classify(r) == "STEP_UNPROVEN"
        assert bug_report.compose(r)["isAppDefect"] is False


# --- what the report says -------------------------------------------------- #

class TestTheTitleSaysWhatWentWrong:
    """Titles were "<scenario> — step N: <action>": 161 characters at the
    median, and what was broken was not in them at all. A bug is now named
    after the defect, and the scenario is kept beside it."""

    def test_the_title_is_the_failure_and_the_scenario_travels_beside_it(self):
        r = run(scenarioSteps=[
            sstep(1, "passed", "Open the app"),
            sstep(2, "failed", "Set the second segment to London", "LHR shows as origin",
                  "Yenileme sonrası son geçerli form korunmadı: Nereye alanı (el_40) "
                  "İstanbul IST yerine 'Seçiniz' gösteriyor; tarihler de boş"),
        ])
        draft = bug_report.compose(r)
        assert draft["title"] == "Yenileme sonrası son geçerli form korunmadı"
        assert draft["caseName"] == r["title"]
        assert "Booking" not in draft["title"]

    def test_a_step_that_ran_out_is_named_after_the_action_under_it(self):
        """"Step 1 used 12 actions" says the step ended, not what was wrong."""
        r = run(
            steps=[astep("failed", "assert_text",
                         'Expected "1 Misafir, 1 Oda" on screen but it is not there. '
                         'Visible text: Otel, Nereye')],
            scenarioSteps=[sstep(1, "failed", "Open the panel", "1 Misafir, 1 Oda",
                                 "Step 1 used 12 actions without reaching its expected result.")],
        )
        assert bug_report.compose(r)["title"] == '"1 Misafir, 1 Oda" is not on the screen'

    def test_with_nothing_said_the_title_is_what_the_cause_means(self):
        assert bug_report.compose(run())["title"] == bug_report.CODES["UNCLASSIFIED"]

    def test_each_shape_of_failure_is_cut_to_its_finding(self):
        for said, title in [
            # The first clause is the finding; the rest is the evidence.
            ("Yenileme sonrası son geçerli form korunmadı: Nereye alanı (el_40) İstanbul "
             "IST yerine 'Seçiniz' gösteriyor",
             "Yenileme sonrası son geçerli form korunmadı"),
            # What worked comes first; the finding is after the "ancak".
            ("IST yazıldığında öneriler geldi ancak ülke adları okunur metin yerine ham "
             "çeviri anahtarı olarak görünüyor: 'İstanbul countrylookup.TR (26)'",
             "Ülke adları okunur metin yerine ham çeviri anahtarı olarak görünüyor"),
            # The finding is the second clause.
            ("'ant' yazıldıktan sonra öneriler ANTWERP (BRU) ve ANTWERPEN (BRU, RTM) "
             "olarak listelendi; beklenen Antalya (AYT) önerisi listede yer almıyor.",
             "Beklenen Antalya (AYT) önerisi listede yer almıyor"),
            # What should have happened is not what did.
            ("Beklenen TO alanının boş (Select) olması ve Search Flight butonunun pasif "
             "olmasıydı; ancak TO alanı ESB - Ankara Esenboga Airport ile dolu, FROM "
             "alanı ise Select olarak boş.",
             "TO alanı ESB - Ankara Esenboga Airport ile dolu, FROM alanı ise Select olarak boş"),
            # The tool's pointers into the page mean nothing to a reader.
            ("Sonraki ay okunun aria-label değeri Türkçe 'Sonraki ay' yerine 'Next month' "
             "olarak geliyor (el_140 üzerinde doğrulandı)",
             "Sonraki ay okunun aria-label değeri Türkçe 'Sonraki ay' yerine 'Next month' "
             "olarak geliyor"),
            # An apostrophe before a suffix is not a closing quote.
            ("EcoFly paketi seçilip 'EcoFly'da kal' onaylandı ve uçuş 'ECONOMY EcoFly B' "
             "olarak işaretlendi, ancak alt bardaki 'Devam et' butonu gri/pasif durumda "
             "kaldı ve tıklanabilir bir öğe olarak sunulmadı; tekrarlanan bekleme",
             "Alt bardaki 'Devam et' butonu gri/pasif durumda kaldı ve tıklanabilir bir "
             "öğe olarak sunulmadı"),
            # A long quotation is cut inside its quotes, not the sentence around it.
            ("Uçuş ara tıklandı ancak uygunluk sayfası yerine 'Seçmiş olduğunuz seyahat "
             "tarihleri için bu parkurda çoklu uçuş seçeneği mevcut değildir' uyarısı "
             "çıktı; üç segment listelenmedi",
             "Uygunluk sayfası yerine 'Seçmiş olduğunuz seyahat tarihleri…' uyarısı çıktı"),
            # The tool's own sentences, said shorter.
            ('Expected "portcitylookup.IST IST" to contain "İstanbul" but it holds '
             '"portcitylookup.IST IST", and "İstanbul" is nowhere on the screen.',
             'Expected "İstanbul", found "portcitylookup.IST IST"'),
            ('Expected "button" to contain "01" but it holds "".',
             'Expected "01", found nothing'),
            ("1 page error(s). First: pageerror: Loading chunk 387 failed.",
             "Page error: Loading chunk 387 failed"),
            ("Element el_308 never became actionable within 10s",
             "Element never became actionable within 10s"),
            ("Anthropic rate limit reached. Wait and retry.", "Anthropic rate limit reached"),
            # Turkish capitals: a title starting with i starts with İ.
            ("istanbul araması boş döndü", "İstanbul araması boş döndü"),
        ]:
            assert bug_report.headline(said) == title, said

    def test_a_line_that_only_says_the_step_ended_is_no_title(self):
        for said in ("Step 3 used 24 actions without reaching its expected result.",
                     "The run ended before this step closed.", "—", "", None):
            assert bug_report.headline(said) is None, said

    def test_a_title_fits_a_list_row_however_long_the_verdict(self):
        said = "Arama sonrası sonuçlar gelmedi " + "ve sayfa uzun uzun açıklama yaptı " * 12
        line = bug_report.headline(said)
        assert len(line) <= bug_report.TITLE_LIMIT
        assert line.endswith("…")


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
    """Someone else's outage is not this team's bug.

    This used to keep first-party warnings out as well, on the grounds that
    they do not decide a verdict. They do not — but they are the evidence for
    the bugs that are *about* them: every 4xx is a warning here, so a report
    on a 404 carried no trace of the 404 and the reader had to go back to the
    run to find out what had happened. They are in now, in their own section,
    marked as not deciding anything.
    """
    r = run(
        scenarioSteps=[sstep(1, "failed", "Tap search", "results", "olmadı")],
        pageEvents=[
            {"level": "error", "thirdParty": True, "kind": "httperror", "text": "HTTP 500", "url": "https://tiktok.test/x"},
            {"level": "error", "thirdParty": False, "kind": "httperror", "text": "HTTP 500 on the booking API", "url": "https://nuat.test/api"},
            {"level": "warning", "thirdParty": False, "kind": "console", "text": "a notice"},
            {"level": "warning", "thirdParty": True, "kind": "console", "text": "meta pixel duplicate"},
        ],
    )
    detail = bug_report.compose(r)["detail"]
    assert "booking API" in detail
    assert "tiktok.test" not in detail
    assert "meta pixel" not in detail
    # The first-party warning is evidence, and is kept where it cannot be
    # mistaken for the cause.
    assert "a notice" in detail
    assert "Sayfa uyarıları" in detail


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


class TestAPassingRunIsNotADefect:
    """A run that passed has nothing to file.

    Measured: a scenario whose four steps all passed, with no error recorded,
    had a bug raised against the app reading "The run failed without a
    recognised cause". The chain was: classify() found no failure to describe
    and returned UNCLASSIFIED; UNCLASSIFIED was not in NOT_APP_DEFECTS, so the
    draft came back isAppDefect; and the raiser ran after every case whatever
    it did. Three places, each reasonable alone.
    """

    def test_not_knowing_why_is_not_evidence_of_anything(self):
        """UNCLASSIFIED means the tool could not work it out. Nobody should
        get a defect assigned to them for that."""
        assert "UNCLASSIFIED" in bug_report.NOT_APP_DEFECTS
        draft = bug_report.compose(run(status="failed", error=""))
        assert draft["code"] == "UNCLASSIFIED"
        assert draft["isAppDefect"] is False

    def test_a_green_run_is_never_filed_automatically(self, tmp_path, monkeypatch):
        import storage

        monkeypatch.setattr(storage, "DB_PATH", str(tmp_path / "bugs.db"))
        monkeypatch.setattr(storage, "_schema_ready", False)
        storage.init_db()
        run_id = storage.create_run("open the booker", case_id="case-1")
        storage.add_step(run_id, "click", target="Yolcu", status="passed",
                         message='Clicked "Yolcu"')
        storage.finish_run(run_id, "passed")

        draft = bug_report.draft_for_run(run_id)
        assert draft is not None
        assert draft["passed"] is True
        assert draft["isAppDefect"] is False

    def test_a_real_failure_is_still_filed(self, tmp_path, monkeypatch):
        """The guard must not turn the feature off."""
        import storage

        monkeypatch.setattr(storage, "DB_PATH", str(tmp_path / "bugs2.db"))
        monkeypatch.setattr(storage, "_schema_ready", False)
        storage.init_db()
        run_id = storage.create_run("search a flight", case_id="case-2")
        storage.add_step(run_id, "assert_text", target=None, status="failed",
                         message='Expected "ECONOMY" on screen but it is not there')
        storage.finish_run(run_id, "failed", error="1 of 2 steps failed")

        draft = bug_report.draft_for_run(run_id)
        assert draft["isAppDefect"] is True
        assert not draft.get("passed")


class TestTheEvidenceIsInTheReport:
    """A bug about a 404 has to carry the 404.

    Every 4xx is a warning — a live airline site answers them all through a
    working booking, so they must not fail a run — but the report only listed
    *errors*, and on those bugs there were none. It showed a cause and no
    evidence for it, and the reader had to go back to the run to find out what
    had actually happened.
    """

    def _run(self, events):
        return run(status="failed", error="it broke", pageEvents=events)

    def test_a_failed_request_carries_its_status_and_its_url(self):
        detail = bug_report.compose(self._run([{
            "kind": "httperror", "level": "warning", "text": "HTTP 404 ",
            "url": "https://nuat.turkishairlines.com/rb_b2184cfc?type=js3",
            "status": 404, "resourceType": "fetch", "thirdParty": False,
        }])) ["detail"]
        assert "HTTP 404" in detail
        assert "(fetch)" in detail
        assert "https://nuat.turkishairlines.com/rb_b2184cfc?type=js3" in detail

    def test_a_console_error_is_labelled_console_and_quoted_whole(self):
        message = ("Access to CSS stylesheet at 'https://accounts.google.com/gsi/style' "
                   "from origin 'https://nuat.turkishairlines.com' has been blocked by CORS")
        detail = bug_report.compose(self._run([{
            "kind": "console", "level": "warning", "text": message,
            "url": "https://nuat.turkishairlines.com/tr-tr", "thirdParty": False,
        }]))["detail"]
        assert "console:" in detail
        assert "blocked by CORS" in detail, "the message must not be cut before the cause"

    def test_errors_and_warnings_are_told_apart(self):
        detail = bug_report.compose(self._run([
            {"kind": "pageerror", "level": "error", "text": "TypeError: x is not a function",
             "thirdParty": False},
            {"kind": "httperror", "level": "warning", "text": "HTTP 404 ", "status": 404,
             "thirdParty": False},
        ]))["detail"]
        assert "Sayfa hataları" in detail
        assert "Sayfa uyarıları" in detail
        assert "koşumun sonucunu belirlemez" in detail

    def test_someone_elses_server_is_left_out(self):
        """Their outage is not this team's bug, and a report full of Google's
        console noise is a report nobody reads twice."""
        detail = bug_report.compose(self._run([{
            "kind": "console", "level": "warning", "text": "GSI_LOGGER FedCM rejected",
            "url": "https://accounts.google.com/gsi", "thirdParty": True,
        }]))["detail"]
        assert "GSI_LOGGER" not in detail

    def test_a_clean_run_adds_no_section_at_all(self):
        assert "Sayfa" not in bug_report.compose(self._run([]))["detail"]


# --- why a run failed, for Test Runs ---------------------------------------- #

class TestARunSaysWhyItFailed:
    """Test Runs says on each row why the run did not pass, read the same way
    a bug is, so the row and the bug raised from it never disagree."""

    def test_a_run_that_passed_or_is_still_going_has_nothing_to_say(self):
        assert bug_report.run_failure(run(status="passed")) is None
        assert bug_report.run_failure(run(status="running")) is None

    def test_the_failure_is_the_step_the_model_judged(self):
        r = run(
            steps=[astep("failed", "click", "Timeout 12000ms exceeded", scenario_idx=2)],
            scenarioSteps=[sstep(1, "passed", "Open the page"),
                           sstep(2, "failed", "Add a room", "3 rooms",
                                 "Oda eklenmedi; sayaç 2'de kaldı")],
        )
        failure = bug_report.run_failure(r)
        assert failure["headline"] == "Oda eklenmedi"
        assert (failure["step"], failure["expected"]) == (2, "3 rooms")
        assert failure["actual"].startswith("Oda eklenmedi")
        assert failure["failingAction"] == {"action": "click",
                                            "message": "Timeout 12000ms exceeded"}
        assert failure["isAppDefect"] is True

    def test_a_model_that_did_not_answer_is_said_to_be_the_run(self):
        r = run(scenarioSteps=[sstep(3, "failed", "Pick a date", "shown",
                                     "Anthropic rate limit reached. Wait and retry.")])
        failure = bug_report.run_failure(r)
        assert failure["headline"] == "Anthropic rate limit reached"
        assert failure["code"] == "MODEL_UNAVAILABLE"
        assert failure["isAppDefect"] is False

    def test_a_stopped_run_says_it_was_stopped_not_what_stop_cut_off(self):
        r = run(status="cancelled", error="Stopped by the user.",
                steps=[astep("failed", "click", "Timeout 12000ms exceeded", scenario_idx=3)],
                scenarioSteps=[sstep(3, "cancelled", "Pick a date", "shown",
                                     "Run stopped before this step closed.")])
        failure = bug_report.run_failure(r)
        assert failure["code"] == "RUN_CANCELLED"
        assert failure["headline"] == bug_report.CODES["RUN_CANCELLED"]
        assert failure["step"] is None

    def test_a_run_stopped_after_a_finding_keeps_the_finding(self):
        r = run(status="cancelled", scenarioSteps=[
            sstep(1, "failed", "Type ant", "Antalya", "Antalya önerilmedi"),
            sstep(2, "cancelled", "Pick it", "shown", "Run stopped before this step closed."),
        ])
        failure = bug_report.run_failure(r)
        assert (failure["headline"], failure["step"]) == ("Antalya önerilmedi", 1)

    def test_the_list_carries_the_steps_the_execution_and_the_evidence(
        self, tmp_path, monkeypatch,
    ):
        import storage
        monkeypatch.setattr(storage, "DB_PATH", str(tmp_path / "runs.db"))
        monkeypatch.setattr(storage, "_schema_ready", False)
        storage.init_db()
        suite_id = storage.create_suite("Otel arama alanı")
        case_id = storage.add_case(suite_id, "Hotel - Guests | [Jolly: IST] room maximum 2", "g")
        execution = storage.create_suite_run(suite_id, name="Otel arama alanı")

        failed = storage.create_run("g", suite_run_id=execution, case_id=case_id)
        opened = storage.start_scenario_step(failed, 1, "Open", "the form")
        storage.finish_scenario_step(opened, "passed")
        opened = storage.start_scenario_step(failed, 2, "Add a room", "3 rooms")
        storage.add_step(failed, "click", "passed", target="Oda Ekle", scenario_idx=2)
        storage.add_step(failed, "click", "failed", target="Oda Ekle",
                         message="Timeout 12000ms exceeded", scenario_idx=2)
        storage.finish_scenario_step(opened, "failed", message="Oda eklenmedi")
        storage.finish_run(failed, "failed", error="1 of 2 steps failed")
        passed = storage.create_run("g", suite_run_id=execution, case_id=case_id)
        storage.finish_run(passed, "passed")

        rows = {row["id"]: row for row in storage.list_runs()}
        row = rows[failed]
        assert (row["scenario_passed"], row["scenario_total"]) == (1, 2)
        assert row["suite_run_name"] == "Otel arama alanı"
        assert row["suite_run_size"] == 2
        assert rows[passed]["scenario_total"] == 0

        evidence = storage.failure_evidence([failed, passed])
        assert [s["idx"] for s in evidence[failed]["scenarioSteps"]] == [2]
        assert [(s["action"], s["scenario_idx"]) for s in evidence[failed]["steps"]] == [("click", 2)]
        assert evidence[passed] == {"scenarioSteps": [], "steps": []}

        failure = bug_report.run_failure({**row, **evidence[failed]})
        assert (failure["headline"], failure["step"]) == ("Oda eklenmedi", 2)


# --- the body read back into its parts ------------------------------------- #

class TestTheBodyReadsBackIntoItsParts:
    """The page shows a bug as labelled parts, each label in its own colour
    beside what it says. The body stays text — it is what a tracker takes and
    what a tester edits — so the parts are read back out of it."""

    def _body(self):
        r = run(
            steps=[astep("failed", "assert_text", 'Expected "IST" on screen but it is not there')],
            scenarioSteps=[
                sstep(1, "passed", "Open the page", "The form shows"),
                sstep(2, "failed", "Type IST", "İstanbul is suggested",
                      "Step 2 used 12 actions without reaching its expected result."),
            ],
            pageEvents=[
                {"kind": "httperror", "level": "warning", "text": "HTTP 404 ", "status": 404,
                 "resourceType": "document", "url": "https://nuat.test/hotel",
                 "thirdParty": False},
                {"kind": "pageerror", "level": "error", "text": "TypeError: x is undefined",
                 "thirdParty": False},
            ],
        )
        return bug_report.AUTO_RAISED + "\n\n" + bug_report.compose(r)["detail"]

    def test_every_labelled_line_comes_back_under_its_own_name(self):
        parts = bug_report.parse_detail(self._body())
        fields = parts["fields"]
        assert parts["autoRaised"] is True
        assert fields["scenario"].startswith("Booking | RT")
        assert fields["where"] == "https://nuat.turkishairlines.com/"
        assert fields["cause"].startswith("TEXT_NOT_FOUND")
        assert fields["expected"] == "İstanbul is suggested"
        assert "used 12 actions" in fields["actual"]
        assert fields["failing"].startswith("assert_text — ")
        assert parts["runId"] == "run1"
        assert parts["rest"] is None

    def test_the_steps_keep_their_order_their_verdict_and_what_they_expected(self):
        steps = bug_report.parse_detail(self._body())["steps"]
        assert [(s["idx"], s["failed"]) for s in steps] == [(1, False), (2, True)]
        assert steps[0]["expected"] == "The form shows"
        assert steps[1]["action"] == "Type IST"

    def test_the_pages_complaints_come_back_by_level_with_their_addresses(self):
        events = {e["level"]: e for e in bug_report.parse_detail(self._body())["events"]}
        assert events["error"]["count"] == 1
        assert "TypeError" in events["error"]["items"][0]["text"]
        warning = events["warning"]["items"][0]
        assert warning["kind"] == "network"
        assert warning["url"] == "https://nuat.test/hotel"

    def test_the_run_not_the_product_note_is_kept_to_say_first(self):
        r = run(scenarioSteps=[sstep(1, "failed", "Type IST", "suggested",
                                     "Step 1 used 12 actions without reaching its expected result.")])
        notes = bug_report.parse_detail(bug_report.compose(r)["detail"])["notes"]
        assert notes and "describes the run rather than the product" in notes[0]
        # An app defect carries no such note.
        assert bug_report.parse_detail(self._body())["notes"] == []

    def test_a_body_typed_by_hand_is_kept_whole(self):
        typed = "Menü açılınca Corporate Club 404 dönüyor.\n\nAdımlar:\n1. Menüyü aç"
        parts = bug_report.parse_detail(typed)
        assert parts["fields"] == {}
        assert parts["rest"] == typed

    def test_a_value_that_runs_onto_the_next_line_stays_with_its_label(self):
        parts = bug_report.parse_detail("**Actual**  ilk satır\nikinci satır\n\nsonra")
        assert parts["fields"]["actual"] == "ilk satır\nikinci satır"
        assert parts["rest"] == "sonra"


class TestBugsFiledBeforeAreRenamed:
    """The bugs already in the list were named after their scenario. They are
    renamed once, from the body they carry; a title someone typed is theirs."""

    @staticmethod
    def _db(tmp_path, monkeypatch):
        import storage
        monkeypatch.setattr(storage, "DB_PATH", str(tmp_path / "renamed.db"))
        monkeypatch.setattr(storage, "_schema_ready", False)
        storage.init_db()
        return storage

    @staticmethod
    def _old_title(r):
        failed = bug_report._failed_scenario_step(r)
        return (f"{bug_report._summarise(r['title'], 90)} — step {failed['idx']}: "
                f"{bug_report._summarise(failed['action'], 70)}")

    def test_an_old_title_is_renamed_after_the_failure_once(self, tmp_path, monkeypatch):
        db = self._db(tmp_path, monkeypatch)
        r = run(scenarioSteps=[sstep(1, "failed", "Refresh the page", "the form is kept",
                                     "Yenileme sonrası son geçerli form korunmadı: alanlar boş")])
        bug_id = db.create_bug(title=self._old_title(r), code="EXPECTATION_NOT_MET",
                               detail=bug_report.compose(r)["detail"])

        assert bug_report.refresh_titles() == 1
        assert db.get_bug(bug_id)["title"] == "Yenileme sonrası son geçerli form korunmadı"
        assert bug_report.refresh_titles() == 0

    def test_a_title_somebody_typed_is_left_alone(self, tmp_path, monkeypatch):
        db = self._db(tmp_path, monkeypatch)
        r = run(scenarioSteps=[sstep(1, "failed", "Refresh", "kept", "korunmadı")])
        bug_id = db.create_bug(title="Refresh drops the search form", code="EXPECTATION_NOT_MET",
                               detail=bug_report.compose(r)["detail"])

        assert bug_report.refresh_titles() == 0
        assert db.get_bug(bug_id)["title"] == "Refresh drops the search form"


class TestABugKnowsItsTestSet:
    """Bug Report groups bugs under their Test Set. 47 of 107 web bugs could
    name their scenario but not its set: a case read out of its own set does
    not carry the set's name, and the runner copied it off the case."""

    def test_an_older_bug_is_given_its_set_and_execution_from_its_run(
        self, tmp_path, monkeypatch,
    ):
        import storage
        monkeypatch.setattr(storage, "DB_PATH", str(tmp_path / "sources.db"))
        monkeypatch.setattr(storage, "_schema_ready", False)
        storage.init_db()
        suite_id = storage.create_suite("Otel arama alanı")
        case_id = storage.add_case(suite_id, "Hotel - Search | refresh", "refresh keeps it")
        suite_run_id = storage.create_suite_run(suite_id, name="Otel arama alanı")
        run_id = storage.create_run("refresh keeps it", suite_run_id=suite_run_id, case_id=case_id)
        bug_id = storage.create_bug(title="it broke", run_id=run_id, case_id=case_id)
        orphan = storage.create_bug(title="typed by hand")

        with storage._connect() as conn:
            storage._backfill_bug_sources(conn)

        bug = storage.get_bug(bug_id)
        assert bug["suite_name"] == "Otel arama alanı"
        assert bug["suite_run_id"] == suite_run_id
        assert storage.get_bug(orphan)["suite_name"] is None

    def test_the_list_says_which_scenario_number_it_was(self, tmp_path, monkeypatch):
        import storage
        monkeypatch.setattr(storage, "DB_PATH", str(tmp_path / "idx.db"))
        monkeypatch.setattr(storage, "_schema_ready", False)
        storage.init_db()
        suite_id = storage.create_suite("Otel arama alanı")
        storage.add_case(suite_id, "first", "one")
        case_id = storage.add_case(suite_id, "second", "two")
        storage.create_bug(title="it broke", case_id=case_id)

        [bug] = storage.list_bugs()
        assert bug["case_idx"] == 2
