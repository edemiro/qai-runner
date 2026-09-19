"""Exploratory crawl classification, safety guards, and page-derived suggestions."""

import pytest

import explorer
import suggestions


# --------------------------------------------------------------------------- #
# Test doubles shaped like WebElement / WebSnapshot
# --------------------------------------------------------------------------- #

class FakeElement:
    def __init__(self, role, text=None, name=None, href=None, tag="button",
                 selector=None, resource_id=None, y=0, x=0):
        self.role = role
        self.tag = tag
        self.text = text
        self.name = name
        self.href = href
        self.resource_id = resource_id
        self.selector = selector or f"#{resource_id or 'el'}"
        self.clickable = role in {
            "button", "link", "checkbox", "switch", "radio", "tab", "textbox", "combobox",
        }
        self.enabled = True
        self.element_id = None
        self.bounds = {"x1": x, "y1": y, "x2": x + 80, "y2": y + 30, "width": 80, "height": 30}

    def is_actionable(self, *_):
        return True


class FakeSnapshot:
    def __init__(self, elements, url="https://shop.test/", title="Shop"):
        self.url = url
        self.title = title
        for index, element in enumerate(elements):
            element.element_id = f"el_{index}"
        self._elements = elements

    def get_all_elements(self):
        return self._elements


# --------------------------------------------------------------------------- #
# What a click did
# --------------------------------------------------------------------------- #

BASE = {
    "url": "https://x.test/", "title": "t", "hash": 111, "textLength": 100,
    "nodes": 50, "dialogs": 0, "toggles": "00", "scrollY": 0,
}


def test_a_changed_url_is_a_navigation():
    after = {**BASE, "url": "https://x.test/detail"}
    assert explorer.classify(BASE, after, 0)["outcome"] == "navigated"


def test_a_new_dialog_is_reported_as_a_dialog():
    assert explorer.classify(BASE, {**BASE, "dialogs": 1}, 0)["outcome"] == "dialog"


def test_changed_text_is_a_content_change():
    result = explorer.classify(BASE, {**BASE, "hash": 222, "textLength": 140}, 0)
    assert result["outcome"] == "changed"
    assert "+40" in result["detail"]


def test_a_toggled_checkbox_counts_as_a_change():
    """Toggling a checkbox leaves innerText identical, so without the toggle
    signature every working checkbox would be reported as dead."""
    assert explorer.classify(BASE, {**BASE, "toggles": "10"}, 0)["outcome"] == "changed"


def test_a_silent_request_is_distinguished_from_a_dead_control():
    assert explorer.classify(BASE, dict(BASE), 3)["outcome"] == "request"


def test_scrolling_counts_as_a_change():
    assert explorer.classify(BASE, {**BASE, "scrollY": 400}, 0)["outcome"] == "changed"


def test_nothing_at_all_is_dead():
    assert explorer.classify(BASE, dict(BASE), 0)["outcome"] == "dead"


def test_a_new_node_count_alone_is_a_change():
    assert explorer.classify(BASE, {**BASE, "nodes": 51}, 0)["outcome"] == "changed"


# --------------------------------------------------------------------------- #
# Safety: what must never be clicked
# --------------------------------------------------------------------------- #

ORIGIN = "https://shop.test"


@pytest.mark.parametrize("label", [
    "Hesabı sil", "Sil", "Çıkış yap", "Oturumu kapat", "Log out", "Sign Out",
    "Satın al", "Ödeme yap", "Checkout", "Place order", "Delete", "Remove",
    "Abone ol", "Subscribe", "Hesabı kapat",
])
def test_destructive_and_paid_labels_are_blocked(label):
    assert explorer.should_skip(label, None, ORIGIN) is not None


@pytest.mark.parametrize("label", [
    "Ara", "Sepete ekle", "Detaya git", "Filtrele", "Giriş yap", "Kaydet", "Devam",
])
def test_ordinary_labels_are_allowed(label):
    assert explorer.should_skip(label, None, ORIGIN) is None


def test_an_off_site_link_is_skipped():
    reason = explorer.should_skip("Partner", "https://other.test/x", ORIGIN)
    assert reason is not None and "other.test" in reason


def test_a_same_site_link_is_allowed():
    assert explorer.should_skip("Detay", "https://shop.test/detail", ORIGIN) is None


@pytest.mark.parametrize("href", ["mailto:a@b.c", "tel:+90", "sms:123", "file:///etc"])
def test_links_that_leave_the_browser_are_skipped(href):
    assert explorer.should_skip("Yaz", href, ORIGIN) is not None


def test_a_relative_link_is_allowed():
    assert explorer.should_skip("Detay", "/detail", ORIGIN) is None


# --------------------------------------------------------------------------- #
# Choosing what to probe
# --------------------------------------------------------------------------- #

