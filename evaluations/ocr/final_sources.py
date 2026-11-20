"""Load the final source plan, the per-page source markdown and the 120 corrections.

Shared by ``assemble_final_cache.py`` (cache objects + validation + seeds) and
its tests. Offline and read-only: every input artifact is identified by its
SHA-256 (pinned where a previous step already pinned it) and recorded in the
provenance of each page. No network, no ``.env``.

Inputs (``reports/alt-ocr-v2-audit.json`` → ``final_source_plan``):

* ``reusable_pdftotext`` / ``reusable_firecrawl_prev``: page evidence of
  ``extraction-corpus-v2/documents.json`` (method ``pdftotext`` or
  ``firecrawl_parse_ocr``).
* ``firecrawl_v1_lots01_03``: ``data.pages[].markdown`` of the complete
  ``selective-ocr-v1`` lots, mapped from fragment page to physical page.
* ``azure_pilot_v1``: ``alt-ocr-pilot-v1/export/azure/pages.json``.
* ``azure_v2``: ``alt-ocr-v2/export/pages.json``.
"""

import sys
from dataclasses import dataclass, field
from pathlib import Path

sys.dont_write_bytecode = True
from pilot import ARTIFACTS, BASE, digest
from remaining_send import coverage, decode, safe
from selective_send import EXECUTION_NAME, lot_state

REPO = BASE.parents[1]
REPORTS = BASE / "reports"
V2_AUDIT = REPORTS / "alt-ocr-v2-audit.json"
V1_AUDIT = REPORTS / "selective-ocr-v1-audit.json"
CORPUS = ARTIFACTS / "extraction-corpus-v2/documents.json"
SELECTIVE = ARTIFACTS / "selective-ocr-v1"
PILOT_EXPORT = ARTIFACTS / "alt-ocr-pilot-v1/export/azure/pages.json"
V2_EXPORT = ARTIFACTS / "alt-ocr-v2/export/pages.json"

# Pins already fixed by previous steps (assemble_cache.py, selective_send.py,
# alt-ocr-v2-audit.json "inputs"). The pilot export is pinned here for the
# first time (value computed when this assembly was written).
CORPUS_SHA256 = "d0057b4292c2c94d3963aea6c2e33a3e0db368abf3ede936a3c5ce657563f0eb"
SELECTIVE_MANIFEST_SHA256 = (
    "5868748c1b2ec6e5bb40959ca8e6ee0aeec41762db580426923f8843fe433161"
)
V2_EXPORT_SHA256 = "58791c31bb35ee8f339e4a8c0b6e9576b07bc318bcd481021cffd227e8a3c272"
PILOT_EXPORT_SHA256 = "0377cd9e54934ee5f368610686d1473c42dfb702b5445f10075c9539088594b9"

SOURCES = (
    "reusable_pdftotext",
    "reusable_firecrawl_prev",
    "firecrawl_v1_lots01_03",
    "azure_pilot_v1",
    "azure_v2",
)
EXPECTED_PAGES = 216
EXPECTED_CORRECTIONS = 120
EXPECTED_CORRECTION_PAGES = 59

# Audit "target" of each correction -> plan source whose markdown it edits.
TARGET_SOURCE = {
    "azure": "azure_v2",
    "firecrawl_v1": "firecrawl_v1_lots01_03",
    "azure_pilot_v1": "azure_pilot_v1",
    "firecrawl_v1_lots01_03": "firecrawl_v1_lots01_03",
}


def require(condition: bool, label: str = "Final assembly check failed") -> None:
    """Fail closed with a controlled label (never document text)."""
    if not condition:
        raise ValueError(label)


def rel(path: Path) -> str:
    """Repo-relative POSIX path for provenance (never absolute)."""
    return path.resolve().relative_to(REPO).as_posix()


@dataclass(frozen=True)
class Page:
    """One physical page of the final cache with its provenance."""

    number: int
    markdown: str
    source: str
    artifact: str
    artifact_sha256: str
    locator: str


@dataclass
class Document:
    """One original PDF of the corpus and its final pages."""

    slug: str
    source_path: str
    source_sha256: str
    page_count: int
    pages: list[Page] = field(default_factory=list)


@dataclass(frozen=True)
class Correction:
    """One required literal correction, as formulated by its audit."""

    document: str
    page: int
    find_text: str
    replace_text: str
    reason: str
    origin: str
    target_source: str
    kind: str


def load_json(path: Path, pinned: str | None = None) -> tuple[dict, str]:
    """Decode a JSON artifact (duplicate keys refused) and return it with its SHA."""
    raw = safe(path).read_bytes()
    sha = digest(raw)
    if pinned is not None:
        require(sha == pinned, f"Identity mismatch: {path.name}")
    return decode(raw), sha


