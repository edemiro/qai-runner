"""Reading an analysis document so scenarios can be written from it.

A team's requirements already exist — as an analysis document, a story, a page
of acceptance criteria — and retyping them into a brief is both work and a
chance to leave something out. This turns the file into the text the scenario
writer already takes, and does nothing else: no summarising, no interpreting.
Whatever the model is going to work from, the tester can read first.

Every reader is optional. A missing library costs that one file type, not the
feature, and the two formats teams paste most often — plain text and Markdown —
need no library at all.
"""

import io
import os
import re
from typing import Callable, Dict, Optional, Tuple

# The brief is sent to the model on every generate, so a long document would be
# paid for in tokens each time — and past a point it stops being a brief and
# becomes a document dump the model skims. Cut with a marker rather than
# silently, so a tester can see it happened and trim the file themselves.
MAX_CHARS = 60_000
TRUNCATION_NOTE = "\n\n[… document truncated at {n:,} characters …]"

# Refused before it is read at all. A file this size is not an analysis
# document, and decoding one costs memory that the rest of the server needs.
MAX_BYTES = 20 * 1024 * 1024


class UnreadableDocument(Exception):
    """The file cannot be turned into text, with a reason worth showing."""


def _read_text(data: bytes) -> str:
    """Plain text, in whichever encoding it turned out to be.

    UTF-8 first because that is what everything writes now; cp1254 after it,
    because a Turkish document saved from an older Windows tool is the common
    second case and decoding it as latin-1 would silently mangle ş, ğ and İ.
    """
    for encoding in ("utf-8-sig", "utf-8", "cp1254", "latin-1"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def _read_docx(data: bytes) -> str:
    try:
        import docx  # type: ignore
    except ImportError as exc:  # pragma: no cover - depends on the install
        raise UnreadableDocument(
            "Reading .docx needs the python-docx package: pip install python-docx"
        ) from exc

    document = docx.Document(io.BytesIO(data))
    blocks = [p.text for p in document.paragraphs]
    # Acceptance criteria are written in tables as often as in paragraphs, and
    # a reader that walked only the paragraphs returned a document with its
    # requirements missing — which reads as an empty analysis, not a bug.
    for table in document.tables:
        for row in table.rows:
            cells = [cell.text.strip() for cell in row.cells]
            if any(cells):
                blocks.append(" | ".join(cells))
    return "\n".join(blocks)


def _read_pdf(data: bytes) -> str:
    try:
        from pypdf import PdfReader  # type: ignore
    except ImportError as exc:  # pragma: no cover - depends on the install
        raise UnreadableDocument(
            "Reading .pdf needs the pypdf package: pip install pypdf"
        ) from exc

    reader = PdfReader(io.BytesIO(data))
    return "\n".join((page.extract_text() or "") for page in reader.pages)


READERS: Dict[str, Callable[[bytes], str]] = {
    ".txt": _read_text,
    ".md": _read_text,
    ".markdown": _read_text,
    ".csv": _read_text,
    ".json": _read_text,
    ".docx": _read_docx,
    ".pdf": _read_pdf,
}

SUPPORTED = tuple(sorted(READERS))


def tidy(text: str) -> str:
    """The document as prose, with the shape of the page taken out.

    A .docx or a .pdf arrives full of blank lines, page furniture and trailing
    spaces from the layout. None of that means anything to the model and all of
    it is paid for, so it goes.
    """
    lines = [line.strip() for line in (text or "").replace("\r\n", "\n").split("\n")]
    out = []
    for line in lines:
        if line:
            out.append(line)
        elif out and out[-1] != "":
            out.append("")
    return "\n".join(out).strip()


def extract(filename: str, data: bytes) -> Tuple[str, Optional[str]]:
    """The document's text, and a note about anything that was left out.

    Raises UnreadableDocument with something a tester can act on: which formats
    are supported, or which package is missing to read this one.
    """
    if not data:
        raise UnreadableDocument("That file is empty.")
    if len(data) > MAX_BYTES:
        raise UnreadableDocument(
            f"That file is {len(data) / 1024 / 1024:.0f}MB. "
            f"Analysis documents are expected under {MAX_BYTES // 1024 // 1024}MB."
        )

    suffix = os.path.splitext(filename or "")[1].lower()
    reader = READERS.get(suffix)
    if reader is None:
        raise UnreadableDocument(
            f"QAi cannot read {suffix or 'a file with no extension'}. "
            f"Supported: {', '.join(SUPPORTED)}."
        )

    try:
        text = tidy(reader(data))
    except UnreadableDocument:
        raise
    except Exception as exc:  # noqa: BLE001 - the library's own failure
        raise UnreadableDocument(
            f"{suffix} file could not be read: {exc}"
        ) from exc

    if not text:
        # A scanned PDF is the usual cause: pages of images with no text layer,
        # which extract to nothing at all and would otherwise reach the model
        # as an empty brief it would answer with invented scenarios.
        raise UnreadableDocument(
            "No text came out of that file. If it is a scan, the pages are "
            "images — export it with a text layer, or paste the text in."
        )

    note = None
    if len(text) > MAX_CHARS:
        note = f"Kept the first {MAX_CHARS:,} characters of {len(text):,}."
        text = text[:MAX_CHARS] + TRUNCATION_NOTE.format(n=MAX_CHARS)
    return text, note


def looks_like_a_module(text: str) -> bool:
    """Whether this is a module name rather than a brief.

    The generator takes both in one field — "Uçuş Arama" and three paragraphs of
    acceptance criteria are both valid — and a short line with no sentence in it
    is the first, which is worth saying so the prompt can ask for breadth rather
    than depth.
    """
    stripped = (text or "").strip()
    return bool(stripped) and len(stripped) <= 60 and not re.search(r"[.!?\n]", stripped)
