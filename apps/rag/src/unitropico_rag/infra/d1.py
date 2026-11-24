"""D1 adapters: FTS5 lexical search and the authoritative chunk catalog.

The SQL is copied verbatim from ``infra/d1/queries.sql`` (blocks
``search_chunks``, ``registry_by_chunk_ids`` and ``registry_by_chunk_id``);
``tests/test_d1.py`` fails if the copies drift.
"""

import asyncio
import json
from collections.abc import Mapping, Sequence
from datetime import date
from math import isfinite
from typing import Protocol

from unitropico_rag.domain.evidence import ChunkCandidate, RankedChunk, RegistryEntry
from unitropico_rag.infra.fts_query import build_fts5_query
from unitropico_rag.infra.fts_synonyms import WELFARE_SYNONYMS, expand_fts5_query

# Keep batches within the documented json_each bound (queries.md §2.1).
MAX_BATCH_IDS = 100

SEARCH_CHUNKS_SQL = """\
SELECT
    c.id               AS chunk_id,
    c.version_id       AS version_id,
    v.document_id      AS document_id,
    bm25(chunks_fts)   AS score
FROM chunks_fts
JOIN chunks c ON c.seq = chunks_fts.rowid
JOIN document_versions v ON v.id = c.version_id
WHERE chunks_fts MATCH ?1
  AND v.status = 'published'
  AND v.requires_review = 0
  AND NOT EXISTS (
      SELECT 1 FROM document_versions s
      WHERE s.supersedes_version_id = v.id AND s.status = 'published'
  )
ORDER BY score, c.seq
LIMIT ?2;"""

_REGISTRY_COLUMNS = """\
SELECT
    r.chunk_id, r.document_id, r.document_title, r.version_id, r.version_label,
    r.source_sha256, r.section, r.page_start, r.page_end, r.text, r.text_sha256,
    r.status, r.requires_review, r.formally_superseded,
    r.issued_at, r.effective_from, r.effective_until
FROM v_chunk_registry r
"""

REGISTRY_BY_CHUNK_IDS_SQL = (
    _REGISTRY_COLUMNS + "WHERE r.chunk_id IN (SELECT value FROM json_each(?1));"
)
REGISTRY_BY_CHUNK_ID_SQL = _REGISTRY_COLUMNS + "WHERE r.chunk_id = ?1;"


class D1ResultError(ValueError):
    """Signal a D1 result whose shape or values break the schema contract."""


class D1Statement(Protocol):
    """Prepared statement surface used here (D1 or a test double)."""

    def bind(self, *values: object) -> D1Statement:
        """Return the statement with positional parameters bound."""
        ...

    async def all(self) -> object:
        """Run the statement and return ``{"results": [...], "meta": ...}``."""
        ...


class D1Database(Protocol):
    """Minimal D1 binding surface: ``prepare(sql)``."""

    def prepare(self, sql: str) -> D1Statement:
        """Prepare one SQL statement."""
        ...


async def query_rows(
    db: D1Database, sql: str, *params: object
) -> list[Mapping[str, object]]:
    """Run one statement and return its rows, rejecting unexpected shapes."""
    statement = db.prepare(sql)
    if params:
        statement = statement.bind(*params)
    result = await statement.all()
    if isinstance(result, Mapping):
        rows = result.get("results")
    else:
        rows = getattr(result, "results", None)
    if not isinstance(rows, list) or not all(isinstance(r, Mapping) for r in rows):
        raise D1ResultError("results")
    return rows


def _text(row: Mapping[str, object], name: str, *, optional: bool = False) -> str:
    """Read a string column; None only when optional."""
    value = row.get(name)
    if value is None and optional:
        return None  # type: ignore[return-value]
    if not isinstance(value, str):
        raise D1ResultError(name)
    return value


def _int(row: Mapping[str, object], name: str) -> int:
    """Read an integer column (bools rejected)."""
    value = row.get(name)
    if type(value) is not int:
        raise D1ResultError(name)
    return value


def _flag(row: Mapping[str, object], name: str) -> bool:
    """Read a 0/1 integer column as bool."""
    value = row.get(name)
    if value not in (0, 1) or type(value) not in (int, bool):
        raise D1ResultError(name)
    return bool(value)


def _date(row: Mapping[str, object], name: str) -> date | None:
    """Read an optional YYYY-MM-DD column."""
    value = row.get(name)
    if value is None:
        return None
    try:
        return date.fromisoformat(value)  # type: ignore[arg-type]
    except TypeError, ValueError:
        raise D1ResultError(name) from None


def registry_entry(row: Mapping[str, object]) -> RegistryEntry:
    """Map a ``v_chunk_registry`` row to a RegistryEntry (no validity label)."""
    return RegistryEntry(
        document_id=_text(row, "document_id"),
        version_id=_text(row, "version_id"),
        chunk_id=_text(row, "chunk_id"),
        source_sha256=_text(row, "source_sha256"),
        page_start=_int(row, "page_start"),
        page_end=_int(row, "page_end"),
        section=_text(row, "section", optional=True),
        text_sha256=_text(row, "text_sha256"),
        status=_text(row, "status"),
        requires_review=_flag(row, "requires_review"),
        formally_superseded=_flag(row, "formally_superseded"),
        document_title=_text(row, "document_title"),
        version_label=_text(row, "version_label", optional=True),
        effective_from=_date(row, "effective_from"),
        effective_until=_date(row, "effective_until"),
    )


