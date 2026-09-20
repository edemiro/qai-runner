"""Reading a story or an analysis page as the text scenarios are written from.

Nothing here talks to a server. What is worth pinning is the part that goes
wrong silently: which link is which, the shapes a link can arrive in, and
turning Confluence's XHTML into something a reader — and a model — can follow.
"""

import pytest

import tracker


@pytest.fixture(autouse=True)
def _servers(monkeypatch):
    monkeypatch.setenv("JIRA_BASE_URL", "https://jira.thy.com")
    monkeypatch.setenv("JIRA_TOKEN", "a-token")
    monkeypatch.setenv("CONFLUENCE_BASE_URL", "https://confluence.thy.com")
    monkeypatch.setenv("CONFLUENCE_TOKEN", "a-token")


# --- which service a link belongs to --------------------------------------- #

def test_a_link_is_matched_against_the_configured_servers_first():
    """A company can call these anything, so the host it was set up with beats
    any guess made from the path."""
    assert tracker.service_for("https://jira.thy.com/browse/TKM-1") == "jira"
    assert tracker.service_for(
        "https://confluence.thy.com/display/QA/Analiz") == "confluence"


def test_a_link_to_neither_is_neither():
    assert tracker.service_for("https://example.com/browse/X-1") is None
    assert tracker.service_for("") is None
    assert tracker.service_for("not a url") is None


def test_the_path_is_the_fallback_before_anything_is_configured(monkeypatch):
    """A link pasted before the settings were saved should still be recognised,
    so the error can say what is missing rather than what is wrong."""
    monkeypatch.delenv("JIRA_BASE_URL", raising=False)
    monkeypatch.delenv("CONFLUENCE_BASE_URL", raising=False)
    assert tracker.service_for("https://jira.internal/browse/ABC-9") == "jira"
    assert tracker.service_for("https://confluence.internal/x") == "confluence"


# --- the shapes a link arrives in ------------------------------------------ #

def test_an_issue_key_is_found_wherever_it_sits():
    cases = {
        "https://jira.thy.com/browse/TKM-1234": "TKM-1234",
        "https://jira.thy.com/browse/tkm-7": "TKM-7",
        # Copied off a board or a filter, which is not a mistake.
        "https://jira.thy.com/secure/RapidBoard.jspa?selectedIssue=TKM-42": "TKM-42",
        "TKM-99": "TKM-99",
    }
    for url, expected in cases.items():
        assert tracker.issue_key(url) == expected, url


def test_something_that_is_not_an_issue_key_is_not_invented():
    for url in ("https://jira.thy.com/projects/TKM", "https://jira.thy.com/", ""):
        assert tracker.issue_key(url) is None, url


def test_a_confluence_page_is_addressed_by_id_when_the_link_carries_one():
    assert tracker.confluence_target(
        "https://confluence.thy.com/pages/viewpage.action?pageId=884736",
    ) == {"id": "884736"}


def test_a_page_named_rather_than_numbered_is_looked_up_by_space_and_title():
    """`/display/SPACE/Page+Title` has no id in it at all, so it needs a
    different request — resolving it as an id would 404."""
    assert tracker.confluence_target(
        "https://confluence.thy.com/display/QA/Ucus+Arama+Analiz",
    ) == {"space": "QA", "title": "Ucus Arama Analiz"}


def test_the_newer_spaces_url_shape_is_understood_too():
    assert tracker.confluence_target(
        "https://confluence.thy.com/spaces/QA/pages/112233/Analiz",
    ) == {"id": "112233"}


def test_a_link_to_no_page_at_all_is_refused():
    assert tracker.confluence_target("https://confluence.thy.com/") is None


# --- what the page actually says ------------------------------------------- #

def test_storage_format_comes_back_as_prose():
    markup = "<p>Uçuş arama ekranı.</p><p>Tek yön seçilebilmeli.</p>"
    assert tracker.from_storage(markup) == "Uçuş arama ekranı.\nTek yön seçilebilmeli."


def test_a_table_of_criteria_keeps_its_rows():
    """Acceptance criteria live in tables as often as in paragraphs. Stripping
    the tags first would run the whole table into one unbroken line."""
    markup = (
        "<table><tbody>"
        "<tr><th>Senaryo</th><th>Beklenen</th></tr>"
        "<tr><td>Tek yön ara</td><td>Sonuç listelenir</td></tr>"
        "</tbody></table>"
    )
    text = tracker.from_storage(markup)
    assert "Senaryo | Beklenen" in text
    assert "Tek yön ara | Sonuç listelenir" in text


def test_entities_are_turned_back_into_characters():
    assert tracker.from_storage("<p>1 &lt; 2 &amp; 3 &gt; 2</p>") == "1 < 2 & 3 > 2"


def test_scripts_and_styles_are_not_read_as_content():
    markup = "<style>.a{color:red}</style><p>Gerçek metin</p><script>x()</script>"
    assert tracker.from_storage(markup) == "Gerçek metin"


def test_line_breaks_inside_a_paragraph_survive():
    assert tracker.from_storage("<p>bir<br/>iki</p>") == "bir\niki"


# --- the settings ----------------------------------------------------------- #

def test_each_service_is_configured_on_its_own(monkeypatch):
    """A team can have one and not the other, and the half that works should
    keep working."""
    monkeypatch.delenv("CONFLUENCE_TOKEN", raising=False)
    assert tracker.configured("jira")
    assert not tracker.configured("confluence")


@pytest.mark.asyncio
async def test_reading_without_a_token_says_what_is_missing(monkeypatch):
    monkeypatch.delenv("JIRA_TOKEN", raising=False)
    with pytest.raises(tracker.TrackerError, match="Settings"):
        await tracker.fetch("https://jira.thy.com/browse/TKM-1")


@pytest.mark.asyncio
async def test_a_link_to_neither_service_says_so_rather_than_guessing():
    with pytest.raises(tracker.TrackerError, match="neither"):
        await tracker.fetch("https://example.com/page")
