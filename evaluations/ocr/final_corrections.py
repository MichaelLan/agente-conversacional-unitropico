"""Validate the audit corrections on the PREPARED pages with the real ingestion code.

The ingestion ``chunk`` step matches each correction against
``StructuralChunker().prepare(pages)`` (normalized text, repeated headers and
footers removed, HTML tables rendered as Markdown rows), not against the raw
OCR markdown the audits used. This module imports that code read-only from
``workers/ingestion/src`` (explicit ``sys.path`` entry, see ``ingestion()``)
and, per document:

1. ``actual`` = prepared pages of the uncorrected cache (what the Workflow sees).
2. ``expected`` = prepared pages of the raw markdown with the audit corrections
   applied literally where the audits verified them (raw target text, count 1),
   plus the documented ``RAW_ADJUSTMENTS``. This is the intended effect.
3. Corrections are tried in audit order with the real ``count_matches`` rule;
   the ones matching exactly once are kept verbatim and applied.
4. The remaining difference between the corrected ``actual`` and ``expected``
   (ignoring blank-line runs and lines the header/footer rule removes in either
   version) is turned into new literal corrections, each matching exactly once
   in the text left by the previous ones and within the API limits
   (find 1..500, replace 0..500). They are attributed to ALL the failing
   audit corrections of that page (``reformulated_from``; the diff is per
   page, not per correction).
5. A failing correction whose page shows no remaining difference had no
   visible effect (its text never reaches the prepared page, e.g. a running
   header removed by the boilerplate rule) and is recorded as ``omitted``.

Finally every document is re-checked in one pass with the real
``apply_corrections`` (all seeds, creation order) and ``chunk_prepared``.
Errors carry only labels, document slugs and page numbers.
"""

import difflib
import re
import sys
from collections.abc import Sequence
from dataclasses import dataclass, field

sys.dont_write_bytecode = True
from final_sources import REPO, Correction, Document, require

INGESTION_SRC = REPO / "workers/ingestion/src"
MAX_FIND = 500
MAX_REPLACE = 500
MAX_ACTIVE_PER_VERSION = 100
CONTEXT_TOKENS = 3
MERGE_GAP = 4
MAX_STEPS = 400
RAW_ADJUSTMENTS = {
    # The ingestion table renderer drops <caption> text, so a corrected caption
    # (audit: "línea", caption moved onto the amounts table) would vanish from
    # the prepared page. The intended effect is kept as a plain title line just
    # above the table. Revisit if services/tables.py starts rendering captions.
    ("manual-m-ip-36", 47): (
        "<table>\n<caption>Tabla 11. Montos de las TMC por línea de intervención "
        "y condicionalidad académica</caption>\n",
        "Tabla 11. Montos de las TMC por línea de intervención y condicionalidad "
        "académica\n\n<table>\n",
    ),
    ("manual-m-ip-36", 67): (
        "<table>\n<caption>Tabla 17. Montos de las TMC por línea de intervención "
        "y condicionalidad académica para el régimen de transición</caption>\n",
        "Tabla 17. Montos de las TMC por línea de intervención y condicionalidad "
        "académica para el régimen de transición\n\n<table>\n",
    ),
}


def ingestion():
    """Import the real ingestion modules (read-only) from workers/ingestion/src.

    The Worker package is not installed in this project; its source tree is
    put first on ``sys.path`` explicitly so the exact code deployed is used.
    """
    if str(INGESTION_SRC) not in sys.path:
        sys.path.insert(0, str(INGESTION_SRC))
    from unitropico_ingestion.domain.chunking import PageText
    from unitropico_ingestion.domain.corrections import TextCorrection
    from unitropico_ingestion.services import corrections, ocr_parser, preprocess
    from unitropico_ingestion.services.chunker import StructuralChunker

    return {
        "PageText": PageText,
        "TextCorrection": TextCorrection,
        "count_matches": corrections.count_matches,
        "apply_corrections": corrections.apply_corrections,
        "parse_ocr_response": ocr_parser.parse_ocr_response,
        "boilerplate_key": preprocess.boilerplate_key,
        "normalize_pages": preprocess.normalize_pages,
        "render_html_tables": preprocess.render_html_tables,
        "chunker": StructuralChunker(),
    }


@dataclass
class Seed:
    """One correction to register through the admin API, in creation order."""

    document: str
    page: int
    find_text: str
    replace_text: str
    reason: str
    origin: str
    status: str  # verbatim | reformulated
    reformulated_from: list[int] = field(default_factory=list)


@dataclass
class Outcome:
    """Per-correction validation result (index = position in the audit list)."""

    index: int
    status: str  # verbatim | reformulated | omitted
    matches: int
    note: str = ""