def chunk_candidate(row: Mapping[str, object]) -> ChunkCandidate:
    """Map a ``v_chunk_registry`` row to the chunk text with its provenance."""
    return ChunkCandidate(
        document_id=_text(row, "document_id"),
        version_id=_text(row, "version_id"),
        chunk_id=_text(row, "chunk_id"),
        source_sha256=_text(row, "source_sha256"),
        page_start=_int(row, "page_start"),
        page_end=_int(row, "page_end"),
        section=_text(row, "section", optional=True),
        text=_text(row, "text"),
    )


class D1LexicalRetriever:
    """Rank published chunks by FTS5 bm25 (lower is better).

    Synonyms (``fts_synonyms``; ``synonyms=None`` disables them) only
    *backfill*: FTS5 cannot weight single query terms, so a chunk matching a
    rare synonym could outrank one matching the student's own word. When the
    expansion adds terms, two bound queries run concurrently — the reference
    query and the expanded one — and the result is the reference hits first,
    then expanded-only hits in their own bm25 order, up to ``limit``. Scores
    keep each query's native bm25 (trace only; fusion uses ranks).
    """

    def __init__(
        self,
        db: D1Database,
        *,
        max_terms: int = 16,
        synonyms: Mapping[str, tuple[str, ...]] | None = WELFARE_SYNONYMS,
    ):
        """Receive the D1 binding, the FTS term cap and the synonym table."""
        if type(max_terms) is not int or max_terms < 1:
            raise ValueError("max_terms must be a positive integer")
        self._db, self._max_terms, self._synonyms = db, max_terms, synonyms

    async def search(self, question: str, limit: int) -> list[RankedChunk]:
        """Return up to ``limit`` hits; an unsearchable question returns []."""
        if type(limit) is not int or limit < 1:
            raise ValueError("limit must be a positive integer")
        base = build_fts5_query(question, max_terms=self._max_terms)
        if base is None:
            return []
        expanded = expand_fts5_query(
            question, max_terms=self._max_terms, synonyms=self._synonyms
        )
        if expanded is None or expanded == base:
            return await self._match(base, limit)
        exact, broad = await asyncio.gather(
            self._match(base, limit), self._match(expanded, limit)
        )
        seen = {hit.chunk_id for hit in exact}
        backfill = [hit for hit in broad if hit.chunk_id not in seen]
        return (exact + backfill)[:limit]

    async def _match(self, expression: str, limit: int) -> list[RankedChunk]:
        """Run one bound MATCH query and validate the rows."""
        rows = await query_rows(self._db, SEARCH_CHUNKS_SQL, expression, limit)
        hits = []
        for row in rows:
            score = row.get("score")
            if type(score) not in (int, float) or not isfinite(score):
                raise D1ResultError("score")
            hits.append(RankedChunk(_text(row, "chunk_id"), float(score)))
        return hits


class D1EvidenceCatalog:
    """Read current chunk provenance and publication state from D1 on every call.

    Implements ``AsyncEvidenceCatalog`` (single and batch) and the chunk source
    used by fusion to hydrate ranked ids into candidates.
    """

    def __init__(self, db: D1Database):
        """Receive the D1 binding."""
        self._db = db

    async def _rows(self, chunk_ids: Sequence[str]) -> dict[str, Mapping[str, object]]:
        """Fetch registry rows for unique ids in json_each batches."""
        unique = list(dict.fromkeys(chunk_ids))
        if not all(isinstance(chunk_id, str) for chunk_id in unique):
            raise TypeError("chunk ids must be strings")
        found: dict[str, Mapping[str, object]] = {}
        for start in range(0, len(unique), MAX_BATCH_IDS):
            batch = json.dumps(unique[start : start + MAX_BATCH_IDS])
            for row in await query_rows(self._db, REGISTRY_BY_CHUNK_IDS_SQL, batch):
                found[_text(row, "chunk_id")] = row
        return found

    async def get_current(self, chunk_id: str) -> RegistryEntry | None:
        """Return the current entry or None when the chunk is not registered."""
        rows = await query_rows(self._db, REGISTRY_BY_CHUNK_ID_SQL, chunk_id)
        if len(rows) > 1:
            raise D1ResultError("duplicate chunk_id")
        return registry_entry(rows[0]) if rows else None

    async def get_current_many(
        self, chunk_ids: Sequence[str]
    ) -> dict[str, RegistryEntry]:
        """Return entries by chunk id; unregistered ids are absent."""
        rows = await self._rows(chunk_ids)
        return {chunk_id: registry_entry(row) for chunk_id, row in rows.items()}

    async def load(self, chunk_ids: Sequence[str]) -> dict[str, ChunkCandidate]:
        """Return chunk text and provenance by id; unregistered ids are absent."""
        rows = await self._rows(chunk_ids)
        return {chunk_id: chunk_candidate(row) for chunk_id, row in rows.items()}
