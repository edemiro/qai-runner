"""Keeping what a green run did, so the next run does not pay for it again.

Every execution used to re-derive the same clicks from the same screens: a
screenshot and a model call per action, paid again on every run of a scenario
that had not changed. A run already knows which element each action reached and
how it addressed it, so the second run of an unchanged scenario has no reason
to ask.

What makes this safe rather than merely fast is what it refuses to keep. A step
is what is judged, because a step is what is replayed: one is kept when the
step's own verdict held and every action under it passed, and what a later step
did has nothing to do with that. A step closed as failed is never kept however
clean its actions look — the agent got there and the expected result did not
hold, so replaying the way there reproduces a failure quickly.

The other half is what a bad turn costs. A step that was not proved keeps
whatever it already had, rather than losing it to one stumble; only a recording
that was actually replayed and came back red has disproved itself, and that one
goes. Deleting on every stumble read as caution and measured as the opposite —
recordings never accumulated, they were earned and lost again every turn.

Most of this file is still about the recordings that are thrown away.
"""

import pytest

import storage


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "DB_PATH", str(tmp_path / "recording.db"))
    monkeypatch.setattr(storage, "_schema_ready", False)
    storage.init_db()
    return storage


@pytest.fixture
def case(db):
    suite_id = db.create_suite("a set")
    return db.add_case(suite_id, "a scenario", "do the thing", steps=[
        {"action": "Open the booker", "expected": "The booker is shown"},
        {"action": "Search IST to ESB", "expected": "Results are listed"},
    ])


def a_run(case_id, status="passed"):
    run_id = storage.create_run(goal="a run", kind="web", case_id=case_id)
    storage.finish_run(run_id, status)
    return run_id


def did(run_id, scenario_idx, action, status="passed", element=None, value=None):
    """One agent action, recorded the way the agent records it — the label goes
    in `target`, the selector comes off the element."""
    element = element or {"xpath": f"#{action}-target", "label": action}
    return storage.add_step(
        run_id, action=action, status=status, value=value,
        target=element.get("label"), element=element, scenario_idx=scenario_idx,
    )


# --- what a recording is allowed to contain -------------------------------- #

def test_an_action_with_nothing_to_act_on_voids_the_recording():
    """A hole in the middle is worse than no recording at all: the replay would
    skip that action and then assert against a screen that never reached the
    state the assertion describes."""
    assert storage.clean_recorded([
        {"action": "click", "selector": "#a"},
        {"action": "click"},
    ]) == []


def test_an_action_on_the_page_itself_needs_no_target(db):
    kept = storage.clean_recorded([
        {"action": "wait", "value": "2"},
        {"action": "scroll", "value": "down"},
    ])
    assert [item["action"] for item in kept] == ["wait", "scroll"]


def test_an_assertion_about_the_screen_needs_no_target_either():
    """Measured on five scenarios: `assert_text` and `assert_absent` read the
    page's text and are driven by the value they look for, so they never carry
    a selector. While that counted as incomplete, one of them at the end of a
    step threw away the recording for every action in it — six of twenty steps.
    """
    kept = storage.clean_recorded([
        {"action": "click", "selector": "#go"},
        {"action": "assert_text", "value": "ESB"},
    ])
    assert [item["action"] for item in kept] == ["click", "assert_text"]
    assert kept[1]["value"] == "ESB"


def test_an_assertion_about_an_element_still_needs_one():
    """assert_visible resolves an element, so a recording of it without a
    selector could not be replayed."""
    assert storage.clean_recorded([{"action": "assert_visible"}]) == []


def test_an_action_a_recording_cannot_carry_voids_it():
    """assert_disabled resolves against a live snapshot's elementId, which a
    recording does not have, so replaying it would always fail."""
    for unreplayable in ("assert_disabled", "explore", "write_scenarios", "done"):
        assert storage.clean_recorded([
            {"action": "click", "selector": "#a"},
            {"action": unreplayable, "selector": "#b"},
        ]) == [], unreplayable


def test_a_check_asked_again_on_an_unchanged_screen_is_dropped():
    """The cookie step was recorded as "Çerez" gone, "Çerezleri kabul et"
    gone, "Çerez" gone, "Çerezleri kabul et" gone, three times over. The
    screen answered each question once; the rest was round trips."""
    kept = storage.clean_recorded([
        {"action": "assert_absent", "value": "Çerez"},
        {"action": "assert_absent", "value": "Çerezleri kabul et"},
        {"action": "assert_absent", "value": "Çerez"},
        {"action": "assert_absent", "value": "Çerezleri kabul et"},
        {"action": "assert_absent", "value": "Çerez"},
    ])
    assert [(a["action"], a["value"]) for a in kept] == [
        ("assert_absent", "Çerez"), ("assert_absent", "Çerezleri kabul et"),
    ]


def test_a_check_after_something_happened_is_a_new_question():
    kept = storage.clean_recorded([
        {"action": "assert_text", "value": "1"},
        {"action": "click", "selector": "#plus"},
        {"action": "assert_text", "value": "1"},
    ])
    assert [a["action"] for a in kept] == ["assert_text", "click", "assert_text"]