def test_text_inputs_are_not_click_probed():
    """Clicking an input only moves the caret, so probing one would report
    every field on the page as dead and bury the real findings."""
    snapshot = FakeSnapshot([
        FakeElement("textbox", name="Ara", resource_id="q"),
        FakeElement("button", text="Gönder", resource_id="go"),
    ])
    worklist = explorer.build_worklist(snapshot, ORIGIN, 20)
    assert [item["label"] for item in worklist] == ["Gönder"]


def test_repeated_identical_controls_are_capped():
    """A product grid with fifty identical buttons would eat the whole budget."""
    snapshot = FakeSnapshot([
        FakeElement("button", text="Sepete ekle", resource_id=f"b{i}", y=i * 40)
        for i in range(10)
    ])
    worklist = explorer.build_worklist(snapshot, ORIGIN, 20)
    assert len(worklist) == explorer.MAX_PER_LABEL


def test_differently_labelled_controls_are_all_kept():
    snapshot = FakeSnapshot([
        FakeElement("button", text=f"Eylem {i}", resource_id=f"b{i}", y=i * 40)
        for i in range(5)
    ])
    assert len(explorer.build_worklist(snapshot, ORIGIN, 20)) == 5


def test_the_worklist_respects_its_ceiling():
    snapshot = FakeSnapshot([
        FakeElement("button", text=f"E{i}", resource_id=f"b{i}", y=i * 40) for i in range(30)
    ])
    assert len(explorer.build_worklist(snapshot, ORIGIN, 7)) == 7


def test_elements_are_probed_in_reading_order():
    snapshot = FakeSnapshot([
        FakeElement("button", text="Alt", resource_id="c", y=300),
        FakeElement("button", text="Üst", resource_id="a", y=10),
        FakeElement("button", text="Orta", resource_id="b", y=150),
    ])
    assert [i["label"] for i in explorer.build_worklist(snapshot, ORIGIN, 10)] == ["Üst", "Orta", "Alt"]


def test_a_risky_control_is_listed_but_carries_its_reason():
    """It stays in the report — "we did not test this, and why" is information."""
    snapshot = FakeSnapshot([FakeElement("button", text="Hesabı sil", resource_id="d")])
    item = explorer.build_worklist(snapshot, ORIGIN, 10)[0]
    assert item["skip"] is not None


def test_an_unnamed_control_is_identified_by_its_selector():
    element = FakeElement("button", resource_id=None, selector="button.icon-close")
    label = explorer._label_of(element)
    assert "button.icon-close" in label


# --------------------------------------------------------------------------- #
# Suggestions read off the page
# --------------------------------------------------------------------------- #

def _texts(snapshot):
    return " | ".join(s["text"] for s in suggestions.for_snapshot(snapshot))


def test_a_search_page_gets_a_search_suggestion():
    snapshot = FakeSnapshot([
        FakeElement("textbox", name="Şehir", resource_id="q"),
        FakeElement("button", text="Ara", resource_id="go"),
    ])
    assert "Arama kutusuna" in _texts(snapshot)
    assert '"Ara" butonuna bas' in _texts(snapshot)


def test_a_search_box_with_no_button_does_not_invent_one():
    snapshot = FakeSnapshot([
        FakeElement("textbox", name="Arama", resource_id="q"),
    ])
    assert "Enter'a bas" in _texts(snapshot)


def test_a_login_page_gets_the_wrong_password_suggestion():
    snapshot = FakeSnapshot([
        FakeElement("textbox", name="E-posta", resource_id="e"),
        FakeElement("button", text="Giriş yap", resource_id="in"),
    ])
    text = _texts(snapshot)
    assert "geçersiz" in text
    assert "boş gönder" in text


def test_a_cookie_banner_is_suggested_first():
    snapshot = FakeSnapshot([
        FakeElement("button", text="Tümünü kabul et", resource_id="c"),
        FakeElement("button", text="Ara", resource_id="s"),
    ])
    assert suggestions.for_snapshot(snapshot)[0]["id"] == "cookie"


def test_a_shop_page_gets_a_cart_suggestion():
    snapshot = FakeSnapshot([FakeElement("button", text="Sepete ekle", resource_id="a")])
    assert "sepete ekle" in _texts(snapshot).lower()


def test_an_unrecognised_page_names_its_own_controls():
    snapshot = FakeSnapshot([
        FakeElement("button", text="Zümrüt", resource_id="z"),
        FakeElement("link", text="Kuşpalazı", resource_id="k", href="/k"),
    ])
    text = _texts(snapshot)
    assert "Zümrüt" in text


def test_the_generic_fallback_never_suggests_clicking_a_heading():
    """Only real controls are offered; a heading is not clickable."""
    snapshot = FakeSnapshot([
        FakeElement("text", text="Bir başlık", tag="h1"),
        FakeElement("button", text="Devam", resource_id="d"),
    ])
    text = _texts(snapshot)
    assert "Bir başlık" not in text
    assert "Devam" in text


