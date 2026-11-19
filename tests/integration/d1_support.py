"""Real D1 schema on in-memory SQLite behind a fake ``DB`` binding.

The migration is read from the repository (``infra/d1/migrations``) so the
adapters run the exact SQL D1 will run. The fake mimics the SDK-converted
D1 result shape: ``{"results": [dict, ...], "success": True, "meta": {...}}``.
"""

import sqlite3
from hashlib import sha256
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
MIGRATIONS_DIR = REPO / "infra/d1/migrations"
QUERIES_FILE = REPO / "infra/d1/queries.sql"


def require(path: Path) -> Path:
    """Fail clearly when the monorepo file the test depends on is missing."""
    if not path.is_file():
        pytest.fail(
            f"missing {path.relative_to(REPO)}: these tests need the full monorepo "
            "checkout (infra/d1 is the canonical D1 schema)",
            pytrace=False,
        )
    return path


def canonical_query(name: str) -> str:
    """Return one ``-- name:`` block of infra/d1/queries.sql without comments."""
    lines, current = [], None
    for line in require(QUERIES_FILE).read_text(encoding="utf-8").splitlines():
        if line.startswith("-- name:"):
            current = line.removeprefix("-- name:").strip()
        elif current == name and not line.startswith("--"):
            lines.append(line)
    if not lines:
        pytest.fail(f"query {name!r} not found in infra/d1/queries.sql")
    return "\n".join(lines).strip()


def digest(text: str) -> str:
    """Return the sha256 hex of UTF-8 text."""
    return sha256(text.encode("utf-8")).hexdigest()


class _Statement:
    """Immutable prepared statement over sqlite3."""

    def __init__(self, db: SqliteD1, sql: str, params: tuple = ()):
        """Keep the connection, SQL and bound parameters."""
        self._db, self._sql, self._params = db, sql, params

    def bind(self, *params):
        """Return a copy with parameters bound, like D1."""
        return _Statement(self._db, self._sql, params)

    async def all(self):
        """Execute and return the D1-shaped result."""
        self._db.calls.append((self._sql, self._params))
        if self._db.fail:
            raise RuntimeError("D1_ERROR SECRET")
        rows = [dict(row) for row in self._db.conn.execute(self._sql, self._params)]
        return {"results": rows, "success": True, "meta": {"rows_read": len(rows)}}


class SqliteD1:
    """Fake ``env.DB`` binding executing real SQL on sqlite3."""

    def __init__(self):
        """Create the database and apply every migration in order."""
        migrations = sorted(MIGRATIONS_DIR.glob("[0-9][0-9][0-9][0-9]_*.sql"))
        if not migrations:
            require(MIGRATIONS_DIR / "0001_initial.sql")
        self.conn = sqlite3.connect(":memory:", autocommit=True)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        for migration in migrations:
            self.conn.executescript(migration.read_text(encoding="utf-8"))
        self.calls: list[tuple[str, tuple]] = []
        self.fail = False

    def prepare(self, sql: str) -> _Statement:
        """Prepare a statement."""
        return _Statement(self, sql)

    # -- seeding through the schema's state machine ------------------------------
    def add_document(self, doc_id: str, title: str) -> None:
        """Insert a logical document."""
        self.conn.execute(
            "INSERT INTO documents (id, title, document_type) VALUES (?, ?, ?)",
            (doc_id, title, "resolucion"),
        )

    def add_version(
        self,
        version_id: str,
        doc_id: str,
        *,
        label: str | None = None,
        page_count: int = 20,
        effective: tuple[str | None, str | None] = (None, None),
    ) -> None:
        """Insert an ``uploaded`` version with page count and validity."""
        self.conn.execute(
            canonical_query("insert_version_idempotent"),
            (version_id, doc_id, label, f"raw/{version_id}.pdf", digest(version_id), 9),
        )
        self.conn.execute(
            "UPDATE document_versions SET page_count = ?, effective_from = ?,"
            " effective_until = ? WHERE id = ?",
            (page_count, *effective, version_id),
        )

    def add_chunk(
        self,
        chunk_id: str,
        version_id: str,
        text: str,
        *,
        section: str | None = None,
        pages: tuple[int, int] = (1, 1),
    ) -> None:
        """Insert a chunk (version must not be published)."""
        ordinal = self.conn.execute(
            "SELECT count(*) FROM chunks WHERE version_id = ?", (version_id,)
        ).fetchone()[0]
        self.conn.execute(
            "INSERT INTO chunks (id, version_id, ordinal, text, text_sha256, section,"
            " page_start, page_end, token_estimate) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (chunk_id, version_id, ordinal, text, digest(text), section, *pages, 10),
        )

    def set_status(self, version_id: str, status: str) -> None:
        """Move a version through one allowed transition."""
        self.conn.execute(
            "UPDATE document_versions SET status = ? WHERE id = ?", (status, version_id)
        )

    def make_ready(self, version_id: str) -> None:
        """Walk uploaded → processing → extracted → review_required → ready."""
        for status in ("processing", "extracted", "review_required"):
            self.set_status(version_id, status)
        self.conn.execute(
            "UPDATE document_versions SET status = 'ready', requires_review = 0,"
            " extraction_r2_key = ?, chunker_version = 'structural-v1' WHERE id = ?",
            (f"extractions/{version_id}.json", version_id),
        )

    def publish(self, version_id: str) -> None:
        """Publish a ready or unpublished version."""
        self.conn.execute(
            "UPDATE document_versions SET status = 'published',"
            " published_at = '2026-11-18T00:00:00.000Z' WHERE id = ?",
            (version_id,),
        )

    def version_source(self, version_id: str) -> str:
        """Return the stored source sha256 of a version."""
        return self.conn.execute(
            "SELECT source_sha256 FROM document_versions WHERE id = ?", (version_id,)
        ).fetchone()[0]


BECA_TEXT = (
    "La beca de alimentación cubre el almuerzo de lunes a viernes. "
    "La inscripción se realiza hasta el 30 de junio de 2026 en Bienestar."
)
TRANSPORTE_TEXT = (
    "El apoyo de transporte se entrega a estudiantes que residen fuera del "
    "municipio y acreditan su matrícula vigente."
)
DRAFT_TEXT = "Borrador: la beca de alimentación se amplía a los sábados."


def seeded() -> SqliteD1:
    """Corpus: one published version (2 chunks) and one draft version (1 chunk)."""
    db = SqliteD1()
    db.add_document("doc-beca", "Reglamento de Bienestar Universitario")
    db.add_version(
        "ver-2026",
        "doc-beca",
        label="2026-1",
        effective=("2026-02-01", "2026-06-30"),
    )
    db.add_chunk(
        "chunk-beca", "ver-2026", BECA_TEXT, section="Capítulo II", pages=(3, 4)
    )
    db.add_chunk("chunk-transporte", "ver-2026", TRANSPORTE_TEXT, pages=(5, 5))
    db.make_ready("ver-2026")
    db.publish("ver-2026")
    db.add_version("ver-draft", "doc-beca", label="2026-2")
    db.add_chunk("chunk-draft", "ver-draft", DRAFT_TEXT, pages=(1, 1))
    db.make_ready("ver-draft")
    return db
