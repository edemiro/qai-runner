"""A <select> tells the model what it offers.

Its options are not elements on the page, so without this the model saw a
combobox called "Ay" and nothing to choose from — and learned the list, if
at all, from the error of a wrong guess.
"""

from web_dom import WebElement


def raw(**over):
    base = {
        "role": "combobox", "tag": "select", "id": "cmonths", "testId": None,
        "text": None, "label": "Son kullanma tarihi - Ay", "value": "Ay",
        "options": ["Ay", "01", "02", "12"], "selector": "#cmonths",
        "bounds": {"x1": 0, "y1": 0, "x2": 80, "y2": 30},
        "enabled": True, "checked": None, "href": None, "parentIndex": -1,
    }
    base.update(over)
    return base


def test_a_select_carries_its_options_to_the_model():
    element = WebElement(raw())
    assert element.to_llm_dict()["options"] == ["Ay", "01", "02", "12"]
    # What it currently says is its text, like any other field.
    assert element.text == "Ay"


def test_a_field_that_is_not_a_select_says_nothing_about_options():
    element = WebElement(raw(tag="input", role="textbox", options=None, value="5610"))
    assert "options" not in element.to_llm_dict()
    element = WebElement(raw(options=[]))
    assert "options" not in element.to_llm_dict()
