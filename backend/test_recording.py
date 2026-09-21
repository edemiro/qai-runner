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
