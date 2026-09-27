"""Typing into a <select> chooses.

A select cannot be filled and its options are not elements on the page, so
the model had nothing to type into and nothing to click. Measured on the
payment form: the expiry month and year are selects, and "02" typed into the
month cost twelve actions and the step. The option is found by what the
step means — "02", "2", "Şubat" are the same month; "30" is "2030" — and when
nothing matches, the choices are in the error so the next try is one of them.
"""

import asyncio

import pytest

from drivers.web import WebTarget


class Select:
    def __init__(self, options):
        self.options = options
        self.chosen = None

    async def evaluate(self, script):
        return [{"value": v, "label": l} for v, l in self.options]

    async def select_option(self, value, timeout=None):
        self.chosen = value


MONTHS = [("", "Ay"), ("01", "01"), ("02", "02"), ("12", "12")]
YEARS = [("", "Yıl"), ("2026", "2026"), ("2030", "2030")]
NAMED = [("", "Choose"), ("tr", "Türkiye"), ("gb", "United Kingdom")]


def choose(options, wanted):
    select = Select(options)
    label = asyncio.run(WebTarget._choose(select, wanted))
    return select.chosen, label


def test_the_exact_label_or_value_wins():
    assert choose(MONTHS, "02") == ("02", "02")
    assert choose(NAMED, "Türkiye") == ("tr", "Türkiye")
    assert choose(NAMED, "gb") == ("gb", "United Kingdom")


def test_a_number_is_matched_as_a_number():
    """The step says "2"; the select lists "02"."""
    assert choose(MONTHS, "2") == ("02", "02")
    assert choose(YEARS, "2030") == ("2030", "2030")


def test_a_two_digit_year_finds_the_full_one():
    """The card says 02/30; the select lists 2030."""
    assert choose(YEARS, "30") == ("2030", "2030")


def test_a_word_inside_the_label_is_enough():
    assert choose(NAMED, "kingdom") == ("gb", "United Kingdom")


def test_nothing_matching_names_the_choices():
    with pytest.raises(ValueError) as caught:
        choose(MONTHS, "Şubat")
    assert 'no option reads "Şubat"' in str(caught.value)
    assert "01, 02, 12" in str(caught.value)