def load_plan(audit: dict) -> dict[str, list[dict]]:
    """Group ``final_source_plan`` by source PDF; require full, unique coverage."""
    plan: dict[str, list[dict]] = {}
    for item in audit["final_source_plan"]:
        require(item["source"] in SOURCES, "Unknown plan source")
        plan.setdefault(item["source_pdf"], []).append(item)
    total = 0
    for items in plan.values():
        numbers = sorted(i["physical_page"] for i in items)
        require(numbers == list(range(1, len(numbers) + 1)), "Plan page gap")
        require(len({i["document"] for i in items}) == 1, "Plan document mismatch")
        total += len(numbers)
    require(total == EXPECTED_PAGES, "Plan page total mismatch")
    return plan


class SourceStore:
    """Lazily load each source artifact once and serve page markdown by key."""

    def __init__(
        self,
        corpus_path: Path = CORPUS,
        selective_root: Path = SELECTIVE,
        pilot_export: Path = PILOT_EXPORT,
        v2_export: Path = V2_EXPORT,
        pins: bool = True,
    ) -> None:
        """Paths are injectable for tests; ``pins=False`` skips SHA pins."""
        self.corpus_path = corpus_path
        self.selective_root = selective_root
        self.pilot_export = pilot_export
        self.v2_export = v2_export
        self.pins = pins
        self._corpus: tuple[dict, str] | None = None
        self._selective: dict | None = None
        self._azure: dict[Path, tuple[dict, str]] = {}

    def _pin(self, value: str) -> str | None:
        return value if self.pins else None

    def corpus_page(self, source_path: str, number: int, method: str) -> Page:
        """Text of one corpus page from the evidence of ``method`` (exactly one)."""
        if self._corpus is None:
            self._corpus = load_json(self.corpus_path, self._pin(CORPUS_SHA256))
        corpus, sha = self._corpus
        docs = [
            d
            for d in corpus["documents"]
            if d["direct_document"]["source_path"] == source_path
        ]
        require(len(docs) == 1, "Corpus document missing")
        pages = [p for p in docs[0]["pages"] if p["physical_page"] == number]
        require(len(pages) == 1, "Corpus page missing")
        evidence = [e for e in pages[0]["evidence"] if e["method"] == method]
        require(len(evidence) == 1, "Ambiguous corpus evidence")
        page = evidence[0]["page"]
        text = page["text"] if method == "pdftotext" else page["markdown"]
        source = (
            "reusable_pdftotext" if method == "pdftotext" else "reusable_firecrawl_prev"
        )
        return Page(
            number,
            text,
            source,
            rel(self.corpus_path),
            sha,
            f"documents[source_path].pages[{number}].evidence[{method}]",
        )

    def _load_selective(self) -> dict:
        manifest_raw = safe(self.selective_root / "manifest.json").read_bytes()
        if self.pins:
            require(
                digest(manifest_raw) == SELECTIVE_MANIFEST_SHA256,
                "Selective manifest mismatch",
            )
        manifest = decode(manifest_raw)
        pages: dict[tuple[str, int], Page] = {}
        for lot in manifest["lots"]:
            folder = self.selective_root / EXECUTION_NAME / lot["lot_id"]
            state, _ = lot_state(folder)
            if state != "complete":
                continue  # Only complete lots can serve pages (01-03 in the plan).
            attempt = decode(safe(folder / "attempt.json").read_bytes())
            require(attempt["fragment_sha256"] == lot["sha256"], "Attempt mismatch")
            raw = safe(folder / "response.raw").read_bytes()
            value = decode(raw)
            require(value["success"] is True, "Unsuccessful response")
            coverage(value, lot["page_count"])
            sha = digest(raw)
            for item in value["data"]["pages"]:
                physical = lot["mapping"][str(item["pageNumber"])]
                key = (lot["source_path"], physical)
                require(key not in pages, "Duplicate physical page")
                pages[key] = Page(
                    physical,
                    item["markdown"],
                    "firecrawl_v1_lots01_03",
                    rel(folder / "response.raw"),
                    sha,
                    f"lot={lot['lot_id']} data.pages[pageNumber={item['pageNumber']}]",
                )
        return pages

    def selective_page(self, source_path: str, number: int) -> Page:
        """Firecrawl page of a complete selective-ocr-v1 lot."""
        if self._selective is None:
            self._selective = self._load_selective()
        page = self._selective.get((source_path, number))
        require(page is not None, "Selective page missing")
        return page

    def azure_page(self, which: str, document: str, number: int) -> Page:
        """Azure markdown from the pilot or v2 export (exactly one entry)."""
        path, pin = (
            (self.pilot_export, PILOT_EXPORT_SHA256)
            if which == "azure_pilot_v1"
            else (self.v2_export, V2_EXPORT_SHA256)
        )
        if path not in self._azure:
            self._azure[path] = load_json(path, self._pin(pin))
        export, sha = self._azure[path]
        found = [
            p
            for p in export["pages"]
            if p["document"] == document and p["pageNumber"] == number
        ]
        require(len(found) == 1, "Azure page missing")
        return Page(
            number,
            found[0]["markdown"],
            which,
            rel(path),
            sha,
            f"pages[page_id={found[0]['page_id']}]",
        )

    def page(self, item: dict) -> Page:
        """Markdown of one plan entry from its declared source."""
        source, n = item["source"], item["physical_page"]
        if source == "reusable_pdftotext":
            return self.corpus_page(item["source_pdf"], n, "pdftotext")
        if source == "reusable_firecrawl_prev":
            return self.corpus_page(item["source_pdf"], n, "firecrawl_parse_ocr")
        if source == "firecrawl_v1_lots01_03":
            return self.selective_page(item["source_pdf"], n)
        return self.azure_page(source, item["document"], n)