def test_a_wait_before_a_check_is_the_check_waiting_twice():
    """`click`, `wait 3`, `wait 5`, `assert_visible` — the search button as
    recorded. The assertion reads the screen until it holds, so the eight
    seconds in front of it were spent for nothing on every replay."""
    kept = storage.clean_recorded([
        {"action": "click", "selector": "#buttonUcusara"},
        {"action": "wait", "value": "3"},
        {"action": "wait", "value": "5"},
        {"action": "assert_visible", "selector": "#flightItem_0"},
    ])
    assert [a["action"] for a in kept] == ["click", "assert_visible"]


def test_a_wait_in_front_of_a_check_that_does_not_wait_is_kept():
    """`assert_no_errors` peeks at what the page has logged so far and
    answers; `assert_visual` compares one screenshot. Neither waits, so the
    wait in front of them is the only thing giving the page time to fail —
    dropping it turns a run that went red into one that goes green."""
    for check in ("assert_no_errors", "assert_visual"):
        kept = storage.clean_recorded([
            {"action": "click", "selector": "#pay"},
            {"action": "wait", "value": "5"},
            {"action": check, "value": "checkout"},
        ])
        assert [a["action"] for a in kept] == ["click", "wait", check], check


def test_waits_before_an_action_become_the_longest_one():
    """A tap on a page still arriving is the one thing a check's patience
    does not cover, so a wait in front of an action stays — one of them."""
    kept = storage.clean_recorded([
        {"action": "click", "selector": "#go"},
        {"action": "wait", "value": "2"},
        {"action": "wait", "value": "5"},
        {"action": "click", "selector": "#next"},
        {"action": "wait", "value": "1"},
    ])
    assert [(a["action"], a.get("value")) for a in kept] == [
        ("click", None), ("wait", "5"), ("click", None), ("wait", "1"),
    ]


def test_a_step_that_took_too_many_actions_is_not_kept():
    """A long recording is a recording of a struggle, and replaying a struggle
    reproduces it."""
    many = [{"action": "click", "selector": f"#a{i}"}
            for i in range(storage.MAX_RECORDED_ACTIONS + 1)]
    assert storage.clean_recorded(many) == []


def test_a_recording_survives_being_written_and_read_back(db, case):
    # clean_steps is the one door every writer goes through — the API, the step
    # editor, the generator — so a recording that survives it survives an edit.
    cleaned = storage.clean_steps([{
        "action": "Search", "expected": "Results",
        "recorded": [{"action": "click", "selector": "#go", "label": "Uçuş ara"}],
    }])
    assert cleaned[0]["recorded"] == [
        {"action": "click", "selector": "#go", "value": None, "label": "Uçuş ara"},
    ]


def test_a_step_with_no_recording_does_not_carry_an_empty_one(db):
    cleaned = storage.clean_steps([{"action": "Search", "expected": "Results"}])
    assert "recorded" not in cleaned[0]


# --- what gets promoted ----------------------------------------------------- #

def test_a_green_run_hands_its_actions_back_to_the_scenario(db, case):
    run_id = a_run(case)
    did(run_id, 1, "click")
    did(run_id, 1, "assert_visible")
    did(run_id, 2, "type", value="IST")
    did(run_id, 2, "assert_text", value="ESB")

    assert db.promote_recording(run_id, case) == 2
    steps = db.get_case(case)["steps"]
    assert [item["action"] for item in steps[0]["recorded"]] == ["click", "assert_visible"]
    assert [item["action"] for item in steps[1]["recorded"]] == ["type", "assert_text"]
    assert steps[1]["recorded"][0]["value"] == "IST"


def test_the_selector_comes_from_the_element_the_action_reached(db, case):
    run_id = a_run(case)
    did(run_id, 1, "click", element={"xpath": "#buttonUcusara", "label": "Uçuş ara"})
    db.promote_recording(run_id, case)
    recorded = db.get_case(case)["steps"][0]["recorded"][0]
    assert recorded["selector"] == "#buttonUcusara"
    assert recorded["label"] == "Uçuş ara"


def test_a_failed_run_with_nothing_to_judge_per_step_teaches_nothing(db, case):
    """A run whose steps were never written down cannot be read step by step,
    so the old rule applies to it: green or nothing. The failure this exists to
    prevent is a recording of a wrong move, replayed faster and without the
    model present to notice it."""
    run_id = a_run(case, status="failed")
    did(run_id, 1, "click")
    assert db.promote_recording(run_id, case) == 0
    assert "recorded" not in db.get_case(case)["steps"][0]


def test_a_step_that_failed_inside_a_green_run_is_not_kept(db, case):
    """The agent recovered by doing something else. Replaying the recovery
    without what prompted it reproduces the mistake, not the fix."""
    run_id = a_run(case)
    did(run_id, 1, "click", status="failed")
    did(run_id, 1, "click")
    did(run_id, 2, "click")

    assert db.promote_recording(run_id, case) == 1
    steps = db.get_case(case)["steps"]
    assert "recorded" not in steps[0]
    assert "recorded" in steps[1]


