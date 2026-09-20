"""The shape a scenario arrives in.

The generator writes `title` and `type`; this API has always taken `name` and
`scenarioType`. The UI maps between them on the way through, so the mismatch
was invisible there — but feeding generated scenarios straight back, which is
what a CI job or a script does, came back 422 naming a field the caller had
never heard of. Both spellings are accepted now, and the point of these is that
neither stopped working.
"""

import pytest

from main import CaseBody, CasePatchBody

GENERATED = {
    "title": "Booking - Availability | OW DOM [IST-ESB] - search and control the results",
    "goal": "Search a one way domestic flight and control the availability page.",
    "type": "Positive",
    "layer": "E2E",
    "priority": "High",
    "steps": [{"action": "Tap Uçuş ara", "expected": "Results are listed"}],
}


def test_the_generators_own_output_is_accepted_unchanged():
    case = CaseBody(**GENERATED)
    assert case.name.startswith("Booking - Availability")
    assert case.scenarioType == "Positive"
    assert case.steps[0].action == "Tap Uçuş ara"


def test_the_spelling_this_api_has_always_taken_still_works():
    case = CaseBody(name="A scenario", goal="do it", scenarioType="Negative")
    assert case.name == "A scenario"
    assert case.scenarioType == "Negative"


def test_the_canonical_name_wins_when_both_are_sent():
    """A caller that maps and then forwards the original should not get the
    unmapped one back."""
    case = CaseBody(**{**GENERATED, "name": "The mapped name"})
    assert case.name == "The mapped name"


def test_a_scenario_with_neither_spelling_is_still_refused():
    """The alias widens what is accepted; it does not make the field optional —
    a case with no name at all is a row nothing can show."""
    with pytest.raises(Exception):
        CaseBody(goal="do it")


def test_a_patch_takes_the_generators_spellings_too():
    """So a scenario can be sent back for editing in the shape it came out in."""
    patch = CasePatchBody(title="Renamed", type="Boundary")
    assert patch.name == "Renamed"
    assert patch.scenarioType == "Boundary"


def test_a_patch_still_carries_only_what_was_sent():
    """The partial patch is the whole reason this model exists: an edit that
    answers one field must not blank the rest."""
    patch = CasePatchBody(preconditionData={"member": "123"})
    assert patch.model_dump(exclude_none=True) == {"preconditionData": {"member": "123"}}
