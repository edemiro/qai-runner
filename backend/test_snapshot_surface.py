"""The two screens the agent drives have to answer the same calls.

`_execute_action` takes a snapshot and asks it questions without knowing
whether it came from a browser or a phone. When the two types drift apart, the
drift is invisible until a scenario reaches the one call that differs — and
then it is not a failed assertion but a crash mid-run.

Measured: `contains_text` gained an `include_hidden` argument on the phone and
not on the page, and every web run that reached an `assert_absent` died with
"got an unexpected keyword argument". The booking scenario lost a whole run to
it. `assert_text` had survived the same drift only because the call site
guarded with hasattr, which is what kept the gap invisible.
"""

import inspect

import pytest

from mobile_dom import MobileDOMManager
from web_dom import WebSnapshot

# What the agent actually calls on a snapshot, whatever it is driving.
SHARED = ("contains_text", "locate_text", "find_text", "get_all_elements",
          "visible_text")


def parameters(cls, name):
    """The call shape, without the return annotation — the two return their
    own element types and always will."""
    signature = inspect.signature(getattr(cls, name))
    return [
        (p.name, p.default is inspect.Parameter.empty)
        for p in signature.parameters.values()
    ]


@pytest.mark.parametrize("name", SHARED)
def test_both_screens_offer_it(name):
    assert hasattr(WebSnapshot, name), f"a page cannot answer {name}"
    assert hasattr(MobileDOMManager, name), f"a phone cannot answer {name}"


@pytest.mark.parametrize("name", SHARED)
def test_and_take_the_same_arguments(name):
    assert parameters(WebSnapshot, name) == parameters(MobileDOMManager, name), name


def test_the_page_has_no_third_answer_about_where_text_is():
    """A phone can say "hidden" — iOS decides visibility by hit-testing and is
    wrong about a word behind a keyboard bar. A page cannot: whatever is not
    on it was dropped before the snapshot existed."""
    page = WebSnapshot({"nodes": [
        {"role": "text", "tag": "p", "text": "Devam et", "selector": "p",
         "bounds": {"x1": 0, "y1": 0, "x2": 80, "y2": 20}, "parentIndex": -1},
    ]})
    assert page.find_text("Devam et") == "visible"
    assert page.find_text("Iptal") is None


def test_the_widest_reading_is_accepted_on_both():
    """assert_absent asks for it, and must not crash on either."""
    page = WebSnapshot({"nodes": []})
    assert page.contains_text("Devam et", include_hidden=True) is False