def build_documents(
    plan: dict[str, list[dict]], store: SourceStore, identities: dict[str, str]
) -> list[Document]:
    """Every physical page exactly once, from its planned source; fail on gaps.

    ``identities`` maps source path -> PDF SHA-256 (verified by the caller).
    """
    documents = []
    for source_path, items in plan.items():
        items = sorted(items, key=lambda i: i["physical_page"])
        doc = Document(
            items[0]["document"], source_path, identities[source_path], len(items)
        )
        for item in items:
            page = store.page(item)
            require(page.number == item["physical_page"], "Page number mismatch")
            require(page.source == item["source"], "Page source mismatch")
            require(
                isinstance(page.markdown, str) and bool(page.markdown.strip()),
                "Page without markdown",
            )
            doc.pages.append(page)
        documents.append(doc)
    return documents


def load_corrections(
    v2_audit: dict, v1_audit: dict, plan: dict[str, list[dict]]
) -> list[Correction]:
    """The required corrections the plan points to, in audit order.

    * v2 ``corrections`` with ``required: true`` (61).
    * v1 ``corrections`` (selective-ocr-v1) of the pages whose plan entry is
      ``firecrawl_v1_lots01_03`` with ``corrections_ref`` = the v1 audit,
      ``kind == substantive`` and not ``optional`` (55; cosmetic/editorial excluded).
    * v2 ``pilot_corrections`` with ``required: true`` (4).
    """
    by_page = {
        (i["document"], i["physical_page"]): i for items in plan.values() for i in items
    }
    path_to_slug = {path: items[0]["document"] for path, items in plan.items()}
    result: list[Correction] = []
    for c in v2_audit["corrections"]:
        if c["required"]:
            result.append(
                Correction(
                    c["document"],
                    c["physical_page"],
                    c["find_text"],
                    c["replace_text"],
                    c["reason"],
                    f"alt-ocr-v2-audit.json corrections ({c['page_id']})",
                    TARGET_SOURCE[c["target"]],
                    c["kind"],
                )
            )
    for c in v1_audit["corrections"]:
        slug = path_to_slug[c["source"]]
        item = by_page[(slug, c["physical_page"])]
        if (
            item["source"] == "firecrawl_v1_lots01_03"
            and item["corrections_ref"] == "selective-ocr-v1-audit.json corrections"
            and c["kind"] == "substantive"
            and not c.get("optional", False)
        ):
            result.append(
                Correction(
                    slug,
                    c["physical_page"],
                    c["find_text"],
                    c["replace_text"],
                    c["reason"],
                    f"selective-ocr-v1-audit.json corrections (lot {c['lot']})",
                    "firecrawl_v1_lots01_03",
                    c["kind"],
                )
            )
    for c in v2_audit["pilot_corrections"]:
        if c["required"]:
            result.append(
                Correction(
                    c["document"],
                    c["physical_page"],
                    c["find_text"],
                    c["replace_text"],
                    c["reason"],
                    "alt-ocr-v2-audit.json pilot_corrections",
                    TARGET_SOURCE[c["target"]],
                    c["kind"],
                )
            )
    for c in result:
        item = by_page[(c.document, c.page)]
        require(item["source"] == c.target_source, "Correction targets another source")
    return result