def test_one_bad_turn_does_not_cost_a_step_its_recording(db, case):
    """This used to delete the old recording whenever a step was not clean.

    It reads as caution and measured as the opposite: recordings never
    accumulated, they were earned and lost again every turn — ten steps on the
    Android set, then two, then ten. A step that stumbles once has not
    disproved what worked last time; the run after it replays that recording
    and finds out, which costs one action rather than a whole step's worth of
    model calls.
    """
    first = a_run(case)
    did(first, 1, "click")
    did(first, 2, "click")
    assert db.promote_recording(first, case) == 2

    second = a_run(case)
    did(second, 1, "click", status="failed")
    did(second, 1, "click")
    did(second, 2, "click")
    db.promote_recording(second, case)
    assert db.get_case(case)["steps"][0].get("recorded"), "kept from the run that worked"


def test_a_recording_that_fails_on_replay_is_dropped(db, case):
    """The other half of it. A stored recording that was actually replayed and
    came back red has disproved itself on this screen, so the next run works it
    out again instead of repeating it faster."""
    first = a_run(case)
    did(first, 1, "click")
    did(first, 2, "click")
    assert db.promote_recording(first, case) == 2

    second = a_run(case)
    storage.add_step(
        second, action="click", status="failed", target="click",
        element={"xpath": "#click-target", "label": "click"}, scenario_idx=1,
        reason="replayed from the last green run",
    )
    did(second, 2, "click")
    db.promote_recording(second, case)
    assert "recorded" not in db.get_case(case)["steps"][0]
    assert db.get_case(case)["steps"][1].get("recorded"), "the other step is untouched"


def test_actions_outside_any_scenario_step_are_ignored(db, case):
    """An open-ended run has no scenario steps to attribute actions to."""
    run_id = a_run(case)
    storage.add_step(run_id, action="click", status="passed",
                     element={"xpath": "#x"})
    assert db.promote_recording(run_id, case) == 0


def test_a_case_that_has_gone_away_is_not_an_error(db, case):
    run_id = a_run(case)
    did(run_id, 1, "click")
    assert db.promote_recording(run_id, "no-such-case") == 0


def test_rewriting_a_step_drops_what_was_recorded_for_the_old_one(db, case):
    """The false green this exists to prevent: a recorded assertion general
    enough to still hold would report the rewritten step as verified without it
    ever having been carried out."""
    run_id = a_run(case)
    did(run_id, 1, "click")
    did(run_id, 2, "click")
    db.promote_recording(run_id, case)

    steps = db.get_case(case)["steps"]
    steps[0]["action"] = "Open the booker in Turkish"
    db.update_case(case, steps=steps)

    after = db.get_case(case)["steps"]
    assert "recorded" not in after[0]
    assert "recorded" in after[1], "the untouched step keeps its recording"


def test_a_step_that_only_moved_keeps_its_recording(db, case):
    """A step that moved is the same step; losing the recording would make
    reordering a scenario cost a full re-derivation of every step in it."""
    run_id = a_run(case)
    did(run_id, 1, "click")
    did(run_id, 2, "type", value="IST")
    db.promote_recording(run_id, case)

    steps = db.get_case(case)["steps"]
    db.update_case(case, steps=[steps[1], steps[0]])

    after = db.get_case(case)["steps"]
    assert [item["recorded"][0]["action"] for item in after] == ["type", "click"]


def test_the_request_model_lets_a_recording_through(db):
    """Found by editing one scenario and watching all four of its recordings
    disappear. The API's step model declared only `action` and `expected`, so
    Pydantic dropped `recorded` on the way in and saving any edit — even one
    that did not touch the steps — erased every recording the scenario had.
    """
    from main import ScenarioStep

    step = ScenarioStep(**{
        "action": "Search", "expected": "Results",
        "recorded": [{"action": "click", "selector": "#go"}],
    })
    assert step.model_dump()["recorded"] == [{"action": "click", "selector": "#go"}]


def test_what_is_written_is_exactly_what_the_replay_reads(db, case):
    """The seam. `promote_recording` writes the recording and the agent's
    `open_step` reads it back; each half is tested on its own, so this is the
    one place a rename on one side and not the other would show up.
    """
    import agent  # imported here so the storage tests stay free of it

    run_id = a_run(case)
    did(run_id, 1, "click", value="IST")
    db.promote_recording(run_id, case)

    recorded = db.get_case(case)["steps"][0]["recorded"][0]
    # Survives the door every writer goes through, unchanged.
    assert db.clean_steps(db.get_case(case)["steps"])[0]["recorded"][0] == recorded
    # And carries what the loop turns into an action, with nothing missing.
    assert set(recorded) == {"action", "selector", "value", "label"}
    assert recorded["action"] in agent.KNOWN_ACTIONS


