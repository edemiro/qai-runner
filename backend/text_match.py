"""Does the screen say this? — answered the way a person reading it would.

Every assertion in the product ends up here, and it exists because the obvious
answer is wrong in a way that stays invisible until you count the failures.
Three of them, all measured against the real product rather than imagined:

**Turkish case.** `"GİDİŞ".lower()` is not `"gidiş"`. Python lowercases the
dotted capital İ (U+0130) to `i` plus a combining dot above, and the dotless ı
is a different letter from i at every case. So a scenario that writes "Gidiş"
against a heading the site renders as "GİDİŞ" — which is most headings on a
Turkish site — fails an assertion about a word plainly on the screen. Silent,
constant, and it looks like a product bug every single time.

**Typography.** The site writes "İstanbul'da" with a right single quote
(U+2019); a tester writes it with an apostrophe. Same word on the screen, no
match. The same goes for en dashes, ellipses and non-breaking hyphens.

**Invisible characters.** Zero-width spaces, soft hyphens and bidi marks sit
inside rendered text and are not whitespace, so collapsing whitespace does not
remove them and the phrase they split never matches.

What this deliberately does NOT do is fuzzy matching. An assertion that passes
for a phrase the screen does not say is worse than one that fails for a phrase
it does: the first gets trusted. Everything here is a normalisation both sides
get equally — it forgives how the text was encoded and cased, never what it
says.

Written with escapes rather than the characters themselves because most of them
are invisible or look identical to their plain form in an editor, and a table
of characters nobody can read is a table nobody can check.
"""
from __future__ import annotations

import unicodedata
from typing import Iterable, List, Optional

# Rendered as nothing, so they must not split a phrase.
INVISIBLE = frozenset([
    "​",  # zero width space
    "‌",  # zero width non-joiner
    "‍",  # zero width joiner
    "‎",  # left-to-right mark
    "‏",  # right-to-left mark
    "­",  # soft hyphen
    "﻿",  # zero width no-break space
    "⁠",  # word joiner
])

# Typographic forms folded to the plain character a tester types. Explicit
# rather than derived: NFKC leaves the quotes and dashes alone, which is the
# half of the problem it does not solve.
PUNCTUATION = {
    "‘": "'", "’": "'", "‚": "'", "‛": "'",
    "ʼ": "'", "′": "'", "´": "'", "`": "'",
    "“": '"', "”": '"', "„": '"', "″": '"',
    "«": '"', "»": '"',
    "‐": "-", "‑": "-", "‒": "-", "–": "-",
    "—": "-", "―": "-", "−": "-",
    "…": "...",
}

# The Turkish i family, all folded to one letter before lowercasing. That order
# matters: Python's lower() maps I to i and leaves ı alone, so "IST" and "ıst"
# drift apart exactly where an airport code lives.
DOTTED = {
    "İ": "i",  # İ  dotted capital
    "ı": "i",  # ı  dotless small
    "I": "i",
    "i": "i",
}


def normalise(text: Optional[str]) -> str:
    """One string, reduced to what the eye reads off the screen.

    Lowercased, whitespace collapsed, invisible characters gone, typographic
    punctuation folded, every Turkish i the same letter, and accents dropped.

    That last one is the only judgement call here, so it is worth naming: it
    makes "Uçuş" and "Ucus" the same phrase. A tester who types the second
    while the screen shows the first has not found a defect, and nobody
    reading the report would call that a failed check. It does mean two words
    that differ only by an accent — "kar" and "kâr" — stop being distinct, and
    that is accepted deliberately: no assertion in this product turns on it,
    and the failures that come from the other choice are constant.
    """
    if not text:
        return ""
    # NFKC first: it settles ligatures, full-width forms and the composed vs
    # decomposed spelling of every accented letter, so the rest works on one
    # shape rather than several.
    out = unicodedata.normalize("NFKC", str(text))
    out = "".join(
        "" if ch in INVISIBLE else PUNCTUATION.get(ch, ch)
        for ch in out
    )
    out = "".join(DOTTED.get(ch, ch) for ch in out)
    out = out.lower()
    # Decomposed, the accents become characters of their own and drop out —
    # which also takes the combining dot that lowercasing a dotted capital
    # leaves behind.
    out = "".join(
        ch for ch in unicodedata.normalize("NFD", out)
        if not unicodedata.combining(ch)
    )
    return " ".join(out.split())


def contains(haystack: Optional[str], needle: Optional[str]) -> bool:
    """Is this phrase in this text, read as a person reads it?"""
    wanted = normalise(needle)
    if not wanted:
        return False
    return wanted in normalise(haystack)


def contains_any(pieces: Iterable[Optional[str]], needle: Optional[str]) -> bool:
    """Is the phrase in any one of these strings?"""
    wanted = normalise(needle)
    if not wanted:
        return False
    return any(wanted in normalise(piece) for piece in pieces)


def joined(pieces: Iterable[Optional[str]]) -> str:
    """Several strings read as one running line, normalised.

    A phrase a tester writes down is a phrase they read off the screen, and the
    screen does not show them where one element stops: Turkish Airlines renders
    an airport as two siblings, "İstanbul Havalimanı" and "(IST)". Joined with a
    space so the phrase matches however the markup split it — whitespace inside
    the phrase is still required, so this forgives the split, not the words.
    """
    out: List[str] = []
    for piece in pieces:
        cleaned = normalise(piece)
        if cleaned and cleaned != (out[-1] if out else None):
            out.append(cleaned)
    return " ".join(out)


def looks_like(a: Optional[str], b: Optional[str]) -> bool:
    """Are these two strings the same thing said the same way?"""
    left = normalise(a)
    return bool(left) and left == normalise(b)
