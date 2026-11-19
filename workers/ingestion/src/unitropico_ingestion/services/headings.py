import re
import unicodedata
from dataclasses import dataclass, replace
from enum import IntEnum


class Rank(IntEnum):
    """Nesting order of headings; lower ranks contain higher ones."""

    PREAMBLE = 0
    TITULO = 10
    CAPITULO = 20
    SECCION = 30
    NUMBERED = 40  # plus depth (1..9) for dotted numbering such as 5.1.2
    ARTICULO = 50
    PARAGRAFO = 60


@dataclass(frozen=True)
class Heading:
    """A detected heading: its rank and the readable label used in sections."""

    rank: int
    label: str
    normative: bool = True
    """False for Markdown or preamble headings, which only apply outside
    normative structure (TÍTULO … PARÁGRAFO or numbered headings)."""
    standalone: bool = False
    """True when the heading line carries no body text of its own."""
    number: str | None = None
    """Dotted number of numbered headings (``5.1.2``), used for nesting."""


_ORDINALS = {
    "PRIMERO": "primero",
    "PRIMER": "primero",
    "SEGUNDO": "segundo",
    "TERCERO": "tercero",
    "TERCER": "tercero",
    "CUARTO": "cuarto",
    "QUINTO": "quinto",
    "SEXTO": "sexto",
    "SEPTIMO": "séptimo",
    "SETIMO": "sétimo",
    "OCTAVO": "octavo",
    "NOVENO": "noveno",
    "DECIMO": "décimo",
    "UNDECIMO": "undécimo",
    "DUODECIMO": "duodécimo",
    "VIGESIMO": "vigésimo",
    "UNICO": "único",
    "TRANSITORIO": "transitorio",
}
_ORDINAL = "|".join(sorted(_ORDINALS, key=len, reverse=True))
_NUMBER = r"\d+(?:\.\d+)*"
_ROMAN = r"[IVXLC]+"
# OCR often turns I into L, 1, | or ! and O into 0 or Q (e.g. "ARliCULO").
_WORDS = {
    "TITULO": (r"T[I1L|!]TUL[O0Q]", Rank.TITULO, "Título"),
    "CAPITULO": (r"C[A4]P[I1L|!]TUL[O0Q]", Rank.CAPITULO, "Capítulo"),
    "SECCION": (r"SECC[I1L|!][O0Q]N", Rank.SECCION, "Sección"),
}
_DIVISION = {
    key: re.compile(
        rf"^{word}\s+(?P<num>{_NUMBER}|{_ROMAN}|(?:{_ORDINAL})(?:\s+(?:{_ORDINAL}))?)"
        r"\s*[°º]?\s*(?:[.:\-–—]|$|\s+[A-Z])"
    )
    for key, (word, _, _) in _WORDS.items()
}
_ARTICULO = re.compile(
    r"^(?:AR[A-Z1|!]{0,2}CUL[O0Q]|ART\.)\s*(?:N\s?[°º.O]\s*)?"
    rf"(?P<num>{_NUMBER}|(?:{_ORDINAL})(?:\s+(?:{_ORDINAL}))?)"
    r"\s*(?:[°º]|O\b)?\s*(?P<rest>[.:\-–—].*|$)"
)
_PARAGRAFO = re.compile(
    r"^P[A4]R[A4]GR[A4]F[O0Q]"
    rf"(?:\s+(?P<num>\d+|(?:{_ORDINAL})(?:\s+(?:{_ORDINAL}))?))?"
    r"\s*[°º]?\s*(?P<rest>[.:\-–—].*|$)"
)
_PREAMBLE = re.compile(r"^(CONSIDERANDO|RESUELVE|VISTOS|ACUERDA|DECRETA)\s*:?$")
_PREAMBLE_LABELS = {
    "CONSIDERANDO": "Considerando",
    "RESUELVE": "Resuelve",
    "VISTOS": "Vistos",
    "ACUERDA": "Acuerda",
    "DECRETA": "Decreta",
}
_MARKDOWN = re.compile(r"^(#{1,6})\s+(.+?)\s*#*$")
_NUMBERED = re.compile(
    r"^(?P<num>\d{1,2}(?:\.\d{1,2}){0,8})(?P<dot>\.?)\s+(?P<title>\S.*)$"
)
_DOT_LEADER = re.compile(r"(?:\.\s?){4,}|…{2,}|_{4,}")
_MAX_HEADING_TITLE = 90
_MAX_LABEL = 80


def fold(text: str) -> str:
    """Uppercase and strip accents and emphasis marks for pattern matching."""
    text = re.sub(r"^[*_]+|[*_]+$", "", text.strip())
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(c for c in decomposed if not unicodedata.combining(c)).upper()