def test_more_actions_than_the_scenario_has_steps_are_dropped(db, case):
    """A scenario edited down to fewer steps must not grow them back."""
    run_id = a_run(case)
    did(run_id, 1, "click")
    did(run_id, 9, "click")
    db.promote_recording(run_id, case)
    assert len(db.get_case(case)["steps"]) == 2


# --------------------------------------------------------------------------- #
# Borrowing: a scenario that has never run, and a sibling that has
# --------------------------------------------------------------------------- #

def _case_with(db, suite_id, name, url, steps):
    return db.add_case(suite_id, name, "g", url=url, steps=steps)


def test_a_new_scenario_starts_from_what_a_sibling_learned(db):
    """A scenario that has never run pays full price for its first pass. If a
    sibling opens on the same screen and begins the same way, it already knows
    how — measured on the real database, forty-four steps did."""
    suite = db.create_suite("Uçuş Arama", kind="web")
    veteran = _case_with(db, suite, "veteran", "https://nuat.test/", [
        {"action": "Tek yön sekmesini seç", "expected": "seçilir",
         "recorded": [{"action": "click", "selector": "#one-way",
                       "value": None, "label": None}]},
    ])
    rookie = _case_with(db, suite, "rookie", "https://nuat.test/", [
        {"action": "Tek yön sekmesini seç", "expected": "seçilir"},
        {"action": "Uçuş ara", "expected": "liste açılır"},
    ])
    assert veteran  # the lender exists

    cases = db.cases_by_id([rookie])
    assert db.lend_recordings(cases) == 1
    step = cases[0]["steps"][0]
    assert step["recorded"][0]["selector"] == "#one-way"
    assert step["borrowed"] is True
    # The second step has no lender and is left for the agent.
    assert not cases[0]["steps"][1].get("recorded")


def test_nothing_is_written_down_until_it_has_worked(db):
    """Borrowed, not shared. A recording is only valid on the screen it came
    from, so one handed over has not been proved on this scenario yet: it goes
    to the run in memory and the database keeps saying the scenario has none."""
    suite = db.create_suite("Uçuş Arama", kind="web")
    _case_with(db, suite, "veteran", "https://nuat.test/", [
        {"action": "Tek yön sekmesini seç", "expected": "seçilir",
         "recorded": [{"action": "click", "selector": "#one-way"}]},
    ])
    rookie = _case_with(db, suite, "rookie", "https://nuat.test/", [
        {"action": "Tek yön sekmesini seç", "expected": "seçilir"},
    ])

    db.lend_recordings(db.cases_by_id([rookie]))

    assert db.get_case(rookie)["recordedSteps"] == 0, "lending must not persist"


def test_a_step_that_starts_from_another_screen_is_not_lent_to(db):
    """The text alone is not enough. The same instruction reaches a different
    element from a different page, and replaying the wrong one is worse than
    working it out."""
    web = db.create_suite("Web", kind="web")
    _case_with(db, web, "veteran", "https://nuat.test/", [
        {"action": "Nereden alanına tıkla", "expected": "açılır",
         "recorded": [{"action": "click", "selector": "#fromPort"}]},
    ])
    other_page = _case_with(db, web, "elsewhere", "https://nuat.test/checkin", [
        {"action": "Nereden alanına tıkla", "expected": "açılır"},
    ])
    later = _case_with(db, web, "later", "https://nuat.test/", [
        {"action": "Çerezleri kabul et", "expected": "kapanır"},
        {"action": "Nereden alanına tıkla", "expected": "açılır"},
    ])

    cases = db.cases_by_id([other_page, later])
    assert db.lend_recordings(cases) == 0, "different page, and different position"


def test_a_phone_does_not_lend_to_a_browser(db):
    ios = db.create_suite("iOS", kind="mobile", os="ios")
    web = db.create_suite("Web", kind="web")
    _case_with(db, ios, "veteran", "https://nuat.test/", [
        {"action": "Tek yön sekmesini seç", "expected": "seçilir",
         "recorded": [{"action": "click", "selector": "//XCUIElementTypeButton"}]},
    ])
    rookie = _case_with(db, web, "rookie", "https://nuat.test/", [
        {"action": "Tek yön sekmesini seç", "expected": "seçilir"},
    ])

    assert db.lend_recordings(db.cases_by_id([rookie])) == 0


def test_one_phone_does_not_lend_to_the_other(db):
    ios = db.create_suite("iOS", kind="mobile", os="ios")
    android = db.create_suite("Android", kind="mobile", os="android")
    _case_with(db, ios, "veteran", None, [
        {"action": "Tek yön sekmesini seç", "expected": "seçilir",
         "recorded": [{"action": "click", "selector": "//XCUIElementTypeButton"}]},
    ])
    rookie = _case_with(db, android, "rookie", None, [
        {"action": "Tek yön sekmesini seç", "expected": "seçilir"},
    ])

    assert db.lend_recordings(db.cases_by_id([rookie])) == 0


