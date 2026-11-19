import re
from collections.abc import Sequence
from dataclasses import dataclass, replace

from unitropico_ingestion.domain.chunking import Chunk, PageText, build_chunk
from unitropico_ingestion.services.headings import (
    Heading,
    Rank,
    detect_heading,
    is_list_item,
    is_toc_line,
)
from unitropico_ingestion.services.preprocess import prepare_pages
from unitropico_ingestion.services.tables import (
    HTML_TABLE,
    Row,
    Table,
    is_table_line,
    parse_html_table,
    parse_markdown_table,
)

STRUCTURAL_CHUNKER_VERSION = "structural-v1"
_SEPARATOR = "\n\n"
_SENTENCE_END = re.compile(r"[.;!?»”\")]\s")
_ENDS_SENTENCE = re.compile(r"[.;:!?»”\")]$")
_TOC_MIN_LINES = 5
_MAX_WRAPPED_TITLE = 80
_DANGLING_WORD = re.compile(
    r"\b(?:A|AL|CON|DE|DEL|E|EL|EN|LA|LAS|LOS|O|PARA|POR|U|Y)$", re.IGNORECASE
)


@dataclass(frozen=True)
class _Segment:
    """A paragraph, list item, heading or table parsed from page text."""

    page_start: int
    page_end: int
    text: str = ""
    heading: Heading | None = None
    heading_only: bool = False
    list_item: bool = False
    table: Table | None = None


@dataclass(frozen=True)
class _Piece:
    """A packable unit with its heading path and page range."""

    text: str
    page_start: int
    page_end: int
    path: tuple[Heading, ...]
    heading_only: bool = False
    boundary: bool = False
    table: Table | None = None


class StructuralChunker:
    """Deterministic chunker that follows normative structure and tables.

    Major headings (TÍTULO, CAPÍTULO, SECCIÓN, numbered headings, ARTÍCULO and
    Markdown/preamble headings outside normative structure) always start a new
    chunk; standalone heading lines attach to the content that follows them.
    PARÁGRAFO, list items and paragraphs are split points used only when a
    section exceeds ``max_chars``. Tables are atomic unless they exceed
    ``max_chars``; then they are split by rows repeating the header.
    """

    def __init__(
        self,
        *,
        max_chars: int = 1800,
        min_chars: int = 200,
        overlap_chars: int = 0,
        strip_boilerplate: bool = True,
    ) -> None:
        """Validate size parameters (characters, not tokens)."""
        if not 0 < min_chars < max_chars:
            raise ValueError("require 0 < min_chars < max_chars")
        if not 0 <= overlap_chars <= max_chars // 4:
            raise ValueError("require 0 <= overlap_chars <= max_chars // 4")
        self.max_chars = max_chars
        self.min_chars = min_chars
        self.overlap_chars = overlap_chars
        self.strip_boilerplate = strip_boilerplate
        # Overlap is prepended with one space, so it is reserved up front.
        self._capacity = max_chars - (overlap_chars + 1 if overlap_chars else 0)

    @property
    def version(self) -> str:
        """Return the constant identifying these chunking rules."""
        return STRUCTURAL_CHUNKER_VERSION

    def chunk(self, pages: Sequence[PageText], *, version_id: str) -> list[Chunk]:
        """Split one document version into ordered structural chunks."""
        prepared = prepare_pages(pages, strip_boilerplate=self.strip_boilerplate)
        segments = _join_across_pages([_parse_page(page) for page in prepared])
        drafts = self._merge_small(self._pack(_assign_paths(segments)))
        rendered = self._with_overlap(drafts)
        return [
            build_chunk(
                version_id=version_id,
                chunker_version=self.version,
                ordinal=ordinal,
                text=text,
                section=_section(draft),
                page_start=page_start,
                page_end=max(p.page_end for p in draft),
            )
            for ordinal, (draft, (text, page_start)) in enumerate(
                zip(drafts, rendered, strict=True)
            )
        ]

    def _pack(self, pieces: list[_Piece]) -> list[list[_Piece]]:
        """Greedily group pieces into drafts that respect boundaries and size."""
        drafts: list[list[_Piece]] = []
        current: list[_Piece] = []

        def flush() -> None:
            if current:
                drafts.append(current.copy())
                current.clear()

        def add(piece: _Piece) -> None:
            if (
                current
                and _length(current) + len(_SEPARATOR) + len(piece.text)
                > self._capacity
            ):
                flush()
            current.append(piece)

        for piece in pieces:
            if piece.boundary and current and not all(p.heading_only for p in current):
                flush()
            if len(piece.text) <= self._capacity:
                add(piece)
                continue
            room = self._capacity - _length(current) - len(_SEPARATOR) if current else 0
            first = room if room >= self.min_chars else self._capacity
            parts = (
                self._split_table(piece, first)
                if piece.table is not None
                else self._split_text(piece, first)
            )
            for part in parts:
                add(part)
        flush()
        return drafts

    def _split_text(self, piece: _Piece, first: int) -> list[_Piece]:
        """Split an oversized paragraph at sentence ends, then at spaces."""
        return [
            replace(piece, text=t)
            for t in split_text(piece.text, first, self._capacity)
        ]

    def _split_table(self, piece: _Piece, first: int) -> list[_Piece]:
        """Split an oversized table by rows, repeating its header in each part."""
        table = piece.table
        assert table is not None
        header_length = len(table.render(())) + 1
        if header_length > self._capacity // 2:
            return self._split_text(replace(piece, table=None), first)
        rows: list[Row] = []
        for row in table.rows:
            if header_length + len(row.text) <= self._capacity:
                rows.append(row)
            else:
                fragments = split_text(row.text, self._capacity - header_length)
                rows.extend(Row(fragment, row.page) for fragment in fragments)
        groups: list[list[Row]] = [[]]
        limit = first
        for row in rows:
            size = (
                header_length + sum(len(r.text) + 1 for r in groups[-1]) + len(row.text)
            )
            if groups[-1] and size > limit:
                groups.append([])
                limit = self._capacity
            elif not groups[-1] and header_length + len(row.text) > limit:
                limit = self._capacity
            groups[-1].append(row)
        parts = []
        for group in groups:
            part = Table(table.header, tuple(group), table.header_page)
            parts.append(
                replace(
                    piece,
                    text=part.render(),
                    page_start=part.page_start,
                    page_end=part.page_end,
                    table=part,
                )
            )
        return parts

    def _merge_small(self, drafts: list[list[_Piece]]) -> list[list[_Piece]]:
        """Merge drafts below ``min_chars`` into a neighbor of the same section."""
        merged: list[list[_Piece]] = []
        for draft in drafts:
            previous = merged[-1] if merged else None
            if (
                previous is not None
                and _section(previous) == _section(draft)
                and min(_length(previous), _length(draft)) < self.min_chars
                and _length(previous) + len(_SEPARATOR) + _length(draft)
                <= self._capacity
                and not draft[0].boundary
            ):
                previous.extend(draft)
            else:
                merged.append(list(draft))
        return merged

    def _with_overlap(self, drafts: list[list[_Piece]]) -> list[tuple[str, int]]:
        """Render drafts as (text, page_start), adding overlap to continuations.

        Overlap is only added when a section was split mid-text: never across
        headings, and never around tables (their header is already repeated).
        """
        texts = [_SEPARATOR.join(p.text for p in draft) for draft in drafts]
        result = []
        for index, (draft, text) in enumerate(zip(drafts, texts, strict=True)):
            page_start = min(p.page_start for p in draft)
            previous = drafts[index - 1] if index else None
            continues = (
                self.overlap_chars > 0
                and previous is not None
                and not draft[0].boundary
                and draft[0].table is None
                and previous[-1].table is None
                and _section(previous) == _section(draft)
            )
            if continues:
                tail = texts[index - 1][-self.overlap_chars :]
                tail = tail.split(" ", 1)[1] if " " in tail else tail
                if tail.strip():
                    text = f"{tail.strip()} {text}"
                    page_start = min(page_start, previous[-1].page_end)
            result.append((text, page_start))
        return result


