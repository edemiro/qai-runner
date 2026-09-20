"""Turning an analysis document into the brief scenarios are written from.

The value is in what reaches the model, so most of this is about what must not:
an empty extraction that would be answered with invented scenarios, a document
long enough to be paid for on every generate, and the page furniture that a
.docx or a .pdf carries and the scenarios have no use for.
"""

import io
import zipfile

import pytest

import documents


def test_plain_text_comes_back_as_it_was_written():
    text, note = documents.extract("brief.txt", "Uçuş arama ekranı.".encode("utf-8"))
    assert text == "Uçuş arama ekranı."
    assert note is None


def test_a_turkish_document_from_an_older_windows_tool_is_not_mangled():
    """cp1254 is what those files are, and reading them as latin-1 turns ş, ğ
    and İ into something else without failing — so the brief would be subtly
    wrong rather than obviously broken."""
    text, _ = documents.extract("brief.txt", "Uçuş Arama İşlemi".encode("cp1254"))
    assert text == "Uçuş Arama İşlemi"


def test_markdown_and_csv_need_no_library_at_all():
    for name in ("notes.md", "rows.csv", "payload.json"):
        text, _ = documents.extract(name, b"a line")
        assert text == "a line", name


def test_the_layout_of_the_page_is_taken_out():
    """A .docx or .pdf arrives full of blank lines and trailing spaces from the
    layout. None of it means anything to the model and all of it is paid for."""
    raw = "Title   \n\n\n\n  Body  \n\n\n"
    assert documents.tidy(raw) == "Title\n\nBody"


def _docx(paragraphs, table=None):
    """A minimal .docx, built rather than fixtured so the test carries what it
    is about — python-docx reads this the same as Word's own output."""
    docx = pytest.importorskip("docx")
    document = docx.Document()
    for line in paragraphs:
        document.add_paragraph(line)
    if table:
        grid = document.add_table(rows=len(table), cols=len(table[0]))
        for r, row in enumerate(table):
            for c, cell in enumerate(row):
                grid.cell(r, c).text = cell
    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def test_a_word_document_is_read():
    text, _ = documents.extract("analiz.docx", _docx(["Uçuş Arama", "Tek yön seçilir."]))
    assert "Uçuş Arama" in text
    assert "Tek yön seçilir." in text


def test_requirements_written_in_a_table_are_not_lost():
    """Acceptance criteria live in tables as often as in paragraphs, and a
    reader that walked only the paragraphs returned a document with its
    requirements missing — which reads as an empty analysis, not as a bug."""
    data = _docx(["Başlık"], table=[["Senaryo", "Beklenen"],
                                    ["Tek yön ara", "Sonuç listelenir"]])
    text, _ = documents.extract("analiz.docx", data)
    assert "Sonuç listelenir" in text
    assert "Senaryo | Beklenen" in text


def test_a_file_type_nobody_can_read_says_which_ones_are_readable():
    with pytest.raises(documents.UnreadableDocument) as exc:
        documents.extract("analiz.pages", b"data")
    assert ".docx" in str(exc.value) and ".pdf" in str(exc.value)


def test_a_file_with_no_extension_is_refused_by_name():
    with pytest.raises(documents.UnreadableDocument):
        documents.extract("analiz", b"data")


def test_an_empty_file_is_refused_before_anything_else():
    with pytest.raises(documents.UnreadableDocument, match="empty"):
        documents.extract("brief.txt", b"")


def test_a_document_with_no_text_in_it_is_refused_rather_than_sent_on():
    """A scan is pages of images. Extracted it is nothing at all, and an empty
    brief is answered with invented scenarios — the worst outcome available."""
    with pytest.raises(documents.UnreadableDocument, match="scan"):
        documents.extract("scan.txt", b"   \n\n   \n")


def test_a_file_too_large_to_be_an_analysis_document_is_refused_unread():
    with pytest.raises(documents.UnreadableDocument, match="MB"):
        documents.extract("huge.txt", b"x" * (documents.MAX_BYTES + 1))


def test_a_long_document_is_cut_and_says_so():
    """The brief is sent on every generate, so a document dump is paid for
    every time. Cutting silently would leave a tester wondering why the last
    third of their criteria produced no scenarios."""
    text, note = documents.extract("long.txt", b"a" * (documents.MAX_CHARS + 5_000))
    assert "truncated" in text
    assert note and "60,000" in note


def test_a_broken_file_of_a_known_type_explains_itself():
    bad = zipfile.ZipFile(io.BytesIO(), "w")  # noqa: SIM115 - never written
    bad.close()
    with pytest.raises(documents.UnreadableDocument, match=r"\.docx"):
        documents.extract("broken.docx", b"not a zip at all")


def test_a_module_name_is_told_apart_from_a_brief():
    """The generator takes both in one field, and knowing which it was given
    is what lets the prompt ask for breadth rather than depth."""
    assert documents.looks_like_a_module("Uçuş Arama")
    assert documents.looks_like_a_module("Check-in")
    assert not documents.looks_like_a_module("Uçuş arama ekranını test et.")
    assert not documents.looks_like_a_module("Bir satır\nbir satır daha")
    assert not documents.looks_like_a_module("")
    assert not documents.looks_like_a_module("x" * 61)