def test_a_data_driven_scenario_is_never_lent_to(db):
    """Its recorded actions carry the values that were typed, and those came
    from one row. The existing rule keeps recordings off these; borrowing one
    would put the previous passenger into this one's form."""
    suite = db.create_suite("Uçuş Arama", kind="web")
    _case_with(db, suite, "veteran", "https://nuat.test/", [
        {"action": "Yolcu adını gir", "expected": "dolar",
         "recorded": [{"action": "type", "selector": "#name", "value": "TEST"}]},
    ])
    driven = db.add_case(
        suite, "driven", "g", url="https://nuat.test/",
        steps=[{"action": "Yolcu adını gir", "expected": "dolar"}],
        dataset=[{"name": "AYŞE"}, {"name": "MEHMET"}],
    )

    assert db.lend_recordings(db.cases_by_id([driven])) == 0


def test_a_scenario_that_already_knows_is_left_alone(db):
    suite = db.create_suite("Uçuş Arama", kind="web")
    _case_with(db, suite, "veteran", "https://nuat.test/", [
        {"action": "Tek yön sekmesini seç", "expected": "seçilir",
         "recorded": [{"action": "click", "selector": "#one-way"}]},
    ])
    owner = _case_with(db, suite, "owner", "https://nuat.test/", [
        {"action": "Tek yön sekmesini seç", "expected": "seçilir",
         "recorded": [{"action": "click", "selector": "#tek-yon"}]},
    ])

    cases = db.cases_by_id([owner])
    assert db.lend_recordings(cases) == 0
    assert cases[0]["steps"][0]["recorded"][0]["selector"] == "#tek-yon"


# --------------------------------------------------------------------------- #
# A step is what is judged, not the run
# --------------------------------------------------------------------------- #

def _stepwise(run_id, verdicts):
    """Open and close the scenario steps the way a written run does."""
    for idx, status in verdicts.items():
        row = storage.start_scenario_step(run_id, idx, f"step {idx}")
        storage.finish_scenario_step(row, status)


def test_a_clean_step_is_kept_even_though_a_later_one_failed(db, case):
    """What a later step did has nothing to do with whether this one worked.

    Measured on the booking flow: four steps, three of them clean, and the run
    kept nothing because the fourth found a real defect — so the next run paid
    full price for three steps that had just been proved.
    """
    run_id = a_run(case, status="failed")
    _stepwise(run_id, {1: "passed", 2: "failed"})
    did(run_id, 1, "click")
    did(run_id, 2, "click", status="failed")

    assert db.promote_recording(run_id, case) == 1
    steps = db.get_case(case)["steps"]
    assert steps[0].get("recorded"), "the step that worked"
    assert not steps[1].get("recorded"), "the step that did not"


def test_a_step_closed_as_failed_is_not_kept_however_clean_its_actions(db, case):
    """Every action passed and the agent still judged the step wrong — the
    expected result did not hold. Replaying the way there reproduces a failure,
    quickly."""
    run_id = a_run(case, status="failed")
    _stepwise(run_id, {1: "failed", 2: "passed"})
    did(run_id, 1, "click")
    did(run_id, 2, "click")

    assert db.promote_recording(run_id, case) == 1
    assert not db.get_case(case)["steps"][0].get("recorded")


def test_a_cancelled_run_teaches_nothing(db, case):
    """Its later steps never ran, and an interrupted one looks clean because
    nothing under it had the chance to fail."""
    run_id = a_run(case, status="cancelled")
    _stepwise(run_id, {1: "passed"})
    did(run_id, 1, "click")

    assert db.promote_recording(run_id, case) == 0
    assert not db.get_case(case)["steps"][0].get("recorded")


def test_an_older_run_with_no_step_record_still_needs_to_be_green(db, case):
    """Runs from before the scenario steps were written down have nothing to
    judge per step, so the rule that was there before applies to them."""
    run_id = a_run(case, status="failed")
    did(run_id, 1, "click")
    assert db.promote_recording(run_id, case) == 0

    green = a_run(case, status="passed")
    did(green, 1, "click")
    did(green, 2, "click")
    assert db.promote_recording(green, case) == 2


# --------------------------------------------------------------------------- #
# A stale recording is not a struggle
# --------------------------------------------------------------------------- #

def _replayed(run_id, scenario_idx, action, status="passed"):
    """An action the runner took from the stored recording."""
    return storage.add_step(
        run_id, action=action, status=status,
        target=action, element={"xpath": f"#{action}", "label": action},
        reason="replayed from the last green run", scenario_idx=scenario_idx,
    )


def test_a_step_that_passed_around_a_stale_recording_keeps_what_worked(db, case):
    """Measured on the Android set, and it cost every run.

    A step's first recorded action aimed at an onboarding "Skip" that stopped
    appearing once the app was restarted between scenarios. The replay failed,
    the model carried the step out its own way, the step passed — and the
    route that worked was thrown away as a struggle. So every run paid ten
    seconds of timeout to be told the same thing again.
    """
    run_id = a_run(case, status="passed")
    _stepwise(run_id, {1: "passed"})
    _replayed(run_id, 1, "click", status="failed")
    did(run_id, 1, "tap_home")
    did(run_id, 1, "assert_text")

    assert db.promote_recording(run_id, case) == 1
    kept = db.get_case(case)["steps"][0].get("recorded")
    assert kept, "the route that worked is written down"
    assert [a["action"] for a in kept] == ["tap_home", "assert_text"], kept