def tidy(text: str) -> str:
    """Comparison form: horizontal spaces collapsed per line, blank runs collapsed.

    The real ``_replace`` collapses horizontal spaces on the lines a correction
    touches (an empty table cell ``|  |`` becomes ``| |``) and ``_tidy``
    collapses blank-line runs; the chunker parses both forms identically.
    """
    lines = [re.sub(r"[^\S\n]+", " ", line).strip() for line in text.split("\n")]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def prepared_pages(raw_pages: dict[int, str], api: dict) -> dict[int, str]:
    """``StructuralChunker().prepare`` of raw markdown pages (number -> text)."""
    page_text = api["PageText"]
    pages = [page_text(n, t) for n, t in sorted(raw_pages.items())]
    return {p.page_number: p.text for p in api["chunker"].prepare(pages)}


def raw_expected(doc: Document, corrections: Sequence[Correction]) -> dict[int, str]:
    """Raw pages with the audit corrections applied where the audits verified them."""
    pages = {p.number: p.markdown for p in doc.pages}
    for c in corrections:
        require(pages[c.page].count(c.find_text) == 1, "Raw correction not unique")
        pages[c.page] = pages[c.page].replace(c.find_text, c.replace_text, 1)
    for (slug, number), (old, new) in RAW_ADJUSTMENTS.items():
        if slug == doc.slug:
            require(pages[number].count(old) == 1, "Raw adjustment not unique")
            pages[number] = pages[number].replace(old, new, 1)
    return pages


def apply_one(text: str, number: int, find: str, replace: str, api: dict) -> str:
    """Apply one correction to one prepared page with the real code."""
    page_text = api["PageText"]
    correction = api["TextCorrection"]("seed", number, find, replace)
    result, _ = api["apply_corrections"]([page_text(number, text)], [correction])
    return result[0].text if result else ""


def boilerplate_keys(raw_versions: Sequence[dict[int, str]], api: dict) -> set[str]:
    """Keys of lines repeated enough to be removed by the header/footer rule.

    Counted on the normalized, NOT yet stripped pages of each raw version (same
    thresholds as ``preprocess.strip_repeated_lines``).
    """
    page_text = api["PageText"]
    keys: set[str] = set()
    for raw in raw_versions:
        pages = api["normalize_pages"]([page_text(n, t) for n, t in raw.items()])
        version = {p.page_number: p.text for p in pages}
        counts: dict[str, int] = {}
        for text in version.values():
            for key in {api["boilerplate_key"](line) for line in text.split("\n")}:
                counts[key] = counts.get(key, 0) + 1
        threshold = max(3, 0.25 * len(version))
        keys |= {
            k
            for k, n in counts.items()
            if n >= threshold and sum(c.isalpha() for c in k) >= 4
        }
    return keys


def target_text(current: str, expected: str, ignore: set[str], api: dict) -> str:
    """``current`` with every non-boilerplate line hunk of ``expected`` adopted."""
    a, b = tidy(current).split("\n"), tidy(expected).split("\n")
    out: list[str] = []
    matcher = difflib.SequenceMatcher(None, a, b, autojunk=False)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            out.extend(a[i1:i2])
            continue
        changed = [*a[i1:i2], *b[j1:j2]]
        if all(
            not line.strip() or api["boilerplate_key"](line) in ignore
            for line in changed
        ):
            out.extend(a[i1:i2])  # header/footer rule difference: not a correction
        else:
            out.extend(b[j1:j2])
    return tidy("\n".join(out))


def tokens(text: str) -> list[tuple[str, int, int]]:
    """Non-whitespace tokens with their spans."""
    return [(m.group(0), m.start(), m.end()) for m in re.finditer(r"\S+", text)]


