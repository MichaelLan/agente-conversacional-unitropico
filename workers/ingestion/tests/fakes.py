"""Runtime fakes: D1 on sqlite3, R2, Vectorize, OCR, embeddings and steps."""

import inspect
import json
import sqlite3
from collections import Counter
from collections.abc import Sequence
from hashlib import sha256
from pathlib import Path

from unitropico_ingestion.domain.ingestion import ErrorCode, IngestionError
from unitropico_ingestion.ports import VectorRecord

REPO_ROOT = Path(__file__).resolve().parents[3]
MIGRATION = REPO_ROOT / "infra/d1/migrations/0001_initial.sql"
QUERIES = REPO_ROOT / "infra/d1/queries.sql"
MAX_STEP_RESULT_BYTES = 1024 * 1024


def read_repo_file(path: Path) -> str:
    """Read a file shared with infra/, failing with a clear message if missing."""
    if not path.is_file():
        raise FileNotFoundError(
            f"{path} not found: the ingestion tests apply the real D1 schema from "
            "infra/d1 and must run inside the full repository checkout"
        )
    return path.read_text(encoding="utf-8")


# -- D1 ---------------------------------------------------------------------------
class FakeStatement:
    """D1 prepared statement: ``bind`` returns a new bound statement."""

    def __init__(self, db: FakeD1, sql: str, args: tuple = ()):
        """Keep the SQL and bound values."""
        self.db, self.sql, self.args = db, sql, args

    def bind(self, *args: object) -> FakeStatement:
        """Bind positional values (``?1``, ``?2``…)."""
        return FakeStatement(self.db, self.sql, args)

    def _execute(self) -> dict:
        before = self.db.conn.total_changes
        cursor = self.db.conn.execute(self.sql, self.args)
        rows = [dict(row) for row in cursor.fetchall()]
        # Like D1, count rows changed by triggers too (total, not direct).
        changes = self.db.conn.total_changes - before
        return {"results": rows, "meta": {"changes": changes}}

    async def all(self) -> dict:
        """Return ``{"results": [...], "meta": {...}}``."""
        return self._execute()

    async def run(self) -> dict:
        """Same shape as ``all``."""
        return self._execute()

    async def first(self) -> dict | None:
        """Return the first row or None."""
        rows = self._execute()["results"]
        return rows[0] if rows else None


class FakeD1:
    """D1 binding over in-memory SQLite with the real migration applied."""

    def __init__(self):
        """Apply ``infra/d1/migrations/0001_initial.sql``."""
        self.conn = sqlite3.connect(":memory:", autocommit=True)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.executescript(read_repo_file(MIGRATION))
        self.fail_next_batch = 0

    def prepare(self, sql: str) -> FakeStatement:
        """Prepare a statement."""
        return FakeStatement(self, sql)

    async def batch(self, statements: Sequence[FakeStatement]) -> list[dict]:
        """Run statements atomically, like D1 ``batch``."""
        if self.fail_next_batch:
            self.fail_next_batch -= 1
            raise RuntimeError("D1_ERROR: simulated")
        self.conn.execute("BEGIN")
        try:
            results = [statement._execute() for statement in statements]
        except Exception:
            self.conn.execute("ROLLBACK")
            raise
        self.conn.execute("COMMIT")
        return results

    # helpers for tests
    def query(self, sql: str, *args: object) -> list[dict]:
        """Run a read query synchronously."""
        return [dict(row) for row in self.conn.execute(sql, args).fetchall()]

    def version(self, version_id: str) -> dict:
        """Return the version row."""
        return self.query("SELECT * FROM document_versions WHERE id = ?", version_id)[0]

    def seed(self, *, version_id: str, pdf: bytes, requires_review: int = 1) -> dict:
        """Insert a document and an ``uploaded`` version as the admin API would."""
        digest = sha256(pdf).hexdigest()
        self.conn.execute(
            "INSERT OR IGNORE INTO documents (id, title, document_type) "
            "VALUES ('doc-1', 'Reglamento de prueba', 'reglamento')"
        )
        self.conn.execute(
            "INSERT INTO document_versions (id, document_id, source_r2_key, "
            "source_sha256, source_bytes, requires_review) VALUES (?, ?, ?, ?, ?, ?)",
            (
                version_id,
                "doc-1",
                f"raw/{digest}.pdf",
                digest,
                len(pdf),
                requires_review,
            ),
        )
        return {"sha256": digest, "key": f"raw/{digest}.pdf"}


