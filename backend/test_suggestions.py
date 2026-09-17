"""Suggestions must describe steps that can actually be carried out here.

The rule these all come back to: a suggestion names a control on the screen and
assumes nothing about it that the screen does not say. A suggestion that cannot
be followed is worse than no suggestion — it is the first thing a new user
tries, and it fails in a way that looks like the agent is broken.
"""

import unittest

import suggestions


class FakeElement:
    def __init__(self, label, role="button", clickable=True, visible=True):
        self.text = label
        self.name = None
        self.resource_id = None
        self.role = role
        self.clickable = clickable
        self.displayed = True
        self.visible = visible


class FakeSnapshot:
    def __init__(self, elements):
        self._elements = elements

    def get_all_elements(self):
        return self._elements


def texts(snapshot, kind="mobile"):
    return [s["text"] for s in suggestions.for_snapshot(snapshot, kind)]


def ids(snapshot, kind="mobile"):
    return [s["id"] for s in suggestions.for_snapshot(snapshot, kind)]


class CheckInIsAJourneyNotADate(unittest.TestCase):
    """The reported bug: an airline home screen offered "pick a date from the
    Check-in field". Check-in is a button that opens a journey, and the screen
    has no date field on it at all."""

    HOME = FakeSnapshot([
        FakeElement("Book a flight"),
        FakeElement("Check-in"),
        FakeElement("Menu"),
    ])

    def test_a_check_in_button_does_not_produce_a_date_step(self):
        self.assertNotIn("date", ids(self.HOME))
        self.assertFalse(any("tarih seç" in t for t in texts(self.HOME)))

    def test_check_in_is_offered_as_its_own_flow(self):
        self.assertIn("checkin", ids(self.HOME))

    def test_the_booking_flow_is_offered_and_names_the_real_button(self):
        booking = next(s for s in suggestions.for_snapshot(self.HOME, "mobile")
                       if s["id"] == "booking")
        self.assertIn("Book a flight", booking["text"])

    def test_a_real_date_field_still_gets_a_date_step(self):
        screen = FakeSnapshot([FakeElement("Gidiş tarihi", role="textbox")])
        self.assertIn("date", ids(screen))

    def test_a_date_heading_alone_is_not_enough(self):
        # A heading that says "Tarih" is not something a date can be picked in.
        screen = FakeSnapshot([FakeElement("Tarih", role="text", clickable=False)])
        self.assertNotIn("date", ids(screen))


class SuggestionsFollowTheScreen(unittest.TestCase):

    def test_the_menu_step_is_only_offered_when_there_is_a_menu(self):
        with_menu = FakeSnapshot([FakeElement("Menu")])
        without = FakeSnapshot([FakeElement("Devam et")])
        self.assertIn("reachable", ids(with_menu))
        self.assertNotIn("reachable", ids(without))

    def test_a_control_behind_a_modal_is_not_suggested(self):
        # On a phone the tree still carries the screen underneath an open
        # sheet, and pressing what is under it is not a step anyone can take.
        covered = FakeSnapshot([
            FakeElement("Book a flight", visible=False),
            FakeElement("Check-in", visible=False),
            FakeElement("Sign in"),
        ])
        self.assertNotIn("booking", ids(covered))
        self.assertNotIn("checkin", ids(covered))

    def test_a_search_step_needs_somewhere_to_type(self):
        button_only = FakeSnapshot([FakeElement("Ara")])
        with_box = FakeSnapshot([
            FakeElement("Ara"), FakeElement("Nereye", role="textbox"),
        ])
        self.assertNotIn("search", ids(button_only))
        self.assertIn("search", ids(with_box))

    def test_every_suggestion_quotes_something_on_the_screen(self):
        screen = FakeSnapshot([
            FakeElement("Book a flight"), FakeElement("Check-in"),
            FakeElement("Menu"), FakeElement("Giriş yap"),
        ])
        labels = {"Book a flight", "Check-in", "Menu", "Giriş yap"}
        for suggestion in suggestions.for_snapshot(screen, "mobile"):
            quoted = [part for part in suggestion["text"].split('"')[1::2]]
            for name in quoted:
                self.assertIn(name, labels,
                              f'{suggestion["id"]} quotes "{name}", which is not on screen')

    def test_an_empty_screen_falls_back_without_naming_anything(self):
        for suggestion in suggestions.for_snapshot(FakeSnapshot([]), "mobile"):
            self.assertNotIn('"', suggestion["text"])


if __name__ == "__main__":
    unittest.main()


class LabelsAreMatchedWholeNotInPassing(unittest.TestCase):
    """A word inside a label is not the same as the label.

    Two suggestions were wrong for this reason: "Check-in" read as a date field
    because the date pattern contained check-in, and "Digital menu" — an
    inflight food menu — read as the navigation menu because it contains the
    word menu.
    """

    def test_an_inflight_menu_row_is_not_the_navigation_menu(self):
        screen = FakeSnapshot([FakeElement("Digital menu", role="cell")])
        self.assertNotIn("reachable", ids(screen))

    def test_the_real_menu_control_is_still_found(self):
        for label in ("Menu", "Menü", "Ana menü"):
            screen = FakeSnapshot([FakeElement(label)])
            self.assertIn("reachable", ids(screen), label)


class ListRowsCountAsPressable(unittest.TestCase):
    """iOS reports a table cell with clickable unset, but a list row is exactly
    what a menu is made of — refusing them left a screen full of choices
    looking like it had none."""

    MENU = FakeSnapshot([
        FakeElement("Book a flight", role="cell", clickable=False),
        FakeElement("Check-in", role="cell", clickable=False),
        FakeElement("Manage booking", role="cell", clickable=False),
    ])

    def test_a_menu_of_rows_produces_its_flows(self):
        self.assertIn("booking", ids(self.MENU))
        self.assertIn("checkin", ids(self.MENU))

    def test_a_row_is_never_treated_as_a_field(self):
        # A row labelled "Gidiş tarihi" is something to open, not to type in.
        screen = FakeSnapshot([FakeElement("Gidiş tarihi", role="cell", clickable=False)])
        self.assertNotIn("date", ids(screen))