def next_edit(current: str, target: str, number: int, api: dict) -> tuple[str, str]:
    """Smallest unique literal edit moving ``current`` toward ``target``."""
    a, b = tokens(current), tokens(target)
    matcher = difflib.SequenceMatcher(
        None, [t[0] for t in a], [t[0] for t in b], autojunk=False
    )
    ops = [op for op in matcher.get_opcodes() if op[0] != "equal"]
    if not ops:  # same tokens, different line breaks (e.g. merged table rows)
        require(len(a) == len(b), "Token mismatch")
        k = next(
            (
                k
                for k in range(len(a) - 1)
                if tidy(current[a[k][2] : a[k + 1][1]] + "x")
                != tidy(target[b[k][2] : b[k + 1][1]] + "x")
            ),
            None,
        )
        require(k is not None, "Whitespace difference at page edge")
        ops = [("replace", k, k + 2, k, k + 2)]
    _, i1, i2, j1, j2 = ops[0]
    for _, k1, k2, _l1, l2 in ops[1:]:  # merge nearby hunks into one edit
        if k1 - i2 > MERGE_GAP:
            break
        i2, j2 = k2, l2
    for left in range(min(CONTEXT_TOKENS, i1), i1 + 1):
        for right in range(CONTEXT_TOKENS, len(a) - i2 + 1):
            right = min(right, len(a) - i2)
            s, e = i1 - left, i2 + right
            ts, te = j1 - left, j2 + right
            if s == e:  # pure insertion at document start/end: need context
                continue
            find = current[a[s][1] : a[e - 1][2]]
            if ts < te:
                replace = target[b[ts][1] : b[te - 1][2]]
            else:
                replace = ""
            if len(find.strip()) > MAX_FIND or len(replace) > MAX_REPLACE:
                return shrink(current, target, (a, b), (i1, i2, j1, j2), number, api)
            if api["count_matches"](current, find) == 1:
                return find, replace
            if i2 + right >= len(a):
                break
    raise ValueError("No unique context for edit")


def shrink(current, target, toks, op, number, api) -> tuple[str, str]:
    """Split an edit above the API limits: replace a bounded prefix first."""
    a, b = toks
    i1, i2, j1, j2 = op
    budget = MAX_FIND - 120  # room for context
    n = i1
    while n < i2 and a[n][2] - a[i1][1] < budget:
        n += 1
    m = j1
    while m < j2 and b[m][2] - b[j1][1] < budget:
        m += 1
    n = max(n, i1 + 1) if i2 > i1 else i1
    sub_current = current
    left = min(CONTEXT_TOKENS, i1)
    for right in range(1, len(a) - n + 1):
        s, e = i1 - left, n + right
        find = sub_current[a[s][1] : a[e - 1][2]]
        head = target[b[j1 - left][1] : b[m - 1][2]] if m > j1 - left else ""
        if j1 - left < j1 and m == j1:
            head = target[b[j1 - left][1] : b[j1 - 1][2]]
        tail = current[a[n][1] : a[e - 1][2]] if e > n else ""
        sep = " " if head and tail else ""
        replace = f"{head}{sep}{tail}"
        if len(find.strip()) > MAX_FIND or len(replace) > MAX_REPLACE:
            break
        if api["count_matches"](current, find) == 1:
            return find, replace
    raise ValueError("Edit cannot be split within limits")


def reformulate(
    current: str, target: str, number: int, api: dict
) -> list[tuple[str, str]]:
    """Literal edits turning ``current`` into ``target`` (tidy-equal)."""
    edits: list[tuple[str, str]] = []
    for _ in range(MAX_STEPS):
        if tidy(current) == tidy(target):
            return edits
        find, replace = next_edit(current, target, number, api)
        require(find.strip() != "" and find != replace, "Degenerate edit")
        current = apply_one(current, number, find, replace, api)
        edits.append((find, replace))
    raise ValueError("Reformulation did not converge")


def validate_document(
    doc: Document, corrections: Sequence[tuple[int, Correction]], api: dict
) -> tuple[list[Seed], list[Outcome], dict[int, str]]:
    """Seeds (creation order), per-correction outcomes and the corrected pages."""
    raw = {p.number: p.markdown for p in doc.pages}
    fixed = raw_expected(doc, [c for _, c in corrections])
    actual = prepared_pages(raw, api)
    expected = prepared_pages(fixed, api)
    ignore = boilerplate_keys([raw, fixed], api)
    current = dict(actual)
    seeds: list[Seed] = []
    outcomes: list[Outcome] = []
    failing: dict[int, list[int]] = {}
    for index, c in corrections:
        text = current.get(c.page, "")
        matches = api["count_matches"](text, c.find_text)
        if matches == 1:
            current[c.page] = apply_one(text, c.page, c.find_text, c.replace_text, api)
            seeds.append(
                Seed(
                    doc.slug,
                    c.page,
                    c.find_text,
                    c.replace_text,
                    c.reason,
                    c.origin,
                    "verbatim",
                )
            )
            outcomes.append(Outcome(index, "verbatim", 1))
        else:
            failing.setdefault(c.page, []).append(index)
            outcomes.append(Outcome(index, "pending", matches))
    by_index = dict(corrections)
    for number in sorted(set(raw)):
        cur, exp = current.get(number, ""), expected.get(number, "")
        # A verbatim replacement may insert an HTML table; the chunker parses it
        # exactly like a rendered one, so compare the rendered form.
        rendered = api["render_html_tables"](cur)
        if tidy(rendered) == target_text(rendered, exp, ignore, api):
            continue
        require(rendered == cur, f"HTML table in reformulated page {doc.slug}")
        target = target_text(cur, exp, ignore, api)
        sources = failing.get(number)
        require(bool(sources), f"Unexplained difference {doc.slug} p{number}")
        edits = reformulate(cur, target, number, api)
        reasons = "; ".join(dict.fromkeys(by_index[i].reason for i in sources))
        origins = "; ".join(dict.fromkeys(by_index[i].origin for i in sources))
        for k, (find, replace) in enumerate(edits, 1):
            reason = (
                f"Reformulada sobre el texto preparado ({k}/{len(edits)}): {reasons}"
            )
            seeds.append(
                Seed(
                    doc.slug,
                    number,
                    find,
                    replace,
                    reason[:300],
                    origins,
                    "reformulated",
                    list(sources),
                )
            )
        current[number] = apply_one_all(cur, number, edits, api)
        for outcome in outcomes:
            if outcome.index in sources:
                outcome.status = "reformulated"
                outcome.note = f"{len(edits)} corrección(es) derivada(s)"
    for outcome in outcomes:
        if outcome.status == "pending":
            outcome.status = "omitted"
            outcome.note = (
                "sin efecto visible: el texto no llega a la página preparada "
                "(p. ej. cabecera repetida eliminada) y el resultado esperado "
                "no difiere"
            )
    return seeds, outcomes, current