# -- R2 ---------------------------------------------------------------------------
class FakeBlobStore:
    """Dict-backed BlobStore with call counters."""

    def __init__(self):
        """Start empty."""
        self.objects: dict[str, bytes] = {}
        self.puts: Counter[str] = Counter()

    async def get(self, key: str) -> bytes | None:
        """Return bytes or None."""
        return self.objects.get(key)

    async def exists(self, key: str) -> bool:
        """Return whether the key exists."""
        return key in self.objects

    async def put(self, key: str, data: bytes, *, content_type: str) -> None:
        """Store bytes."""
        assert isinstance(data, bytes) and content_type
        self.objects[key] = data
        self.puts[key] += 1


# -- providers --------------------------------------------------------------------
class FakeOcr:
    """OcrClient returning a fixed body or raising a fixed error."""

    def __init__(self, body: bytes | None = None, error: IngestionError | None = None):
        """Configure the outcome."""
        self.body, self.error, self.calls = body, error, 0

    async def parse(self, pdf: bytes, *, filename: str) -> bytes:
        """Count the (would-be paid) call."""
        self.calls += 1
        assert pdf.startswith(b"%PDF") and filename.endswith(".pdf")
        if self.error is not None:
            raise self.error
        assert self.body is not None
        return self.body


class FakeEmbedder:
    """Deterministic embedder; ``dimensions`` may differ from the config."""

    def __init__(self, dimensions: int = 8, fail_times: int = 0):
        """Configure dimension and transient failures."""
        self.dimensions, self.fail_times = dimensions, fail_times
        self.calls = 0
        self.texts: list[str] = []

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        """Return a vector derived from each text hash."""
        self.calls += 1
        if self.fail_times:
            self.fail_times -= 1
            raise IngestionError(ErrorCode.EMBEDDING_FAILED, "status=503")
        self.texts.extend(texts)
        return [
            [b / 255 for b in sha256(text.encode()).digest()[: self.dimensions]]
            for text in texts
        ]


class FakeVectorIndex:
    """Vectorize stand-in with full call log."""

    def __init__(self):
        """Start empty."""
        self.vectors: dict[str, VectorRecord] = {}
        self.upserts: list[list[str]] = []
        self.deletes: list[list[str]] = []

    async def upsert(self, vectors: Sequence[VectorRecord]) -> None:
        """Insert or replace by id."""
        assert len(vectors) <= 1000
        self.upserts.append([v.id for v in vectors])
        for vector in vectors:
            self.vectors[vector.id] = vector

    async def delete_by_ids(self, ids: Sequence[str]) -> None:
        """Delete by id."""
        self.deletes.append(list(ids))
        for vector_id in ids:
            self.vectors.pop(vector_id, None)


# -- Workflows step -----------------------------------------------------------------
class FakeNonRetryableError(Exception):
    """Stand-in for ``workers.workflows.NonRetryableError``."""


class SimulatedCrash(BaseException):
    """Isolate eviction after a step completed: bypasses ``except Exception``."""


class FakeStep:
    """Minimal Workflows engine: caches results by name, honours retries.

    Passing the same ``cache`` to a new ``FakeStep`` simulates resuming an
    instance: completed steps return their cached (JSON round-tripped) result
    without running their body again.
    """

    def __init__(self, cache: dict | None = None, crash_after: str | None = None):
        """Configure the replay cache and an optional crash point."""
        self.cache = cache if cache is not None else {}
        self.crash_after = crash_after
        self.executed: list[str] = []
        self.attempts: Counter[str] = Counter()
        self.configs: dict[str, dict] = {}

    def do(self, name=None, *, concurrent=False, config=None):
        """Return the step decorator (implicit dependencies are not used)."""

        def decorator(func):
            step_name = name or func.__name__
            params = inspect.signature(func).parameters
            assert not params, "steps take no parameters in this design"
            assert config is not None, "every step sets an explicit config"
            self.configs[step_name] = config

            async def wrapper():
                if step_name in self.cache:
                    return json.loads(self.cache[step_name])
                limit = config["retries"]["limit"]
                attempt = 0
                while True:
                    attempt += 1
                    self.attempts[step_name] += 1
                    try:
                        result = await func()
                        break
                    except FakeNonRetryableError:
                        raise
                    except Exception:
                        if attempt > limit:
                            raise
                encoded = json.dumps(result)
                assert len(encoded.encode()) < MAX_STEP_RESULT_BYTES
                self.cache[step_name] = encoded
                self.executed.append(step_name)
                if self.crash_after == step_name:
                    raise SimulatedCrash(step_name)
                return json.loads(encoded)

            return wrapper

        return decorator