def test_a_model_struggling_is_still_not_recorded(db, case):
    """The rule this sits beside, and the reason it exists: an action the
    model tried and got wrong, then recovered from, must not be replayed
    without whatever prompted the recovery."""
    run_id = a_run(case, status="passed")
    _stepwise(run_id, {1: "passed"})
    did(run_id, 1, "click", status="failed")
    did(run_id, 1, "click")
    did(run_id, 1, "assert_text")

    assert db.promote_recording(run_id, case) == 0
    assert not db.get_case(case)["steps"][0].get("recorded")


def test_a_stale_recording_on_a_step_that_failed_is_still_dropped(db, case):
    """Nothing proved it this time, so there is nothing to replace it with —
    and keeping the recording would repeat the failure faster."""
    run_id = a_run(case, status="failed")
    _stepwise(run_id, {1: "failed"})
    _replayed(run_id, 1, "click", status="failed")

    db.get_case(case)  # the step starts with a recording
    storage.update_case(case, steps=[{
        "action": "step 1", "expected": "",
        "recorded": [{"action": "click", "selector": "#old", "label": "old",
                      "value": None}],
    }])
    db.promote_recording(run_id, case)
    assert not db.get_case(case)["steps"][0].get("recorded")


# --------------------------------------------------------------------------- #
# Healing is the argument, so it has to be readable
# --------------------------------------------------------------------------- #

def test_a_step_that_had_to_be_found_again_says_so(db, case):
    """The product's claim is that it keeps up when the app moves.

    It was doing exactly that and saying nothing: a recording that no longer
    fitted was dropped, the model worked the step out again, the recording was
    refreshed, and the only way to know any of it had happened was to read the
    steps table by hand.
    """
    run_id = a_run(case, status="passed")
    row = storage.start_scenario_step(run_id, 1, "Devam et'e bas")
    storage.finish_scenario_step(
        row, "passed", healed=True,
        healed_note='"Devam et" at #old is now "Continue" at #new')

    step = storage.list_scenario_steps(run_id)[0]
    assert step["healed"] == 1
    assert "#new" in step["healed_note"]


def test_a_step_that_replayed_cleanly_is_not_called_healed(db, case):
    """Otherwise the number means nothing — every recorded step would carry
    it, and a report that says everything moved says nothing moved."""
    run_id = a_run(case, status="passed")
    row = storage.start_scenario_step(run_id, 1, "Tek yön seç")
    storage.finish_scenario_step(row, "passed")

    assert storage.list_scenario_steps(run_id)[0]["healed"] == 0


def test_the_run_counts_the_steps_that_moved(db, case):
    """Counted at the step, not at the action: the action count answers how
    often a selector was re-found, this answers how much of the scenario had
    moved since it last ran."""
    run_id = a_run(case, status="passed")
    for idx, healed in ((1, True), (2, False), (3, True)):
        row = storage.start_scenario_step(run_id, idx, f"step {idx}")
        storage.finish_scenario_step(row, "passed", healed=healed,
                                     healed_note="moved" if healed else None)

    assert storage.get_run(run_id)["healed_steps"] == 2


# --------------------------------------------------------------------------- #
# Asking the same question twice
# --------------------------------------------------------------------------- #

def test_a_check_repeated_straight_after_itself_is_dropped():
    """Found across the sets: one step asserted the same word eight times.

    The answer to a question asked twice in a row is the answer to asking it
    once, so every repeat is a device round trip that proves nothing — about
    three seconds each on a phone. Seventy-three steps were carrying a hundred
    and fifty-four of them.
    """
    kept = storage.clean_recorded([
        {"action": "assert_text", "selector": None, "value": "İstanbul"},
        {"action": "assert_text", "selector": None, "value": "İstanbul"},
        {"action": "assert_text", "selector": None, "value": "İstanbul"},
    ])
    assert len(kept) == 1


def test_a_check_after_something_happened_is_proving_it_survived():
    """Only consecutive repeats go. The same check after a tap is a different
    question: did it still hold once the screen changed."""
    kept = storage.clean_recorded([
        {"action": "assert_text", "selector": None, "value": "IST"},
        {"action": "click", "selector": "#swap", "label": "Swap"},
        {"action": "assert_text", "selector": None, "value": "IST"},
    ])
    assert [a["action"] for a in kept] == ["assert_text", "click", "assert_text"]


def test_two_identical_taps_are_left_alone():
    """A passenger count going up twice is two taps, and dropping one of them
    would record a booking for one passenger fewer."""
    kept = storage.clean_recorded([
        {"action": "click", "selector": "#plus", "label": "+"},
        {"action": "click", "selector": "#plus", "label": "+"},
    ])
    assert len(kept) == 2