def apply_one_all(text: str, number: int, edits, api: dict) -> str:
    """Apply a list of edits to one page, one by one."""
    for find, replace in edits:
        text = apply_one(text, number, find, replace, api)
    return text


def check_seeds(seed_list: Sequence[Seed], api: dict) -> None:
    """API limits, Cf/Cc characters and (page, find_text) uniqueness."""
    import unicodedata

    seen: set[tuple[str, int, str]] = set()
    counts: dict[str, int] = {}
    for s in seed_list:
        require(1 <= len(s.find_text.strip()) <= MAX_FIND, "find_text length")
        require(len(s.replace_text) <= MAX_REPLACE, "replace_text length")
        require(1 <= len(s.reason.strip()) <= 300, "reason length")
        require(s.find_text != s.replace_text, "find equals replace")
        for value in (s.find_text, s.replace_text, s.reason):
            for ch in value:
                cat = unicodedata.category(ch)
                require(cat != "Cf", "Cf character")
                require(cat != "Cc" or ch in "\n\t", "Cc character")
        key = (s.document, s.page, s.find_text)
        require(key not in seen, "Duplicate (page, find_text)")
        seen.add(key)
        counts[s.document] = counts.get(s.document, 0) + 1
    require(all(n <= MAX_ACTIVE_PER_VERSION for n in counts.values()), "Too many seeds")


def final_check(
    doc: Document,
    seeds: Sequence[Seed],
    raw_cache: bytes,
    api: dict,
    override=None,
    expected_pages: dict[int, str] | None = None,
) -> dict:
    """One pass like the chunk step: parse → prepare → (override) → apply → chunk."""
    result = api["parse_ocr_response"](raw_cache)
    page_text = api["PageText"]
    pages = [page_text(p.page_number, p.markdown) for p in result.pages]
    chunker = api["chunker"]
    prepared = chunker.prepare(pages)
    if override is not None:
        number, text = override
        override_prepared = chunker.prepare([page_text(number, text)])
        texts = {p.page_number: p.text for p in prepared}
        texts[number] = override_prepared[0].text if override_prepared else ""
        prepared = [page_text(n, t) for n, t in sorted(texts.items()) if t]
    corrections = [
        api["TextCorrection"](f"seed-{i}", s.page, s.find_text, s.replace_text)
        for i, s in enumerate(seeds)
        if s.document == doc.slug
    ]
    corrected, applied = api["apply_corrections"](prepared, corrections)
    require(len(applied) == len(corrections), "Not all corrections applied")
    texts = {p.page_number: p.text for p in corrected}
    mine = [s for s in seeds if s.document == doc.slug]
    for k, s in enumerate(mine):
        visible = " ".join(s.replace_text.split())
        superseded = any(t.page == s.page for t in mine[k + 1 :])
        if visible and not superseded:  # a later edit of the page may rewrite it
            require(
                visible in " ".join(texts.get(s.page, "").split()),
                "Corrected text missing",
            )
    for number, text in (expected_pages or {}).items():
        require(tidy(texts.get(number, "")) == tidy(text), "Corrected page differs")
    chunks = chunker.chunk_prepared(corrected, version_id=f"check-{doc.slug}")
    require(len(chunks) > 0, "No chunks")
    return {
        "pages": result.page_count,
        "text_pages": len(prepared),
        "corrections_applied": len(applied),
        "chunks": len(chunks),
        "corrected_pages": {p.page_number: p.text for p in corrected},
    }
