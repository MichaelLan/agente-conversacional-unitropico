"""Literal quote matching between a model citation and its source chunk.

A quote is accepted only if, after a *canonical folding* applied identically to
the quote and to the chunk text, it is a **contiguous substring** of the chunk
and still carries at least ``MIN_QUOTE_CHARS`` visible characters. There is no
similarity score: every accepted quote is a real span of the chunk.

Canonical folding (per character, so every folded character maps back to its
position in the original text):

1. NFKD decomposition and ``casefold`` (ligatures, full-width forms, ``…`` →
   ``...``, non-breaking spaces → space; case-insensitive).
2. Combining marks are dropped (accent-insensitive: OCR often loses accents and
   models often restore them). Side effect, shared with the FTS5 tokenizer:
   ``ñ`` folds to ``n``.
3. Curly/typographic quotes become ``'`` or ``"``; every dash/hyphen/minus
   variant becomes ``-``.
4. Invisible format characters (soft hyphen, zero-width spaces/joiners, BOM)
   and Markdown emphasis/code marks (``*``, `````) are dropped.
5. Markdown table pipes (``|``) count as whitespace; every whitespace run
   collapses to one space.

Only the *quote* is then trimmed of leading/trailing whitespace, punctuation and
ellipses (``"...requisitos del apoyo."`` → ``requisitos del apoyo``); an
ellipsis in the middle still fails because the span must be contiguous.

``match_quote`` returns the **matching span of the chunk text** (whitespace
collapsed, invisible characters removed), not the model's string: the citation
quote is shown to students as evidence, and after lenient matching the model's
spelling (accents, case, quote marks) may differ from the source. Returning the
source span keeps the contract "quote is literal text of the cited chunk".
"""

import unicodedata

# A quote must carry at least this many non-whitespace characters, so that
# trivially short strings ("de", "2026") cannot pass as literal evidence.
MIN_QUOTE_CHARS = 12

_SINGLE_QUOTES = "‘’‚‛′‵"
_DOUBLE_QUOTES = "“”„‟″‶«»"
_DASHES = "‐‑‒–—―−⁃﹘﹣－"
_REPLACE = (
    {ord(ch): "'" for ch in _SINGLE_QUOTES}
    | {ord(ch): '"' for ch in _DOUBLE_QUOTES}
    | {ord(ch): "-" for ch in _DASHES}
)
# Invisible format characters: removed from matching and from returned quotes.
_INVISIBLE = frozenset("­​‌‍⁠﻿")
# Markdown marks ignored when matching (kept in the returned source span).
_IGNORED = _INVISIBLE | frozenset("*`")
_SPACE_LIKE = frozenset("|")
# Trimmed from the ends of the folded quote only.
_EDGE = " .,;:!?¡¿\"'()[]{}-"


def _fold_char(char: str) -> str:
    """Fold one character to its canonical form (possibly empty or several)."""
    folded = unicodedata.normalize(
        "NFKD", unicodedata.normalize("NFKD", char).casefold()
    )
    return "".join(
        c for c in folded if not unicodedata.combining(c) and c not in _IGNORED
    ).translate(_REPLACE)


def canonicalize(text: str) -> tuple[str, list[int]]:
    """Return the folded text and, per folded character, its source index."""
    out: list[str] = []
    origin: list[int] = []
    pending_space = False
    for index, char in enumerate(text):
        for folded in _fold_char(char):
            if folded.isspace() or folded in _SPACE_LIKE:
                pending_space = bool(out)
                continue
            if pending_space:
                out.append(" ")
                origin.append(index)
                pending_space = False
            out.append(folded)
            origin.append(index)
    return "".join(out), origin


def _clean_span(span: str) -> str:
    """Collapse whitespace and drop invisible characters of a source span."""
    return " ".join("".join(c for c in span if c not in _INVISIBLE).split())


def match_quote(quote: str, text: str) -> str | None:
    """Return the chunk span matching ``quote``, or None if it is not literal.

    None when the folded quote has fewer than ``MIN_QUOTE_CHARS`` visible
    characters or is not a contiguous substring of the folded chunk text.
    """
    if not isinstance(quote, str) or not isinstance(text, str):
        return None
    needle = canonicalize(quote)[0].strip(_EDGE)
    if len(needle.replace(" ", "")) < MIN_QUOTE_CHARS:
        return None
    haystack, origin = canonicalize(text)
    position = haystack.find(needle)
    if position < 0:
        return None
    start = origin[position]
    end = origin[position + len(needle) - 1] + 1
    return _clean_span(text[start:end]) or None


def quote_matches(quote: str, text: str) -> bool:
    """Return whether ``quote`` is accepted as literal evidence from ``text``."""
    return match_quote(quote, text) is not None