def split_text(text: str, first: int, rest: int | None = None) -> list[str]:
    """Split text into parts of at most ``first`` then ``rest`` characters.

    Prefers a sentence end in the second half of the window, then the last
    space; words longer than the window are cut.
    """
    limit, rest = first, rest or first
    parts: list[str] = []
    text = text.strip()
    while len(text) > limit:
        window = text[: limit + 1]
        cut = 0
        for match in _SENTENCE_END.finditer(window):
            if match.start() + 1 >= limit // 2:
                cut = match.start() + 1
        if not cut:
            space = window.rfind(" ")
            cut = space if space > 0 else limit
        parts.append(text[:cut].rstrip())
        text = text[cut:].lstrip()
        limit = rest
    if text:
        parts.append(text)
    return parts


def _length(pieces: list[_Piece]) -> int:
    """Return the rendered length of pieces joined by the separator."""
    if not pieces:
        return 0
    return sum(len(p.text) for p in pieces) + len(_SEPARATOR) * (len(pieces) - 1)


def _section(draft: list[_Piece]) -> str | None:
    """Return the heading path of the first content piece of a draft."""
    content = next((p for p in draft if not p.heading_only), draft[-1])
    return " > ".join(h.label for h in content.path) or None


def _parse_page(page: PageText) -> list[_Segment]:
    """Parse one normalized page into paragraphs, headings, list items and tables.

    Table-of-contents pages (several dot-leader lines) are parsed without
    heading detection, so TOC entries never become sections.
    """
    toc = sum(map(is_toc_line, page.text.split("\n"))) >= _TOC_MIN_LINES
    segments: list[_Segment] = []
    position = 0
    for match in HTML_TABLE.finditer(page.text):
        before = page.text[position : match.start()]
        segments.extend(_parse_lines(before, page.page_number, headings=not toc))
        table = parse_html_table(match.group(0), page.page_number)
        if table is not None:
            segments.append(_Segment(page.page_number, page.page_number, table=table))
        position = match.end()
    segments.extend(
        _parse_lines(page.text[position:], page.page_number, headings=not toc)
    )
    return segments


