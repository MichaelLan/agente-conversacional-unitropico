-- Canonical D1 queries used by the adapters (RAG, API admin, ingestion).
-- Each block starts with "-- name: <name>" and holds exactly one statement.
-- All values are bound parameters (?N). Never interpolate user text into SQL.
-- Executed against the schema by infra/d1/tests/test_schema.py.
-- Documentation: infra/d1/queries.md.

-- name: search_chunks
-- ?1 = FTS5 expression from build_fts5_query (never raw user text)
-- ?2 = candidate limit (oversample, e.g. 50, before RRF fusion)
-- Lower bm25 = better. Only published, reviewed, not formally superseded versions.
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
LIMIT ?2;

-- name: search_chunks_in_force
-- Same as search_chunks plus temporal filter. ?3 = local date YYYY-MM-DD
-- computed by the caller in America/Bogota (never date('now'), which is UTC).
-- NULL bounds mean "unknown/open" and do not exclude.
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
  AND (v.effective_from IS NULL OR v.effective_from <= ?3)
  AND (v.effective_until IS NULL OR v.effective_until >= ?3)
ORDER BY score, c.seq
LIMIT ?2;

-- name: registry_by_chunk_ids
-- ?1 = JSON array of chunk ids, e.g. '["c1","c2"]'. One bound parameter, so the
-- D1 limit of 100 bound parameters per query never applies. Keep arrays <= 100
-- ids anyway (top-k is at most 100 in Vectorize). Returns RegistryEntry and
-- citation fields. Missing ids are simply absent (treat as not admissible).
SELECT
    r.chunk_id, r.document_id, r.document_title, r.version_id, r.version_label,
    r.source_sha256, r.section, r.page_start, r.page_end, r.text, r.text_sha256,
    r.status, r.requires_review, r.formally_superseded,
    r.issued_at, r.effective_from, r.effective_until
FROM v_chunk_registry r
WHERE r.chunk_id IN (SELECT value FROM json_each(?1));

-- name: registry_by_chunk_id
-- ?1 = chunk id. Single-row variant for AsyncEvidenceCatalog.get_current.
SELECT
    r.chunk_id, r.document_id, r.document_title, r.version_id, r.version_label,
    r.source_sha256, r.section, r.page_start, r.page_end, r.text, r.text_sha256,
    r.status, r.requires_review, r.formally_superseded,
    r.issued_at, r.effective_from, r.effective_until
FROM v_chunk_registry r
WHERE r.chunk_id = ?1;

-- name: catalog_by_version_ids
-- ?1 = JSON array of version ids. Citation metadata per version.
SELECT
    v.id AS version_id, v.document_id, d.title AS document_title, v.version_label,
    v.status, v.requires_review, v.issued_at, v.effective_from, v.effective_until,
    v.supersedes_version_id, v.page_count
FROM document_versions v
JOIN documents d ON d.id = v.document_id
WHERE v.id IN (SELECT value FROM json_each(?1));

-- name: chunks_by_version
-- ?1 = version id. Ordered chunks (ingestion re-index, admin preview).
SELECT id, ordinal, text, text_sha256, section, page_start, page_end,
       token_estimate, embedding_model
FROM chunks
WHERE version_id = ?1
ORDER BY ordinal;

-- name: published_versions_in_force
-- ?1 = local date YYYY-MM-DD (America/Bogota). Admin view of what is in force.
SELECT v.id AS version_id, v.document_id, d.title AS document_title, v.version_label,
       v.effective_from, v.effective_until, v.published_at
FROM document_versions v
JOIN documents d ON d.id = v.document_id
WHERE v.status = 'published'
  AND NOT EXISTS (
      SELECT 1 FROM document_versions s
      WHERE s.supersedes_version_id = v.id AND s.status = 'published'
  )
  AND (v.effective_from IS NULL OR v.effective_from <= ?1)
  AND (v.effective_until IS NULL OR v.effective_until >= ?1)
ORDER BY d.title, v.effective_from;

-- name: insert_version_idempotent
-- ?1 id, ?2 document_id, ?3 version_label, ?4 source_r2_key, ?5 source_sha256,
-- ?6 source_bytes. Re-uploading the same bytes for the same document is a no-op.
-- Follow with version_by_source to obtain the existing or new id.
INSERT INTO document_versions (id, document_id, version_label, source_r2_key,
                               source_sha256, source_bytes)
VALUES (?1, ?2, ?3, ?4, ?5, ?6)
ON CONFLICT (document_id, source_sha256) DO NOTHING;

-- name: version_by_source
-- ?1 document_id, ?2 source_sha256.
SELECT id, status, source_r2_key FROM document_versions
WHERE document_id = ?1 AND source_sha256 = ?2;

-- name: log_event
-- ?1 version_id, ?2 step, ?3 status, ?4 error_code (nullable), ?5 detail (nullable, no PII).
INSERT INTO processing_events (version_id, step, status, error_code, detail)
VALUES (?1, ?2, ?3, ?4, ?5);

-- name: fts_integrity_check
-- Raises SQLITE_CORRUPT_VTAB if chunks_fts disagrees with chunks.
INSERT INTO chunks_fts (chunks_fts, rank) VALUES ('integrity-check', 1);

-- name: fts_rebuild
-- Recovery only: rebuilds chunks_fts from chunks.
INSERT INTO chunks_fts (chunks_fts) VALUES ('rebuild');