def _label(word: str, number: str) -> str:
    """Render ``Artículo 14`` or ``Artículo primero`` from a matched number."""
    parts = [_ORDINALS.get(part, part) for part in number.split()]
    return f"{word} {' '.join(parts)}"


def _shorten(text: str) -> str:
    """Trim a free-text label to a readable length at a word boundary."""
    text = " ".join(text.replace("*", "").split()).rstrip(" .:")
    if len(text) <= _MAX_LABEL:
        return text
    return text[:_MAX_LABEL].rsplit(" ", 1)[0] + "…"


def detect_heading(line: str, previous_line: str | None = None) -> Heading | None:
    r"""Return the heading starting this line, or None.

    ``previous_line`` is the preceding line of the same paragraph, if any: a
    normative keyword right after a line ending mid-sentence (lowercase letter
    or comma) is a wrapped citation such as "conforme al\nArtículo 45. …".
    """
    stripped = line.strip()
    if (
        not stripped
        or not stripped[0].isalnum()
        and not stripped.startswith(("#", "*"))
    ):
        return None
    markdown = _MARKDOWN.match(stripped)
    body = markdown.group(2) if markdown else stripped
    first_alpha = next((c for c in body.lstrip("*_") if c.isalnum()), "")
    if not first_alpha or first_alpha.islower():
        return None
    folded = fold(body)
    continuation = previous_line is not None and re.search(
        r"[a-záéíóúñ,]$", previous_line
    )
    heading = None if continuation else _normative(folded)
    if heading is None and not continuation:
        heading = _numbered(body)
    if heading is not None:
        standalone = markdown is not None or heading.standalone
        return replace(heading, standalone=standalone)
    preamble = _PREAMBLE.match(folded)
    if preamble:
        return Heading(Rank.PREAMBLE, _PREAMBLE_LABELS[preamble.group(1)], False, True)
    if markdown:
        return Heading(Rank.PREAMBLE, _shorten(body), False, True)
    return None


def _normative(folded: str) -> Heading | None:
    """Detect TÍTULO, CAPÍTULO, SECCIÓN, ARTÍCULO and PARÁGRAFO headings."""
    for key, pattern in _DIVISION.items():
        match = pattern.match(folded)
        if match:
            _, rank, word = _WORDS[key]
            standalone = len(folded) <= len(match.group(0)) + 1
            return Heading(rank, _label(word, match.group("num")), True, standalone)
    match = _ARTICULO.match(folded)
    if match:
        standalone = len(match.group("rest").strip(" .:-–—")) == 0
        return Heading(
            Rank.ARTICULO, _label("Artículo", match.group("num")), True, standalone
        )
    match = _PARAGRAFO.match(folded)
    if match:
        label = (
            _label("Parágrafo", match.group("num"))
            if match.group("num")
            else "Parágrafo"
        )
        standalone = len(match.group("rest").strip(" .:-–—")) == 0
        return Heading(Rank.PARAGRAFO, label, True, standalone)
    return None


def _numbered(body: str) -> Heading | None:
    """Detect short numbered headings like ``5.1.2 Objetivos Específicos``.

    Single-level numbers need a dot and a mostly uppercase title ("1.
    INTRODUCCIÓN") so that ordinary enumerations ("1. Identificación de …")
    stay list items. Table-of-contents lines with dot leaders are ignored.
    """
    match = _NUMBERED.match(body.strip())
    if not match or _DOT_LEADER.search(body):
        return None
    number, title = match.group("num"), match.group("title").strip()
    depth = number.count(".") + 1
    if len(title) > _MAX_HEADING_TITLE or title.endswith((",", ";")):
        return None
    if not title[0].isalpha() or not title[0].isupper():
        return None
    letters = [c for c in title if c.isalpha()]
    if depth == 1:
        upper_ratio = sum(c.isupper() for c in letters) / len(letters)
        if not match.group("dot") or upper_ratio < 0.8 or title.endswith(":"):
            return None
    label = _shorten(f"{number} {title}")
    return Heading(Rank.NUMBERED + min(depth, 9), label, True, True, number)


def is_toc_line(line: str) -> bool:
    """Return whether the line has table-of-contents dot leaders."""
    return bool(_DOT_LEADER.search(line))


def is_structural_heading(line: str) -> bool:
    """Return whether the line starts a normative or numbered heading."""
    heading = detect_heading(line)
    return heading is not None and heading.normative


_LIST_ITEM = re.compile(r"^(?:\d{1,2}[.)]|[a-zñ]\)|[ivx]{1,4}\)|[-•·*▪●◦])(?:\s+\S|$)")


def is_list_item(line: str) -> bool:
    """Return whether the line starts an enumerated or bulleted item."""
    return bool(_LIST_ITEM.match(line.strip()))