def test_the_same_check_on_two_different_things_is_two_checks():
    kept = storage.clean_recorded([
        {"action": "assert_text", "selector": None, "value": "IST"},
        {"action": "assert_text", "selector": None, "value": "ESB"},
    ])
    assert len(kept) == 2


# --------------------------------------------------------------------------- #
# A recording that wrote down a moment
# --------------------------------------------------------------------------- #

def test_a_step_about_soon_does_not_keep_the_day_it_happened_to_pick():
    """"Open the calendar and pick a day a few days from today" was recorded
    clicking the 25th button and checking the screen said "25 Eyl". Correct
    for about a week. Not stale by accident — stale by construction, so
    replaying it buys a failed assertion, a timeout, and then the model doing
    the step anyway."""
    step = {"action": "Tarih alanını aç, bugünden birkaç gün sonrası için "
                      "müsait bir gün seç",
            "expected": "Seçilen gün gidiş tarihi alanında yazar."}
    recorded = [
        {"action": "click", "selector": "#day", "value": None, "label": "25"},
        {"action": "assert_text", "selector": None, "value": "25 Eyl",
         "label": None},
    ]
    assert storage.froze_a_moment(step, recorded)


def test_a_step_about_a_fixed_date_keeps_its_recording():
    """A scenario that means the 25th of September means it every time, and
    throwing its recording away would cost a model call a run for nothing."""
    step = {"action": "25 Eylül tarihini seç",
            "expected": "Tarih alanı 25 Eyl gösterir."}
    recorded = [{"action": "assert_text", "selector": None, "value": "25 Eyl",
                 "label": None}]
    assert not storage.froze_a_moment(step, recorded)


def test_a_relative_step_that_wrote_down_no_day_is_left_alone():
    """Opening the calendar is the same gesture whatever the date is."""
    step = {"action": "Tarih alanını aç ve bugünden bir hafta sonrasını seç",
            "expected": "Takvim açılır."}
    recorded = [{"action": "click", "selector": "#dateField", "value": None,
                 "label": "Tarih"}]
    assert not storage.froze_a_moment(step, recorded)


def test_the_moment_is_recognised_in_either_language():
    english = {"action": "Pick a departure date about a week from today",
               "expected": "The field shows the chosen day."}
    recorded = [{"action": "assert_text", "selector": None,
                 "value": "4 OCT 2026", "label": None}]
    assert storage.froze_a_moment(english, recorded)


def test_the_moment_is_recognised_when_the_store_names_the_day():
    """`{{tarih.gidis}}` with the store holding `today+7` is "a week from
    today" moved one level out. Measured on the iOS set: this step was
    recorded clicking "Sunday, October 4, 2026" and checking "4 OCT 2026",
    and kept, because its own words never said today."""
    step = {"action": "Tap the DEPARTURE DATE field, select {{tarih.gidis}}"
                      " in the calendar and confirm it.",
            "expected": "The field shows the chosen day ({{tarih.gidis}})."}
    recorded = [{"action": "click", "label": None, "value": None,
                 "selector": '//*[@text="Sunday, October 4, 2026, ₺ 2.000,"]'},
                {"action": "click", "selector": '//*[@content-desc="doneButton"]',
                 "label": None, "value": None}]
    assert storage.froze_a_moment(step, recorded, moving_keys={"tarih.gidis"})
    # The same recording under a name that holds a fixed value is fine.
    assert not storage.froze_a_moment(step, recorded, moving_keys=set())
    # And the day in the *selector* is what gave it away.
    assert storage.froze_a_moment(step, recorded[:1], moving_keys={"tarih.gidis"})


def test_a_step_naming_a_moving_day_loses_its_recording_when_promoted(db, case):
    db.set_test_data("tarih.gidis", "today+7", "gidis")
    storage.update_case(case, steps=[{
        "action": "Takvimden {{tarih.gidis}} gününü seç",
        "expected": "Seçilen gün ({{tarih.gidis}}) yazar.",
    }])
    run_id = a_run(case, status="passed")
    _stepwise(run_id, {1: "passed"})
    did(run_id, 1, "click", element={"xpath": "#day-4", "label": "4 Eki"})
    did(run_id, 1, "assert_text", value="4 OCT 2026")

    storage.promote_recording(run_id, case)
    assert not storage.get_case(case)["steps"][0].get("recorded")