def _parse_lines(text: str, page: int, *, headings: bool = True) -> list[_Segment]:
    """Group lines into segments; wrapped lines of a paragraph are unwrapped."""
    segments: list[_Segment] = []
    paragraph: list[str] = []
    heading: Heading | None = None
    list_item = False
    table_lines: list[str] = []

    def close_paragraph() -> None:
        nonlocal heading, list_item
        if paragraph:
            only_heading = (
                heading is not None and heading.standalone and len(paragraph) == 1
            )
            segments.append(
                _Segment(
                    page, page, " ".join(paragraph), heading, only_heading, list_item
                )
            )
        paragraph.clear()
        heading, list_item = None, False

    def close_table() -> None:
        if table_lines:
            table = parse_markdown_table(table_lines.copy(), page)
            segments.append(_Segment(page, page, table=table))
            table_lines.clear()

    wrapped_title = False
    for line in text.split("\n"):
        if is_table_line(line):
            close_paragraph()
            table_lines.append(line)
            continue
        close_table()
        if not line:
            close_paragraph()
            continue
        detected = (
            detect_heading(line, paragraph[-1] if paragraph else None)
            if headings
            else None
        )
        if wrapped_title:
            wrapped_title = False
            if (
                detected is None
                and not is_list_item(line)
                and len(line) <= _MAX_WRAPPED_TITLE
            ):
                paragraph[-1] = f"{paragraph[-1]} {line}"
                heading = detect_heading(paragraph[-1]) or heading
                close_paragraph()
                continue
            close_paragraph()
        if detected is not None:
            close_paragraph()
            paragraph.append(line)
            heading = detected
            if detected.standalone and detected.rank not in (
                Rank.ARTICULO,
                Rank.PARAGRAFO,
            ):
                # pdftotext wraps long titles ("… PARA LA" / "JUVENTUD"): a title
                # ending in a connector word continues on the next short line.
                wrapped_title = not line.startswith("#") and bool(
                    _DANGLING_WORD.search(line)
                )
                if not wrapped_title:
                    close_paragraph()
        elif is_list_item(line):
            close_paragraph()
            paragraph.append(line)
            list_item = True
        else:
            paragraph.append(line)
    close_table()
    close_paragraph()
    return segments


def _join_across_pages(pages: list[list[_Segment]]) -> list[_Segment]:
    """Flatten pages, merging tables and sentences that continue on the next page."""
    joined: list[_Segment] = []
    for segments in pages:
        for index, segment in enumerate(segments):
            previous = joined[-1] if joined else None
            if index == 0 and previous is not None:
                merged = _continuation(previous, segment)
                if merged is not None:
                    joined[-1] = merged
                    continue
            joined.append(segment)
    return joined


def _continuation(previous: _Segment, segment: _Segment) -> _Segment | None:
    """Return the merge of a page-final and the next page-initial segment."""
    if segment.page_start != previous.page_end + 1:
        return None
    if previous.table is not None and segment.table is not None:
        if previous.table.columns != segment.table.columns:
            return None
        table = previous.table.continued_by(segment.table)
        return _Segment(table.page_start, table.page_end, table=table)
    if (
        previous.table is None
        and segment.table is None
        and segment.heading is None
        and not segment.list_item
        and not previous.heading_only
        and not _ENDS_SENTENCE.search(previous.text)
        and segment.text[:1].islower()
    ):
        text = f"{previous.text} {segment.text}"
        return replace(previous, text=text, page_end=segment.page_end)
    return None


def _assign_paths(segments: list[_Segment]) -> list[_Piece]:
    """Attach the active heading path to every segment, in document order.

    Normative headings replace deeper or equal levels; a numbered heading only
    keeps numbered ancestors whose number prefixes its own (9 → 9.4 → 9.4.1).
    Markdown and preamble headings only set the path while no normative
    heading is active; inside
    normative structure they are kept as plain text (often a chapter name or a
    running header) so they never overwrite a detected article.
    """
    path: tuple[Heading, ...] = ()
    pieces: list[_Piece] = []
    for segment in segments:
        heading, boundary = segment.heading, False
        if heading is not None and heading.normative:
            path = (*(h for h in path if _is_ancestor(h, heading)), heading)
            boundary = heading.rank < Rank.PARAGRAFO
        elif heading is not None and not any(h.normative for h in path):
            path, boundary = (heading,), True
        text = segment.table.render() if segment.table is not None else segment.text
        pieces.append(
            _Piece(
                text=text,
                page_start=segment.page_start,
                page_end=segment.page_end,
                path=path,
                heading_only=segment.heading_only,
                boundary=boundary,
                table=segment.table,
            )
        )
    return pieces


def _is_ancestor(candidate: Heading, heading: Heading) -> bool:
    """Return whether an active heading still contains a new normative heading."""
    if not candidate.normative or candidate.rank >= heading.rank:
        return False
    if candidate.number is not None and heading.number is not None:
        return heading.number.startswith(f"{candidate.number}.")
    return True
