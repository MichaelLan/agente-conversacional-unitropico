"""Apply the D1 migration to in-memory SQLite and verify its invariants.

Run (from repo root):
    uv run --no-project --python 3.14 python -m unittest discover -s infra/d1/tests -v
"""

import json
import re
import sqlite3
import unittest
from hashlib import sha256
from pathlib import Path

from fts_query import build_fts5_query

D1_DIR = Path(__file__).resolve().parents[1]
MIGRATIONS = sorted((D1_DIR / "migrations").glob("[0-9][0-9][0-9][0-9]_*.sql"))
QUERIES_FILE = D1_DIR / "queries.sql"


def load_queries() -> dict[str, str]:
    """Split queries.sql into {name: statement} using '-- name:' markers."""
    queries: dict[str, str] = {}
    name = None
    for line in QUERIES_FILE.read_text(encoding="utf-8").splitlines():
        if line.startswith("-- name:"):
            name = line.removeprefix("-- name:").strip()
            queries[name] = ""
        elif name is not None:
            queries[name] += line + "\n"
    return {key: value.strip() for key, value in queries.items()}


QUERIES = load_queries()


def digest(text: str) -> str:
    return sha256(text.encode("utf-8")).hexdigest()


class SchemaTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.db = sqlite3.connect(":memory:", autocommit=True)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys = ON")  # D1 enforces FKs by default
        for migration in MIGRATIONS:
            self.db.executescript(migration.read_text(encoding="utf-8"))

    def tearDown(self) -> None:
        self.db.close()

    # -- helpers ---------------------------------------------------------------
    def add_document(self, doc_id: str = "doc-1") -> str:
        self.db.execute(
            "INSERT INTO documents (id, title, document_type) VALUES (?, ?, ?)",
            (doc_id, f"Documento {doc_id}", "resolucion"),
        )
        return doc_id

    def add_version(
        self, version_id: str = "ver-1", doc_id: str = "doc-1", page_count: int = 10
    ) -> str:
        self.db.execute(
            QUERIES["insert_version_idempotent"],
            (
                version_id,
                doc_id,
                None,
                f"sources/{version_id}.pdf",
                digest(version_id),
                1000,
            ),
        )
        self.db.execute(
            "UPDATE document_versions SET page_count = ? WHERE id = ?",
            (page_count, version_id),
        )
        return version_id

    def to_ready(self, version_id: str) -> None:
        for status in ("processing", "extracted", "review_required"):
            self.set_status(version_id, status)
        self.db.execute(
            "UPDATE document_versions SET status = 'ready', requires_review = 0,"
            " extraction_r2_key = ?, chunker_version = 'structural-v1' WHERE id = ?",
            (f"extractions/{version_id}.json", version_id),
        )

    def publish(self, version_id: str) -> None:
        self.db.execute(
            "UPDATE document_versions SET status = 'published',"
            " published_at = '2026-11-18T00:00:00.000Z' WHERE id = ?",
            (version_id,),
        )

    def set_status(self, version_id: str, status: str) -> None:
        self.db.execute(
            "UPDATE document_versions SET status = ? WHERE id = ?", (status, version_id)
        )

    def add_chunk(
        self,
        chunk_id: str,
        text: str,
        version_id: str = "ver-1",
        ordinal: int | None = None,
        section: str | None = None,
        pages: tuple[int, int] = (1, 1),
    ) -> None:
        if ordinal is None:
            ordinal = self.db.execute(
                "SELECT count(*) FROM chunks WHERE version_id = ?", (version_id,)
            ).fetchone()[0]
        self.db.execute(
            "INSERT INTO chunks (id, version_id, ordinal, text, text_sha256, section,"
            " page_start, page_end, token_estimate) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (chunk_id, version_id, ordinal, text, digest(text), section, *pages, 10),
        )

    def published_corpus(self, chunks: list[tuple[str, str]]) -> None:
        self.add_document()
        self.add_version()
        for chunk_id, text in chunks:
            self.add_chunk(chunk_id, text)
        self.to_ready("ver-1")
        self.publish("ver-1")

    def fts_ids(self, expression: str) -> set[str]:
        rows = self.db.execute(
            "SELECT c.id FROM chunks_fts JOIN chunks c ON c.seq = chunks_fts.rowid"
            " WHERE chunks_fts MATCH ?",
            (expression,),
        )
        return {row[0] for row in rows}

    def search(self, question: str, limit: int = 10) -> list[str]:
        expression = build_fts5_query(question)
        if expression is None:  # adapters skip lexical search, never MATCH NULL
            return []
        rows = self.db.execute(QUERIES["search_chunks"], (expression, limit))
        return [row["chunk_id"] for row in rows]

    def assert_integrity_error(self, sql: str, params: tuple = ()) -> None:
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.execute(sql, params)

    def assert_fts_consistent(self) -> None:
        self.db.execute(QUERIES["fts_integrity_check"])


class MigrationTests(SchemaTestCase):
    def test_single_migration_in_wrangler_naming(self) -> None:
        self.assertEqual([path.name for path in MIGRATIONS], ["0001_initial.sql"])

    def test_migration_has_no_explicit_transactions(self) -> None:
        code = "\n".join(
            line.split("--", 1)[0]
            for line in MIGRATIONS[0].read_text(encoding="utf-8").splitlines()
        )
        pattern = r"\b(BEGIN\s+(DEFERRED|IMMEDIATE|EXCLUSIVE|TRANSACTION)|COMMIT|ROLLBACK|END\s+TRANSACTION)\b"
        self.assertIsNone(re.search(pattern, code, re.IGNORECASE))

    def test_tokenizer_declared(self) -> None:
        sql = self.db.execute(
            "SELECT sql FROM sqlite_master WHERE name = 'chunks_fts'"
        ).fetchone()[0]
        self.assertIn("tokenize = 'unicode61 remove_diacritics 2'", sql)

    def test_foreign_key_check_clean(self) -> None:
        self.published_corpus([("c1", "texto")])
        self.assertEqual(self.db.execute("PRAGMA foreign_key_check").fetchall(), [])

    def test_all_queries_prepare(self) -> None:
        self.assertGreaterEqual(len(QUERIES), 10)
        for name, sql in QUERIES.items():
            with self.subTest(name=name):
                arity = max((int(n) for n in re.findall(r"\?(\d+)", sql)), default=0)
                self.db.execute(f"EXPLAIN {sql.rstrip(';')}", (None,) * arity)