def test_the_universal_actions_are_always_offered():
    snapshot = FakeSnapshot([FakeElement("button", text="Bir şey", resource_id="x")])
    ids = {s["id"] for s in suggestions.for_snapshot(snapshot)}
    assert {"explore", "links", "a11y"} <= ids


def test_suggestions_survive_a_page_with_nothing_on_it():
    assert suggestions.for_snapshot(FakeSnapshot([]))


def test_suggestions_survive_no_snapshot_at_all():
    assert suggestions.for_snapshot(None) == suggestions.UNIVERSAL


def test_the_page_summary_names_what_kind_of_page_it_is():
    snapshot = FakeSnapshot([
        FakeElement("button", text="Sepete ekle", resource_id="c"),
        FakeElement("button", text="Giriş yap", resource_id="l"),
    ])
    described = suggestions.describe_page(snapshot)
    assert "giriş" in described["kind"]
    assert "e-ticaret" in described["kind"]
    assert described["controls"] == 2


def test_describing_a_missing_snapshot_does_not_raise():
    assert suggestions.describe_page(None)["kind"] == "bilinmiyor"


# --------------------------------------------------------------------------- #
# Summary wording
# --------------------------------------------------------------------------- #

# --------------------------------------------------------------------------- #
# Telling a real defect from noise the page was already making
# --------------------------------------------------------------------------- #

def _console(text, level="error"):
    return {"kind": "console", "level": level, "text": text}


def test_the_same_error_twice_has_the_same_signature():
    assert explorer.signature(_console("Boom happened")) == explorer.signature(_console("Boom happened"))


def test_volatile_ids_do_not_change_a_signature():
    """The same WebGL warning differs only by a pointer address each time; if
    that counted, every navigation would look like a brand-new error."""
    a = _console("[.WebGL-0x4f6c01c82c00] GL Driver Message")
    b = _console("[.WebGL-0x537c01b1b800] GL Driver Message")
    assert explorer.signature(a) == explorer.signature(b)


def test_genuinely_different_errors_have_different_signatures():
    assert explorer.signature(_console("Credential service failed")) != explorer.signature(
        _console("terminate stream because StartStreamFailure")
    )


def test_a_baseline_is_built_from_the_page_load():
    baseline = explorer.baseline_signatures([_console("A"), _console("B")])
    assert explorer.signature(_console("A")) in baseline
    assert explorer.signature(_console("C")) not in baseline


def test_a_server_error_is_significant_whatever_asked_for_it():
    """5xx is the one answer that can only mean the server broke."""
    from drivers.web import _network_level
    for resource in ("document", "xhr", "fetch", "script", "image", "other"):
        assert _network_level(resource, 500) == "error"
        assert _network_level(resource, 503) == "error"


def test_a_document_that_never_arrived_is_significant():
    """No status at all means the request never completed. That decides a
    verdict only when it was the page itself."""
    from drivers.web import _network_level
    assert _network_level("document", None) == "error"
    for resource in ("xhr", "fetch", "script", "image"):
        assert _network_level(resource, None) == "warning"


def test_a_4xx_is_only_a_notice():
    """A 4xx is the server answering deliberately, and a live site answers
    plenty of them through a working flow: a 404 on a bot-protection script,
    a 404 on a RUM beacon, a 428 on an API that then retries with the token it
    was being asked for, a 410 on a challenge-loader document. Whether that
    answer mattered to the feature is what the scenario's assertions are for."""
    from drivers.web import _network_level
    for resource in ("document", "xhr", "fetch", "script", "image", "font", "stylesheet"):
        for status in (400, 401, 403, 404, 410, 428, 429):
            assert _network_level(resource, status) == "warning"


def test_describe_event_names_what_went_wrong():
    line = explorer.describe_event({
        "kind": "httperror", "text": "HTTP 500 Internal Server Error",
        "url": "https://shop.test/api/cart", "resourceType": "xhr", "status": 500,
    })
    assert "500" in line and "/api/cart" in line and "xhr" in line


def test_describe_event_collapses_multiline_console_text():
    line = explorer.describe_event(_console("ErrorUtils caught:\n\n  stream failed\n"))
    assert "\n" not in line
    assert "ErrorUtils caught: stream failed" in line


def test_a_third_party_url_is_shown_with_its_host():
    line = explorer.describe_event({
        "kind": "httperror", "text": "HTTP 404", "url": "https://ads.other.test/px.gif",
        "thirdParty": True, "resourceType": "image",
    })
    assert "ads.other.test" in line


def test_the_summary_names_the_dead_controls():
    summary = explorer._summarise(
        {"dead": 2}, [{"label": "Rapor al"}, {"label": "Dışa aktar"}], [],
    )
    assert "Rapor al" in summary and "Dışa aktar" in summary


def test_a_clean_crawl_says_so():
    summary = explorer._summarise({"changed": 3, "navigated": 1}, [], [])
    assert "hepsi yanıt verdi" in summary
