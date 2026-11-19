import re
from dataclasses import dataclass
from html.parser import HTMLParser

HTML_TABLE = re.compile(r"<table\b.*?</table\s*>", re.IGNORECASE | re.DOTALL)
_SEPARATOR = re.compile(r"^\|?\s*:?-{3,}:?\s*(?:\|\s*:?-{3,}:?\s*)*\|?$")


@dataclass(frozen=True)
class Row:
    """One rendered Markdown table row and the physical page it came from."""

    text: str
    page: int


@dataclass(frozen=True)
class Table:
    """A table normalized to Markdown: header lines plus data rows."""

    header: tuple[str, ...]
    rows: tuple[Row, ...]
    header_page: int

    @property
    def columns(self) -> int:
        """Return the number of cells in the header row."""
        return count_cells(self.header[0]) if self.header else 0

    @property
    def page_start(self) -> int:
        """Return the first physical page covered by the table."""
        return min([self.header_page, *(row.page for row in self.rows)])

    @property
    def page_end(self) -> int:
        """Return the last physical page covered by the table."""
        return max([self.header_page, *(row.page for row in self.rows)])

    def render(self, rows: tuple[Row, ...] | None = None) -> str:
        """Render the header followed by the given rows (all by default)."""
        selected = self.rows if rows is None else rows
        return "\n".join([*self.header, *(row.text for row in selected)])

    def continued_by(self, other: Table) -> Table:
        """Merge a table continuing on the next page into this one.

        A repeated identical header is dropped. A differing first row is kept
        as data: OCR frequently promotes the first continuation row to a header
        (``<thead>``), and dropping it would lose content.
        """
        rows = list(other.rows)
        if other.header and _cells(other.header[0]) != _cells(self.header[0]):
            rows.insert(0, Row(other.header[0], other.header_page))
        return Table(self.header, self.rows + tuple(rows), self.header_page)


def count_cells(line: str) -> int:
    """Count cells of a Markdown table row, honoring escaped pipes."""
    return len(_cells(line))


def _cells(line: str) -> list[str]:
    """Split a Markdown row into trimmed cells."""
    inner = line.strip()
    inner = inner[1:] if inner.startswith("|") else inner
    inner = inner[:-1] if inner.endswith("|") and not inner.endswith("\\|") else inner
    return [cell.strip().casefold() for cell in re.split(r"(?<!\\)\|", inner)]


def is_table_line(line: str) -> bool:
    """Return whether the line looks like a Markdown table row."""
    return line.startswith("|") and line.count("|") >= 2


def parse_markdown_table(lines: list[str], page: int) -> Table:
    """Build a table from consecutive Markdown rows; the first row is the header."""
    if len(lines) > 1 and _SEPARATOR.match(lines[1]):
        header, body = tuple(lines[:2]), lines[2:]
    else:
        header, body = (lines[0],), lines[1:]
    return Table(header, tuple(Row(line, page) for line in body), page)


class _GridParser(HTMLParser):
    """Collect HTML table cells with their colspan/rowspan."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.rows: list[list[tuple[str, int, int]]] = []
        self._cell: list[str] | None = None
        self._span = (1, 1)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        """Open rows and cells, remembering colspan/rowspan."""
        if tag == "tr":
            self.rows.append([])
        elif tag in {"td", "th"}:
            values = dict(attrs)
            self._cell = []
            self._span = (_span(values.get("colspan")), _span(values.get("rowspan")))
            if not self.rows:
                self.rows.append([])
        elif tag == "br" and self._cell is not None:
            self._cell.append(" ")

    def handle_endtag(self, tag: str) -> None:
        """Close the current cell."""
        if tag in {"td", "th"} and self._cell is not None:
            text = " ".join("".join(self._cell).split()).replace("|", "\\|")
            self.rows[-1].append((text, *self._span))
            self._cell = None

    def handle_data(self, data: str) -> None:
        """Accumulate text inside the current cell."""
        if self._cell is not None:
            self._cell.append(data)


def _span(value: str | None) -> int:
    """Parse a span attribute, defaulting to 1 for missing or invalid values."""
    try:
        return max(1, min(int(value or 1), 50))
    except ValueError:
        return 1


def parse_html_table(html: str, page: int) -> Table | None:
    """Convert an HTML table to Markdown; None when it has no cells.

    Spanned cells repeat their content in every covered position, which is
    what the merged cell means visually (e.g. a single date spanning both
    "Fecha Inicial" and "Fecha Final"). The first row is always the header,
    whether or not the source marked it with ``<thead>``.
    """
    parser = _GridParser()
    parser.feed(html)
    parser.close()
    grid: list[list[str]] = []
    pending: dict[tuple[int, int], str] = {}
    for r, cells in enumerate(parser.rows):
        row: list[str] = []
        col = 0
        for text, colspan, rowspan in cells:
            while (r, col) in pending:
                row.append(pending.pop((r, col)))
                col += 1
            for offset in range(colspan):
                row.append(text)
                for below in range(1, rowspan):
                    pending[(r + below, col + offset)] = text
            col += colspan
        while (r, col) in pending:
            row.append(pending.pop((r, col)))
            col += 1
        if row:
            grid.append(row)
    if not grid:
        return None
    width = max(len(row) for row in grid)
    lines = ["| " + " | ".join(row + [""] * (width - len(row))) + " |" for row in grid]
    separator = "| " + " | ".join(["---"] * width) + " |"
    rows = tuple(Row(line, page) for line in lines[1:])
    return Table((lines[0], separator), rows, page)