class FtsDiacriticsTests(SchemaTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.published_corpus(
            [
                ("accent", "Fechas de inscripción a la convocatoria 2026-1"),
                ("ocr", "Requisitos de inscripcion y documentos soporte"),
                ("other", "Pago del apoyo socioeconómico de alimentación"),
            ]
        )

    def test_unaccented_query_matches_accented_text(self) -> None:
        self.assertEqual(self.fts_ids('"inscripcion"'), {"accent", "ocr"})

    def test_accented_query_matches_unaccented_text(self) -> None:
        self.assertEqual(self.fts_ids('"inscripción"'), {"accent", "ocr"})

    def test_case_and_accent_insensitive(self) -> None:
        self.assertEqual(self.fts_ids('"INSCRIPCIÓN"'), {"accent", "ocr"})
        self.assertEqual(self.fts_ids('"socioeconomico"'), {"other"})
        self.assertEqual(self.fts_ids('"alimentacion"'), {"other"})

    def test_decomposed_unicode_query_via_builder(self) -> None:
        nfd = "inscripcio\u0301n"
        self.assertEqual(set(self.search(nfd)), {"accent", "ocr"})

    def test_n_tilde_folds_to_n(self) -> None:
        self.add_document("doc-2")
        self.add_version("ver-2", "doc-2")
        self.add_chunk("anio", "el año académico", version_id="ver-2")
        self.assertIn("anio", self.fts_ids('"ano"'))

    def test_section_column_is_searchable(self) -> None:
        self.add_document("doc-2")
        self.add_version("ver-2", "doc-2")
        self.add_chunk(
            "sec",
            "texto sin la palabra",
            version_id="ver-2",
            section="Artículo 5 Cronograma",
        )
        self.assertEqual(self.fts_ids("section:cronograma"), {"sec"})


class FtsTriggerTests(SchemaTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.add_document()
        self.add_version()
        self.add_chunk("c1", "beca de alimentación", section="Capítulo I")
        self.add_chunk("c2", "renta joven")

    def test_insert_indexes(self) -> None:
        self.assertEqual(self.fts_ids("beca"), {"c1"})
        self.assert_fts_consistent()

    def test_update_text_reindexes(self) -> None:
        self.db.execute(
            "UPDATE chunks SET text = 'subsidio de transporte', text_sha256 = ? WHERE id = 'c1'",
            (digest("subsidio de transporte"),),
        )
        self.assertEqual(self.fts_ids("beca"), set())
        self.assertEqual(self.fts_ids("transporte"), {"c1"})
        self.assert_fts_consistent()

    def test_update_section_reindexes(self) -> None:
        self.db.execute("UPDATE chunks SET section = 'Capítulo II' WHERE id = 'c1'")
        self.assertEqual(self.fts_ids('section:"capitulo ii"'), {"c1"})
        self.assertEqual(self.fts_ids('section:"capitulo i"'), set())
        self.assert_fts_consistent()

    def test_update_non_indexed_column_keeps_index(self) -> None:
        self.db.execute("UPDATE chunks SET embedding_model = 'm' WHERE id = 'c1'")
        self.assertEqual(self.fts_ids("beca"), {"c1"})
        self.assert_fts_consistent()

    def test_delete_removes_from_index(self) -> None:
        self.db.execute("DELETE FROM chunks WHERE id = 'c1'")
        self.assertEqual(self.fts_ids("beca"), set())
        self.assertEqual(self.fts_ids("renta"), {"c2"})
        self.assert_fts_consistent()

    def test_cascade_delete_of_unpublished_version_cleans_index(self) -> None:
        self.db.execute("DELETE FROM document_versions WHERE id = 'ver-1'")
        self.assertEqual(
            self.db.execute("SELECT count(*) FROM chunks").fetchone()[0], 0
        )
        self.assertEqual(self.fts_ids("beca OR renta"), set())
        self.assert_fts_consistent()

    def test_events_block_version_deletion(self) -> None:
        self.db.execute(
            QUERIES["log_event"], ("ver-1", "upload", "succeeded", None, None)
        )
        self.assert_integrity_error("DELETE FROM document_versions WHERE id = 'ver-1'")

    def test_rebuild_is_consistent(self) -> None:
        self.db.execute(QUERIES["fts_rebuild"])
        self.assert_fts_consistent()
        self.assertEqual(self.fts_ids("beca"), {"c1"})


class CheckConstraintTests(SchemaTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.add_document()
        self.add_version()

    def chunk_sql(self) -> str:
        return (
            "INSERT INTO chunks (id, version_id, ordinal, text, text_sha256, section,"
            " page_start, page_end, token_estimate) VALUES (?, 'ver-1', ?, ?, ?, ?, ?, ?, ?)"
        )

    def test_page_end_before_start_rejected(self) -> None:
        self.assert_integrity_error(
            self.chunk_sql(), ("x", 0, "t", digest("t"), None, 3, 2, 1)
        )

    def test_page_zero_rejected(self) -> None:
        self.assert_integrity_error(
            self.chunk_sql(), ("x", 0, "t", digest("t"), None, 0, 1, 1)
        )

    def test_page_range_crossing_pages_allowed(self) -> None:
        self.db.execute(
            self.chunk_sql(), ("x", 0, "tabla", digest("tabla"), None, 3, 4, 1)
        )

    def test_page_beyond_page_count_rejected(self) -> None:
        with self.assertRaisesRegex(sqlite3.IntegrityError, "chunk_page_out_of_range"):
            self.db.execute(
                self.chunk_sql(), ("x", 0, "t", digest("t"), None, 10, 11, 1)
            )

    def test_bad_hashes_rejected(self) -> None:
        for bad in ("A" * 64, "a" * 63, "g" * 64):
            with self.subTest(bad=bad):
                self.assert_integrity_error(
                    self.chunk_sql(), ("x", 0, "t", bad, None, 1, 1, 1)
                )

    def test_chunk_id_byte_limit(self) -> None:
        ok, too_long = "ñ" * 32, "ñ" * 33  # 64 and 66 UTF-8 bytes
        self.db.execute(self.chunk_sql(), (ok, 0, "t", digest("t"), None, 1, 1, 1))
        self.assert_integrity_error(
            self.chunk_sql(), (too_long, 1, "t", digest("t"), None, 1, 1, 1)
        )
        self.assert_integrity_error(
            self.chunk_sql(), ("", 1, "t", digest("t"), None, 1, 1, 1)
        )

    def test_blank_text_and_section_rejected(self) -> None:
        self.assert_integrity_error(
            self.chunk_sql(), ("x", 0, "  ", digest("  "), None, 1, 1, 1)
        )
        self.assert_integrity_error(
            self.chunk_sql(), ("x", 0, "t", digest("t"), " ", 1, 1, 1)
        )

    def test_strict_types(self) -> None:
        self.assert_integrity_error(
            self.chunk_sql(), ("x", 0, "t", digest("t"), None, "uno", 1, 1)
        )

    def test_unknown_status_rejected(self) -> None:
        self.assert_integrity_error(
            "UPDATE document_versions SET status = 'deleted' WHERE id = 'ver-1'"
        )

    def test_failed_requires_error_code(self) -> None:
        self.assert_integrity_error(
            "UPDATE document_versions SET status = 'failed' WHERE id = 'ver-1'"
        )
        self.db.execute(
            "UPDATE document_versions SET status = 'failed', error_code = 'ocr_timeout'"
            " WHERE id = 'ver-1'"
        )

    def test_dates_validated(self) -> None:
        for bad in ("2026-13-01", "18/11/2026", "2026-11-18T00:00:00"):
            with self.subTest(bad=bad):
                self.assert_integrity_error(
                    "UPDATE document_versions SET effective_from = ? WHERE id = 'ver-1'",
                    (bad,),
                )
        self.assert_integrity_error(
            "UPDATE document_versions SET effective_from = '2026-02-01',"
            " effective_until = '2026-01-31' WHERE id = 'ver-1'"
        )

    def test_self_supersession_rejected(self) -> None:
        self.assert_integrity_error(
            "UPDATE document_versions SET supersedes_version_id = 'ver-1' WHERE id = 'ver-1'"
        )

    def test_dangling_supersession_rejected(self) -> None:
        self.assert_integrity_error(
            "UPDATE document_versions SET supersedes_version_id = 'nope' WHERE id = 'ver-1'"
        )

    def test_failed_event_requires_error_code(self) -> None:
        self.assert_integrity_error(
            QUERIES["log_event"], ("ver-1", "extract", "failed", None, None)
        )
        self.assert_integrity_error(
            QUERIES["log_event"], ("ver-1", "magic", "started", None, None)
        )

    def test_events_are_append_only(self) -> None:
        self.db.execute(
            QUERIES["log_event"], ("ver-1", "extract", "failed", "ocr_timeout", "t")
        )
        with self.assertRaisesRegex(sqlite3.IntegrityError, "append_only"):
            self.db.execute("UPDATE processing_events SET detail = 'x'")
        with self.assertRaisesRegex(
            sqlite3.IntegrityError, "processing_events_append_only"
        ):
            self.db.execute("DELETE FROM processing_events")
        count = self.db.execute("SELECT COUNT(*) FROM processing_events").fetchone()[0]
        self.assertEqual(count, 1)

    def test_updated_at_is_touched(self) -> None:
        self.db.execute(
            "UPDATE document_versions SET updated_at = '2000-01-01T00:00:00.000Z'"
        )
        self.db.execute("UPDATE document_versions SET processing_attempts = 1")
        updated = self.db.execute(
            "SELECT updated_at FROM document_versions"
        ).fetchone()[0]
        self.assertGreater(updated, "2026")


class IdempotencyTests(SchemaTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.add_document()
        self.add_version()

    def test_same_bytes_same_document_is_noop(self) -> None:
        self.db.execute(
            QUERIES["insert_version_idempotent"],
            ("ver-dup", "doc-1", None, "sources/other-key.pdf", digest("ver-1"), 1000),
        )
        rows = self.db.execute(
            QUERIES["version_by_source"], ("doc-1", digest("ver-1"))
        ).fetchall()
        self.assertEqual([row["id"] for row in rows], ["ver-1"])
        self.assertEqual(
            self.db.execute("SELECT count(*) FROM document_versions").fetchone()[0], 1
        )

    def test_plain_duplicate_insert_rejected(self) -> None:
        self.assert_integrity_error(
            "INSERT INTO document_versions (id, document_id, source_r2_key, source_sha256,"
            " source_bytes) VALUES ('ver-dup', 'doc-1', 'k2', ?, 1)",
            (digest("ver-1"),),
        )

    def test_same_r2_key_rejected(self) -> None:
        self.assert_integrity_error(
            "INSERT INTO document_versions (id, document_id, source_r2_key, source_sha256,"
            " source_bytes) VALUES ('ver-2', 'doc-1', 'sources/ver-1.pdf', ?, 1)",
            (digest("x"),),
        )

    def test_version_label_unique_per_document(self) -> None:
        self.db.execute("UPDATE document_versions SET version_label = 'v1'")
        self.add_version("ver-2")
        self.assert_integrity_error(
            "UPDATE document_versions SET version_label = 'v1' WHERE id = 'ver-2'"
        )

    def test_chunk_ordinal_and_id_unique(self) -> None:
        self.add_chunk("c1", "a", ordinal=0)
        self.assert_integrity_error(
            "INSERT INTO chunks (id, version_id, ordinal, text, text_sha256, page_start,"
            " page_end, token_estimate) VALUES ('c2', 'ver-1', 0, 'b', ?, 1, 1, 1)",
            (digest("b"),),
        )
        self.assert_integrity_error(
            "INSERT INTO chunks (id, version_id, ordinal, text, text_sha256, page_start,"
            " page_end, token_estimate) VALUES ('c1', 'ver-1', 1, 'b', ?, 1, 1, 1)",
            (digest("b"),),
        )

    def test_rechunk_is_delete_then_insert(self) -> None:
        self.add_chunk("c1", "versión inicial")
        self.db.execute("DELETE FROM chunks WHERE version_id = 'ver-1'")
        self.add_chunk("c1", "versión corregida")
        self.assertEqual(self.fts_ids("corregida"), {"c1"})
        self.assertEqual(self.fts_ids("inicial"), set())
        self.assert_fts_consistent()


class LifecycleTests(SchemaTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.add_document()
        self.add_version()

    def test_initial_status_must_be_uploaded(self) -> None:
        with self.assertRaisesRegex(sqlite3.IntegrityError, "invalid_initial_status"):
            self.db.execute(
                "INSERT INTO document_versions (id, document_id, source_r2_key, source_sha256,"
                " source_bytes, status) VALUES ('v9', 'doc-1', 'k9', ?, 1, 'ready')",
                (digest("k9"),),
            )

    def test_cannot_skip_to_published(self) -> None:
        with self.assertRaisesRegex(
            sqlite3.IntegrityError, "invalid_status_transition"
        ):
            self.set_status("ver-1", "published")

    def test_publish_requires_review_cleared(self) -> None:
        for status in ("processing", "extracted", "review_required"):
            self.set_status("ver-1", status)
        self.db.execute(
            "UPDATE document_versions SET status = 'ready', extraction_r2_key = 'e',"
            " chunker_version = 'v1' WHERE id = 'ver-1'"
        )
        self.add_chunk("c1", "texto")
        self.assert_integrity_error(
            "UPDATE document_versions SET status = 'published', published_at = 'x'"
        )

    def test_publish_requires_chunks(self) -> None:
        self.to_ready("ver-1")
        with self.assertRaisesRegex(sqlite3.IntegrityError, "publish_without_chunks"):
            self.publish("ver-1")

    def test_publish_rechecks_page_range(self) -> None:
        self.db.execute("UPDATE document_versions SET page_count = NULL")
        self.add_chunk("c1", "texto", pages=(5, 12))
        self.to_ready("ver-1")
        self.db.execute("UPDATE document_versions SET page_count = 10")
        with self.assertRaisesRegex(sqlite3.IntegrityError, "chunk_page_out_of_range"):
            self.publish("ver-1")

    def test_published_chunks_immutable(self) -> None:
        self.add_chunk("c1", "texto")
        self.to_ready("ver-1")
        self.publish("ver-1")
        for sql in (
            "UPDATE chunks SET section = 'x'",
            "DELETE FROM chunks",
        ):
            with (
                self.subTest(sql=sql),
                self.assertRaisesRegex(sqlite3.IntegrityError, "immutable"),
            ):
                self.db.execute(sql)
        with self.assertRaisesRegex(sqlite3.IntegrityError, "immutable"):
            self.add_chunk("c2", "otro")
        with self.assertRaisesRegex(sqlite3.IntegrityError, "immutable"):
            self.db.execute(
                "UPDATE document_versions SET source_sha256 = ?", (digest("z"),)
            )

    def test_unpublish_allows_edit_and_republish(self) -> None:
        self.add_chunk("c1", "texto")
        self.to_ready("ver-1")
        self.publish("ver-1")
        self.set_status("ver-1", "unpublished")
        self.db.execute("UPDATE chunks SET section = 'Artículo 1'")
        self.publish("ver-1")
        self.assertEqual(self.search("texto"), ["c1"])

    def test_failure_and_retry(self) -> None:
        self.set_status("ver-1", "processing")
        self.db.execute(
            "UPDATE document_versions SET status = 'failed', error_code = 'ocr_timeout',"
            " processing_attempts = processing_attempts + 1"
        )
        self.set_status("ver-1", "processing")
        row = self.db.execute(
            "SELECT status, processing_attempts FROM document_versions"
        ).fetchone()
        self.assertEqual(tuple(row), ("processing", 1))


class RetrievalQueryTests(SchemaTestCase):
    def test_bm25_orders_by_relevance(self) -> None:
        self.published_corpus(
            [
                (
                    "weak",
                    (
                        "La universidad publica información general sobre muchos temas"
                        " administrativos, académicos y financieros, incluida una mención"
                        " de la beca."
                    ),
                ),
                ("strong", "Beca de alimentación: requisitos de la beca."),
                ("none", "Calendario académico del semestre."),
                ("filler", "Información general de la universidad."),
            ]
        )
        self.assertEqual(
            self.search("¿Cuáles son los requisitos de la beca?"), ["strong", "weak"]
        )
        scores = [
            row["score"]
            for row in self.db.execute(
                QUERIES["search_chunks"], (build_fts5_query("beca requisitos"), 10)
            )
        ]
        self.assertEqual(scores, sorted(scores))
        self.assertLess(scores[0], 0)

    def test_limit_parameter(self) -> None:
        self.published_corpus([(f"c{i}", f"beca número {i}") for i in range(5)])
        self.assertEqual(len(self.search("beca", limit=3)), 3)

    def test_only_published_versions_are_searchable(self) -> None:
        self.published_corpus([("pub", "beca publicada")])
        self.add_version("ver-2")
        self.add_chunk("draft", "beca en borrador", version_id="ver-2")
        self.assertEqual(self.search("beca"), ["pub"])

    def test_supersession_requires_published_successor(self) -> None:
        self.published_corpus([("old", "beca 2025")])
        self.add_document("doc-2")
        self.add_version("ver-2", "doc-2")
        self.db.execute(
            "UPDATE document_versions SET supersedes_version_id = 'ver-1' WHERE id = 'ver-2'"
        )
        self.add_chunk("new", "beca 2026", version_id="ver-2")
        self.to_ready("ver-2")
        self.assertEqual(set(self.search("beca")), {"old"})
        self.publish("ver-2")
        self.assertEqual(self.search("beca"), ["new"])
        old = self.db.execute(QUERIES["registry_by_chunk_id"], ("old",)).fetchone()
        self.assertEqual((old["status"], old["formally_superseded"]), ("published", 1))

    def test_in_force_filter_uses_explicit_local_date(self) -> None:
        self.published_corpus([("c1", "convocatoria beca")])
        self.db.execute(
            "UPDATE document_versions SET effective_from = '2026-01-15',"
            " effective_until = '2026-06-30'"
        )
        sql = QUERIES["search_chunks_in_force"]
        expr = build_fts5_query("convocatoria")
        hits = {
            d: [r[0] for r in self.db.execute(sql, (expr, 10, d))]
            for d in ("2026-01-14", "2026-01-15", "2026-06-30", "2026-07-01")
        }
        self.assertEqual(
            hits,
            {
                "2026-01-14": [],
                "2026-01-15": ["c1"],
                "2026-06-30": ["c1"],
                "2026-07-01": [],
            },
        )
        # Expired versions remain in the formal search: the policy is the caller's.
        self.assertEqual(self.search("convocatoria"), ["c1"])
        in_force = self.db.execute(
            QUERIES["published_versions_in_force"], ("2026-03-01",)
        ).fetchall()
        self.assertEqual([r["version_id"] for r in in_force], ["ver-1"])

    def test_registry_batch_read_with_single_parameter(self) -> None:
        self.add_document()
        self.add_version(page_count=500)
        ids = [f"chunk-{i:03d}" for i in range(250)]
        for i, chunk_id in enumerate(ids):
            self.add_chunk(chunk_id, f"texto {i}", ordinal=i, pages=(i + 1, i + 2))
        rows = self.db.execute(
            QUERIES["registry_by_chunk_ids"], (json.dumps([*ids[:100], "missing"]),)
        ).fetchall()
        self.assertEqual(len(rows), 100)
        row = next(r for r in rows if r["chunk_id"] == "chunk-007")
        self.assertEqual(
            (
                row["document_title"],
                row["page_start"],
                row["page_end"],
                row["text_sha256"],
            ),
            ("Documento doc-1", 8, 9, digest("texto 7")),
        )
        self.assertEqual(
            (row["status"], row["requires_review"], row["formally_superseded"]),
            ("uploaded", 1, 0),
        )
        plan = " ".join(
            r[3]
            for r in self.db.execute(
                "EXPLAIN QUERY PLAN " + QUERIES["registry_by_chunk_ids"].rstrip(";"),
                ("[]",),
            )
        )
        self.assertIn("USING INDEX sqlite_autoindex_chunks_1", plan)
        self.assertNotIn("SCAN c", plan)

    def test_catalog_by_version_ids(self) -> None:
        self.published_corpus([("c1", "texto")])
        rows = self.db.execute(
            QUERIES["catalog_by_version_ids"], ('["ver-1", "x"]',)
        ).fetchall()
        self.assertEqual(
            [(r["version_id"], r["document_title"]) for r in rows],
            [("ver-1", "Documento doc-1")],
        )

    def test_adversarial_questions_never_break_match(self) -> None:
        self.published_corpus([("c1", "beca NEAR alimentación")])
        for question in (
            'beca" OR 1=1 --',
            "NEAR(beca alimentación, 2)",
            "beca*",
            "^beca",
            "text:beca",
            "beca AND NOT",
            "(beca",
            "beca)",
            "-beca",
            "+beca",
            "beca; DROP TABLE chunks",
            "{text section}: beca",
            "beca_alimentación",
        ):
            with self.subTest(question=question):
                self.assertEqual(self.search(question), ["c1"])
        # FTS5 keywords become plain quoted terms.
        self.assertEqual(self.search("OR AND NOT NEAR"), ["c1"])
        for question in ('"""', "¿?", "  ", "--"):
            with self.subTest(question=question):
                self.assertIsNone(build_fts5_query(question))


class TextCorrectionTests(SchemaTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.add_document()
        self.add_version(page_count=20)

    def insert(
        self,
        correction_id: str = "cor-1",
        page: int = 18,
        find: str = "igual a 3.0",
        replace: str = "igual o superior a 3.0",
        reason: str = "El PDF original dice igual o superior",
        version_id: str = "ver-1",
    ) -> None:
        self.db.execute(
            QUERIES["insert_correction"],
            (correction_id, version_id, page, find, replace, reason),
        )

    def active(self) -> list[str]:
        rows = self.db.execute(QUERIES["active_corrections_by_version"], ("ver-1",))
        return [row["id"] for row in rows]

    def test_insert_and_list(self) -> None:
        self.insert()
        rows = self.db.execute(QUERIES["corrections_by_version"], ("ver-1",)).fetchall()
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(
            (row["page_number"], row["find_text"], row["replace_text"]),
            (18, "igual a 3.0", "igual o superior a 3.0"),
        )
        self.assertIsNone(row["deleted_at"])
        self.assertRegex(row["created_at"], r"^\d{4}-\d{2}-\d{2}T")
        self.assertEqual(self.active(), ["cor-1"])

    def test_empty_replacement_allowed(self) -> None:
        self.insert(find="~~ruido~~", replace="")
        self.assertEqual(self.active(), ["cor-1"])

    def test_check_constraints(self) -> None:
        cases = {
            "page_zero": {"page": 0},
            "blank_find": {"find": "   "},
            "long_find": {"find": "x" * 501},
            "long_replace": {"replace": "x" * 501},
            "blank_reason": {"reason": " "},
            "long_reason": {"reason": "x" * 301},
            "noop": {"find": "igual", "replace": "igual"},
            "bad_id": {"correction_id": " cor"},
            "unknown_version": {"version_id": "nope"},
        }
        for name, kwargs in cases.items():
            with self.subTest(name=name), self.assertRaises(sqlite3.IntegrityError):
                self.insert(**kwargs)
        self.insert(find="x" * 500, replace="y" * 500, reason="r" * 300)

    def test_strict_page_number(self) -> None:
        with self.assertRaises(sqlite3.IntegrityError):
            self.insert(page="dieciocho")  # type: ignore[arg-type]

    def test_page_beyond_known_page_count_rejected(self) -> None:
        with self.assertRaisesRegex(
            sqlite3.IntegrityError, "correction_page_out_of_range"
        ):
            self.insert(page=21)
        self.db.execute("UPDATE document_versions SET page_count = NULL")
        self.insert(page=21)  # unknown page count: checked by the ingestion

    def test_soft_delete_once_and_keeps_row(self) -> None:
        self.insert()
        self.insert("cor-2", page=3, find="otro", replace="otra")
        deleted = self.db.execute(
            QUERIES["soft_delete_correction"], ("cor-1", "ver-1")
        ).fetchall()
        self.assertEqual([row["id"] for row in deleted], ["cor-1"])
        again = self.db.execute(
            QUERIES["soft_delete_correction"], ("cor-1", "ver-1")
        ).fetchall()
        self.assertEqual(again, [])
        other_version = self.db.execute(
            QUERIES["soft_delete_correction"], ("cor-2", "ver-x")
        ).fetchall()
        self.assertEqual(other_version, [])
        self.assertEqual(self.active(), ["cor-2"])
        rows = self.db.execute(QUERIES["corrections_by_version"], ("ver-1",)).fetchall()
        self.assertEqual([r["id"] for r in rows], ["cor-1", "cor-2"])
        self.assertGreaterEqual(rows[0]["deleted_at"], rows[0]["created_at"])

    def test_rows_are_immutable_and_never_deleted(self) -> None:
        self.insert()
        for sql in (
            "UPDATE text_corrections SET find_text = 'otro'",
            "UPDATE text_corrections SET replace_text = 'otro'",
            "UPDATE text_corrections SET reason = 'otro'",
            "UPDATE text_corrections SET page_number = 2",
            "UPDATE text_corrections SET created_at = '2000-01-01T00:00:00.000Z'",
        ):
            with (
                self.subTest(sql=sql),
                self.assertRaisesRegex(
                    sqlite3.IntegrityError, "text_correction_immutable"
                ),
            ):
                self.db.execute(sql)
        self.db.execute(QUERIES["soft_delete_correction"], ("cor-1", "ver-1"))
        with self.assertRaisesRegex(sqlite3.IntegrityError, "immutable"):
            self.db.execute("UPDATE text_corrections SET deleted_at = NULL")
        with self.assertRaisesRegex(sqlite3.IntegrityError, "append_only"):
            self.db.execute("DELETE FROM text_corrections")

    def test_corrections_block_version_deletion(self) -> None:
        self.insert()
        self.assert_integrity_error("DELETE FROM document_versions WHERE id = 'ver-1'")

    def test_published_version_refuses_changes(self) -> None:
        self.insert()
        self.add_chunk("c1", "texto")
        self.to_ready("ver-1")
        # The ingestion chunk step applied the correction (see clear_corrections_dirty).
        self.db.execute("UPDATE document_versions SET corrections_dirty = 0")
        self.publish("ver-1")
        with self.assertRaisesRegex(sqlite3.IntegrityError, "immutable"):
            self.insert("cor-2", page=2)
        with self.assertRaisesRegex(sqlite3.IntegrityError, "immutable"):
            self.db.execute(QUERIES["soft_delete_correction"], ("cor-1", "ver-1"))
        self.set_status("ver-1", "unpublished")
        self.db.execute(QUERIES["soft_delete_correction"], ("cor-1", "ver-1"))
        self.assertEqual(self.active(), [])

    def test_active_order_is_creation_then_insertion(self) -> None:
        same = "2030-01-01T00:00:00.000Z"
        for cid, find in (("cor-z", "z"), ("cor-b", "b"), ("cor-a", "a")):
            self.db.execute(
                "INSERT INTO text_corrections (id, version_id, page_number, find_text,"
                " replace_text, reason, created_at) VALUES (?, 'ver-1', 1, ?, 'X',"
                " 'r', ?)",
                (cid, find, same),
            )
        self.db.execute(
            "INSERT INTO text_corrections (id, version_id, page_number, find_text,"
            " replace_text, reason, created_at) VALUES"
            " ('cor-0', 'ver-1', 1, 'c', 'C', 'r', '2000-01-01T00:00:00.000Z')"
        )
        # Ties on created_at follow insertion order (rowid), not the id.
        self.assertEqual(self.active(), ["cor-0", "cor-z", "cor-b", "cor-a"])
        rows = self.db.execute(QUERIES["corrections_by_version"], ("ver-1",))
        self.assertEqual([r["id"] for r in rows], ["cor-0", "cor-z", "cor-b", "cor-a"])

    def test_insert_returns_id_and_is_capped_at_100_active(self) -> None:
        for n in range(99):
            self.insert(f"cor-{n}", page=1, find=f"texto {n}")
        self.insert("cor-del", page=1, find="borrada")
        self.db.execute(QUERIES["soft_delete_correction"], ("cor-del", "ver-1"))
        returned = self.db.execute(
            QUERIES["insert_correction"],
            ("cor-99", "ver-1", 1, "texto 99", "x", "r"),
        ).fetchall()
        self.assertEqual([r["id"] for r in returned], ["cor-99"])
        refused = self.db.execute(
            QUERIES["insert_correction"],
            ("cor-100", "ver-1", 1, "texto 100", "x", "r"),
        ).fetchall()
        self.assertEqual(refused, [])
        self.assertEqual(len(self.active()), 100)

    def test_active_duplicate_rejected_deleted_does_not_block(self) -> None:
        self.insert()
        with self.assertRaisesRegex(sqlite3.IntegrityError, "UNIQUE"):
            self.insert("cor-2", replace="otra cosa")
        self.insert("cor-3", page=17)  # other page
        self.db.execute(QUERIES["soft_delete_correction"], ("cor-1", "ver-1"))
        self.insert("cor-4")
        self.assertEqual(self.active(), ["cor-3", "cor-4"])

    def dirty(self) -> tuple[int, int]:
        row = self.db.execute(
            "SELECT corrections_dirty, corrections_rev FROM document_versions"
            " WHERE id = 'ver-1'"
        ).fetchone()
        return row[0], row[1]

    def test_create_and_delete_mark_dirty_and_bump_revision(self) -> None:
        self.assertEqual(self.dirty(), (0, 0))
        self.insert()
        self.assertEqual(self.dirty(), (1, 1))
        self.db.execute("UPDATE document_versions SET corrections_dirty = 0")
        self.db.execute(QUERIES["soft_delete_correction"], ("cor-1", "ver-1"))
        self.assertEqual(self.dirty(), (1, 2))
        # A no-op soft delete (already deleted) changes nothing.
        self.db.execute("UPDATE document_versions SET corrections_dirty = 0")
        self.db.execute(QUERIES["soft_delete_correction"], ("cor-1", "ver-1"))
        self.assertEqual(self.dirty(), (0, 2))
        # A refused insert (cap or trigger) rolls back the bump too.
        with self.assertRaises(sqlite3.IntegrityError):
            self.insert("cor-2", page=99)
        self.assertEqual(self.dirty(), (0, 2))

    def test_clear_dirty_is_compare_and_set_on_revision(self) -> None:
        self.insert()
        for status in ("processing", "extracted"):
            self.set_status("ver-1", status)
        (rev,) = self.db.execute(
            QUERIES["corrections_rev_by_version"], ("ver-1",)
        ).fetchone()
        self.insert("cor-2", page=2)  # created after the chunk step read
        self.db.execute(QUERIES["clear_corrections_dirty"], ("ver-1", rev))
        self.assertEqual(self.dirty(), (1, 2))
        self.db.execute(QUERIES["clear_corrections_dirty"], ("ver-1", 2))
        self.assertEqual(self.dirty(), (0, 2))

    def test_clear_dirty_only_while_extracted(self) -> None:
        self.insert()
        self.db.execute(QUERIES["clear_corrections_dirty"], ("ver-1", 1))
        self.assertEqual(self.dirty(), (1, 1))

    def test_dirty_version_cannot_be_published(self) -> None:
        self.add_chunk("c1", "texto")
        self.insert()
        self.to_ready("ver-1")
        with self.assertRaisesRegex(sqlite3.IntegrityError, "corrections_pending"):
            self.publish("ver-1")
        self.db.execute("UPDATE document_versions SET corrections_dirty = 0")
        self.publish("ver-1")
        self.set_status("ver-1", "unpublished")
        self.db.execute(QUERIES["soft_delete_correction"], ("cor-1", "ver-1"))
        with self.assertRaisesRegex(sqlite3.IntegrityError, "corrections_pending"):
            self.set_status("ver-1", "published")

    def test_published_check_requires_clean_corrections(self) -> None:
        self.add_chunk("c1", "texto")
        self.to_ready("ver-1")
        self.publish("ver-1")
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.execute("UPDATE document_versions SET corrections_dirty = 1")

    def test_correct_event_step_allowed(self) -> None:
        self.db.execute(
            QUERIES["log_event"],
            ("ver-1", "correct", "succeeded", None, "created correction=cor-1"),
        )

    def test_index_serves_version_lookup(self) -> None:
        plan = " ".join(
            r[3]
            for r in self.db.execute(
                "EXPLAIN QUERY PLAN "
                + QUERIES["active_corrections_by_version"].rstrip(";"),
                ("ver-1",),
            )
        )
        self.assertIn("text_corrections_version", plan)


class PageOverrideTests(SchemaTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.add_document()
        self.add_version(page_count=20)

    def insert(
        self,
        override_id: str = "pov-1",
        page: int = 4,
        text: str = "Texto completo de la página 4",
        reason: str = "Diagrama mal transcrito",
        version_id: str = "ver-1",
    ) -> list[str]:
        rows = self.db.execute(
            QUERIES["insert_override"], (override_id, version_id, page, text, reason)
        ).fetchall()
        return [row["id"] for row in rows]

    def active(self) -> list[tuple[str, int]]:
        rows = self.db.execute(QUERIES["active_overrides_by_version"], ("ver-1",))
        return [(row["id"], row["page_number"]) for row in rows]

    def dirty(self) -> tuple[int, int]:
        row = self.db.execute(
            "SELECT corrections_dirty, corrections_rev FROM document_versions"
            " WHERE id = 'ver-1'"
        ).fetchone()
        return row[0], row[1]

    def test_insert_returns_id_and_lists(self) -> None:
        self.assertEqual(self.insert(), ["pov-1"])
        rows = self.db.execute(QUERIES["overrides_by_version"], ("ver-1",)).fetchall()
        self.assertEqual(len(rows), 1)
        self.assertEqual(
            (rows[0]["page_number"], rows[0]["text"], rows[0]["reason"]),
            (4, "Texto completo de la página 4", "Diagrama mal transcrito"),
        )
        self.assertIsNone(rows[0]["deleted_at"])
        self.assertEqual(self.active(), [("pov-1", 4)])

    def test_check_constraints(self) -> None:
        cases = {
            "page_zero": {"page": 0},
            "blank_text": {"text": "  \n "},
            "long_text": {"text": "x" * 20001},
            "blank_reason": {"reason": " "},
            "long_reason": {"reason": "x" * 301},
            "bad_id": {"override_id": "pov-1 "},
            "unknown_version": {"version_id": "nope"},
        }
        for name, kwargs in cases.items():
            with self.subTest(name=name), self.assertRaises(sqlite3.IntegrityError):
                self.insert(**kwargs)
        self.insert(text="á" * 20000, reason="r" * 300)

    def test_strict_page_number(self) -> None:
        with self.assertRaises(sqlite3.IntegrityError):
            self.insert(page="cuatro")  # type: ignore[arg-type]

    def test_page_beyond_known_page_count_rejected(self) -> None:
        with self.assertRaisesRegex(
            sqlite3.IntegrityError, "override_page_out_of_range"
        ):
            self.insert(page=21)
        self.insert(page=20)
        self.db.execute("UPDATE document_versions SET page_count = NULL")
        self.insert("pov-2", page=21)

    def test_one_active_per_page_deleted_does_not_block(self) -> None:
        self.insert()
        with self.assertRaisesRegex(sqlite3.IntegrityError, "UNIQUE"):
            self.insert("pov-2", text="otro texto")
        self.insert("pov-3", page=5)
        self.db.execute(QUERIES["soft_delete_override"], ("pov-1", "ver-1"))
        self.insert("pov-4")
        self.assertEqual(self.active(), [("pov-4", 4), ("pov-3", 5)])

    def test_soft_delete_once_and_keeps_row(self) -> None:
        self.insert()
        deleted = self.db.execute(
            QUERIES["soft_delete_override"], ("pov-1", "ver-1")
        ).fetchall()
        self.assertEqual([row["id"] for row in deleted], ["pov-1"])
        for params in (("pov-1", "ver-1"), ("pov-1", "ver-x")):
            again = self.db.execute(QUERIES["soft_delete_override"], params).fetchall()
            self.assertEqual(again, [])
        rows = self.db.execute(QUERIES["overrides_by_version"], ("ver-1",)).fetchall()
        self.assertEqual(len(rows), 1)
        self.assertGreaterEqual(rows[0]["deleted_at"], rows[0]["created_at"])
        self.assertEqual(self.active(), [])

    def test_rows_are_immutable_and_never_deleted(self) -> None:
        self.insert()
        for sql in (
            "UPDATE page_overrides SET text = 'otro'",
            "UPDATE page_overrides SET reason = 'otro'",
            "UPDATE page_overrides SET page_number = 2",
            "UPDATE page_overrides SET created_at = '2000-01-01T00:00:00.000Z'",
        ):
            with (
                self.subTest(sql=sql),
                self.assertRaisesRegex(
                    sqlite3.IntegrityError, "page_override_immutable"
                ),
            ):
                self.db.execute(sql)
        self.db.execute(QUERIES["soft_delete_override"], ("pov-1", "ver-1"))
        with self.assertRaisesRegex(sqlite3.IntegrityError, "immutable"):
            self.db.execute("UPDATE page_overrides SET deleted_at = NULL")
        with self.assertRaisesRegex(sqlite3.IntegrityError, "immutable"):
            self.db.execute(
                "UPDATE page_overrides SET deleted_at = '2100-01-01T00:00:00.000Z'"
            )
        with self.assertRaisesRegex(sqlite3.IntegrityError, "append_only"):
            self.db.execute("DELETE FROM page_overrides")

    def test_overrides_block_version_deletion(self) -> None:
        self.insert()
        self.assert_integrity_error("DELETE FROM document_versions WHERE id = 'ver-1'")

    def test_published_version_refuses_changes(self) -> None:
        self.insert()
        self.add_chunk("c1", "texto")
        self.to_ready("ver-1")
        self.db.execute("UPDATE document_versions SET corrections_dirty = 0")
        self.publish("ver-1")
        with self.assertRaisesRegex(sqlite3.IntegrityError, "immutable"):
            self.insert("pov-2", page=2)
        with self.assertRaisesRegex(sqlite3.IntegrityError, "immutable"):
            self.db.execute(QUERIES["soft_delete_override"], ("pov-1", "ver-1"))
        self.set_status("ver-1", "unpublished")
        self.db.execute(QUERIES["soft_delete_override"], ("pov-1", "ver-1"))
        self.assertEqual(self.active(), [])

    def test_create_and_delete_mark_dirty_and_bump_revision(self) -> None:
        self.assertEqual(self.dirty(), (0, 0))
        self.insert()
        self.assertEqual(self.dirty(), (1, 1))
        self.db.execute("UPDATE document_versions SET corrections_dirty = 0")
        self.db.execute(QUERIES["soft_delete_override"], ("pov-1", "ver-1"))
        self.assertEqual(self.dirty(), (1, 2))
        self.db.execute("UPDATE document_versions SET corrections_dirty = 0")
        self.db.execute(QUERIES["soft_delete_override"], ("pov-1", "ver-1"))
        self.assertEqual(self.dirty(), (0, 2))
        with self.assertRaises(sqlite3.IntegrityError):
            self.insert("pov-2", page=99)
        self.assertEqual(self.dirty(), (0, 2))
        # Corrections and overrides share one revision counter.
        self.db.execute(
            QUERIES["insert_correction"], ("cor-1", "ver-1", 1, "a", "b", "r")
        )
        self.assertEqual(self.dirty(), (1, 3))

    def test_clear_dirty_compare_and_set_sees_override_changes(self) -> None:
        for status in ("processing", "extracted"):
            self.set_status("ver-1", status)
        self.insert()
        (rev,) = self.db.execute(
            QUERIES["corrections_rev_by_version"], ("ver-1",)
        ).fetchone()
        self.insert("pov-2", page=2)
        self.db.execute(QUERIES["clear_corrections_dirty"], ("ver-1", rev))
        self.assertEqual(self.dirty(), (1, 2))

    def test_dirty_override_blocks_publish(self) -> None:
        self.add_chunk("c1", "texto")
        self.insert()
        self.to_ready("ver-1")
        with self.assertRaisesRegex(sqlite3.IntegrityError, "corrections_pending"):
            self.publish("ver-1")

    def test_index_serves_version_lookup(self) -> None:
        plan = " ".join(
            r[3]
            for r in self.db.execute(
                "EXPLAIN QUERY PLAN " + QUERIES["overrides_by_version"].rstrip(";"),
                ("ver-1",),
            )
        )
        self.assertIn("page_overrides_version", plan)


class ChunkReviewQueryTests(SchemaTestCase):
    def test_paginated_chunks_with_chunker_version(self) -> None:
        self.add_document()
        self.add_version()
        for i in range(5):
            self.add_chunk(f"c{i}", f"texto {i}", section="Artículo 1")
        self.db.execute(
            "UPDATE document_versions SET chunker_version = 'structural-v1'"
        )
        rows = self.db.execute(
            QUERIES["chunks_page_by_version"], ("ver-1", 2, 1)
        ).fetchall()
        self.assertEqual([r["ordinal"] for r in rows], [1, 2])
        self.assertEqual(
            dict(rows[0]),
            {
                "ordinal": 1,
                "id": "c1",
                "section": "Artículo 1",
                "page_start": 1,
                "page_end": 1,
                "text": "texto 1",
                "chunker_version": "structural-v1",
            },
        )
        total = self.db.execute(
            QUERIES["count_chunks_by_version"], ("ver-1",)
        ).fetchone()
        self.assertEqual(total["total"], 5)


if __name__ == "__main__":
    unittest.main()