def test_a_value_that_came_from_the_store_is_recorded_by_name(db, case):
    """Kept as "Ergün", the recording goes on typing it after the store has
    said otherwise. Kept as {{yolcu.ad}} it types whatever the store says on
    the day — and a card number is never written into a scenario at all."""
    db.set_test_data("yolcu.ad", "Ergün", None)
    db.set_test_data("yolcu.soyad", "Demiro", None)
    db.set_test_data("yolcu.sayi", "1", None)
    storage.update_case(case, steps=[{
        "action": "Ad {{yolcu.ad}}, soyad {{yolcu.soyad}} ve kart üstü isim",
        "expected": "Form dolar",
    }])
    run_id = a_run(case, status="passed")
    _stepwise(run_id, {1: "passed"})
    did(run_id, 1, "type", value="Ergün", element={"xpath": "#name", "label": "Ad"})
    did(run_id, 1, "type", value="Ergün Demiro",
        element={"xpath": "#card-name", "label": "Kart üstü isim"})
    # "1" is in the store too, but this step never names it: a wait of one
    # second is not a passenger count.
    did(run_id, 1, "wait", value="1", element={"xpath": None, "label": None})

    storage.promote_recording(run_id, case)
    values = [a["value"] for a in storage.get_case(case)["steps"][0]["recorded"]]
    assert values == ["{{yolcu.ad}}", "{{yolcu.ad}} {{yolcu.soyad}}", "1"]


def test_a_recording_that_holds_a_secret_is_not_kept(db, case):
    """Three scenarios carried `assert_text "5610 5910 8101 8250"` — the
    screen groups a card number and the recording copied the grouping, so the
    store's unspaced value did not match and the name was never put back. A
    recording lives in the scenario file the whole team reads."""
    db.set_test_data("kart.numara", "5610591081018250", "test", secret=True)
    storage.update_case(case, steps=[{
        "action": "Kart numarasını yaz", "expected": "Alan dolar",
    }])
    run_id = a_run(case, status="passed")
    _stepwise(run_id, {1: "passed"})
    did(run_id, 1, "type", value="5610591081018250",
        element={"xpath": "#card", "label": "Kart numarası"})
    did(run_id, 1, "assert_text", value="5610 5910 8101 8250")

    storage.promote_recording(run_id, case)
    assert not storage.get_case(case)["steps"][0].get("recorded")


def test_a_scenario_step_never_carries_a_secret_either(db):
    """The scenario on disk says {{kart.numara}}; what reaches the run has
    already been filled in, and this row is what the run detail, the JUnit
    file a CI job attaches and an automatically raised bug all quote."""
    db.set_test_data("kart.numara", "5610591081018250", "test", secret=True)
    db.set_test_data("kart.cvv", "123", "test", secret=True)
    run_id = storage.create_run(goal="a run", kind="web")
    row = storage.start_scenario_step(
        run_id, 1, "Type 5610591081018250 into the card number field.",
        "The field shows 5610591081018250.",
    )
    storage.finish_scenario_step(row, "failed",
                                 message="Typed 123 into the CVV field, 1123ms")

    step = storage.list_scenario_steps(run_id)[0]
    assert step["action"] == "Type {{kart.numara}} into the card number field."
    assert step["expected"] == "The field shows {{kart.numara}}."
    # A short secret only where it stands as a word of its own: 123 inside
    # 1123 is a duration, not a code.
    assert step["message"] == "Typed {{kart.cvv}} into the CVV field, 1123ms"


def test_a_secret_is_named_in_the_run_log_never_spelled(db):
    """The run typed the card number; the run's log says it typed
    {{kart.numara}}. Recordings are built from these rows, so a card never
    reaches a scenario either."""
    db.set_test_data("kart.numara", "4111111111111111", "test", secret=True)
    db.set_test_data("kart.cvv", "123", "test", secret=True)
    run_id = storage.create_run(goal="a run", kind="web")
    storage.add_step(
        run_id, action="type", status="passed", value="4111111111111111",
        target="Kart numarası", message='Typed "4111111111111111" into "Kart numarası"',
        element={"xpath": "#card", "label": "Kart numarası"}, scenario_idx=1,
    )
    storage.add_step(run_id, action="type", status="passed", value="123",
                     message='Typed "123" into "CVV"', scenario_idx=1)
    storage.add_step(run_id, action="wait", status="passed", value="1123",
                     message="Waited 1123ms", scenario_idx=1)

    steps = storage.get_run(run_id)["steps"]
    assert steps[0]["value"] == "{{kart.numara}}"
    assert steps[0]["message"] == 'Typed "{{kart.numara}}" into "Kart numarası"'
    assert steps[1]["value"] == "{{kart.cvv}}"
    # A short secret is only taken for itself when it is the whole value:
    # "123" inside a wait is not a card.
    assert steps[2]["value"] == "1123"
    assert steps[2]["message"] == "Waited 1123ms"


def test_a_dated_step_loses_its_recording_when_the_run_is_promoted(db, case):
    """End to end, because the rule is only worth anything where it is read."""
    storage.update_case(case, steps=[{
        "action": "Tarih alanını aç, bugünden birkaç gün sonrasını seç",
        "expected": "Seçilen gün yazar.",
        "recorded": [{"action": "assert_text", "selector": None,
                      "value": "25 Eyl", "label": None}],
    }])
    run_id = a_run(case, status="passed")
    _stepwise(run_id, {1: "passed"})
    did(run_id, 1, "assert_text", value="27 Eyl")

    storage.promote_recording(run_id, case)
    assert not storage.get_case(case)["steps"][0].get("recorded")
