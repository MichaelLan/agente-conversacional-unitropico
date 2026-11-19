-- Migration number: 0001 	 initial catalog, FTS5 lexical index and processing log
--
-- Target: Cloudflare D1 (SQLite). Applied by `wrangler d1 migrations apply`.
-- No explicit BEGIN/COMMIT: wrangler wraps each migration file itself.
-- D1 enforces foreign keys by default, the local tests enable them too.
-- Comments avoid semicolons so that statement splitting stays trivial.
--
-- Invariants enforced here (not only in application code):
--   * Ids are TEXT of 1..64 UTF-8 bytes (chunk id is reused as Vectorize id, max 64 bytes).
--   * Hashes are 64 lowercase hex characters (sha256).
--   * A version is published only when reviewed, extracted, chunked and with valid page ranges.
--   * Chunks of a published version are immutable (unpublish first).
--   * A version with corrections or page overrides not yet applied by the
--     ingestion (corrections_dirty = 1) cannot be published.
--   * Upload order never implies validity: effective_from/effective_until and
--     supersedes_version_id are manual administrator metadata.

-- ---------------------------------------------------------------------------
-- documents: the logical institutional document (e.g. "Resolucion CBU 002").
-- ---------------------------------------------------------------------------
CREATE TABLE documents (
    id            TEXT PRIMARY KEY NOT NULL
                  CHECK (length(CAST(id AS BLOB)) BETWEEN 1 AND 64 AND trim(id) = id),
    title         TEXT NOT NULL CHECK (length(trim(title)) > 0),
    document_type TEXT NOT NULL CHECK (length(trim(document_type)) > 0),
    issuing_body  TEXT CHECK (issuing_body IS NULL OR length(trim(issuing_body)) > 0),
    created_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
) STRICT;

-- ---------------------------------------------------------------------------
-- document_versions: one physical source file (PDF) of a document.
-- ---------------------------------------------------------------------------
CREATE TABLE document_versions (
    id                    TEXT PRIMARY KEY NOT NULL
                          CHECK (length(CAST(id AS BLOB)) BETWEEN 1 AND 64 AND trim(id) = id),
    document_id           TEXT NOT NULL REFERENCES documents (id) ON DELETE RESTRICT,
    version_label         TEXT CHECK (version_label IS NULL OR length(trim(version_label)) > 0),
    source_r2_key         TEXT NOT NULL UNIQUE CHECK (length(trim(source_r2_key)) > 0),
    source_sha256         TEXT NOT NULL
                          CHECK (length(source_sha256) = 64 AND source_sha256 NOT GLOB '*[^0-9a-f]*'),
    source_bytes          INTEGER NOT NULL CHECK (source_bytes > 0),
    page_count            INTEGER CHECK (page_count IS NULL OR page_count > 0),
    -- Calendar dates YYYY-MM-DD, entered by the administrator. NULL = unknown/open.
    issued_at             TEXT CHECK (issued_at IS NULL OR date(issued_at) IS issued_at),
    effective_from        TEXT CHECK (effective_from IS NULL OR date(effective_from) IS effective_from),
    effective_until       TEXT CHECK (effective_until IS NULL OR date(effective_until) IS effective_until),
    supersedes_version_id TEXT REFERENCES document_versions (id) ON DELETE RESTRICT,
    status                TEXT NOT NULL DEFAULT 'uploaded'
                          CHECK (status IN ('uploaded', 'processing', 'extracted', 'review_required',
                                            'ready', 'published', 'unpublished', 'failed')),
    requires_review       INTEGER NOT NULL DEFAULT 1 CHECK (requires_review IN (0, 1)),
    error_code            TEXT CHECK (error_code IS NULL OR length(trim(error_code)) > 0),
    error_detail          TEXT CHECK (error_detail IS NULL OR length(error_detail) <= 4000),
    processing_attempts   INTEGER NOT NULL DEFAULT 0 CHECK (processing_attempts >= 0),
    chunker_version       TEXT CHECK (chunker_version IS NULL OR length(trim(chunker_version)) > 0),
    extraction_r2_key     TEXT UNIQUE CHECK (extraction_r2_key IS NULL OR length(trim(extraction_r2_key)) > 0),
    -- Pending OCR corrections. Every create or logical delete of a text_correction
    -- or a page_override sets corrections_dirty = 1 and increments corrections_rev
    -- (triggers below).
    -- Only the ingestion chunk step clears the flag, and only when the revision it
    -- read together with the corrections is still current (compare-and-set).
    corrections_dirty     INTEGER NOT NULL DEFAULT 0 CHECK (corrections_dirty IN (0, 1)),
    corrections_rev       INTEGER NOT NULL DEFAULT 0 CHECK (corrections_rev >= 0),
    created_at            TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at            TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    published_at          TEXT,

    -- Idempotent upload: the same bytes cannot be registered twice for a document.
    UNIQUE (document_id, source_sha256),
    -- Labels are unique per document when present (multiple NULLs allowed).
    UNIQUE (document_id, version_label),
    CHECK (supersedes_version_id IS NULL OR supersedes_version_id <> id),
    CHECK (effective_from IS NULL OR effective_until IS NULL OR effective_until >= effective_from),
    CHECK (status <> 'failed' OR error_code IS NOT NULL),
    CHECK (status <> 'published' OR (
        requires_review = 0
        AND published_at IS NOT NULL
        AND page_count IS NOT NULL
        AND extraction_r2_key IS NOT NULL
        AND chunker_version IS NOT NULL
        AND error_code IS NULL
        AND corrections_dirty = 0
    ))
) STRICT;

-- Only uploaded is a valid initial state.
CREATE TRIGGER document_versions_initial_status
BEFORE INSERT ON document_versions
WHEN NEW.status <> 'uploaded'
BEGIN
    SELECT RAISE(ABORT, 'invalid_initial_status');
END;

-- Allowed state machine. Any state may fail. Retry goes back through processing.
CREATE TRIGGER document_versions_status_transition
BEFORE UPDATE OF status ON document_versions
WHEN NEW.status <> OLD.status AND NOT (
       (OLD.status = 'uploaded'        AND NEW.status IN ('processing', 'failed'))
    OR (OLD.status = 'processing'      AND NEW.status IN ('extracted', 'failed'))
    OR (OLD.status = 'extracted'       AND NEW.status IN ('review_required', 'ready', 'processing', 'failed'))
    OR (OLD.status = 'review_required' AND NEW.status IN ('ready', 'processing', 'failed'))
    OR (OLD.status = 'ready'           AND NEW.status IN ('published', 'review_required', 'processing', 'failed'))
    OR (OLD.status = 'published'       AND NEW.status IN ('unpublished'))
    OR (OLD.status = 'unpublished'     AND NEW.status IN ('published', 'review_required', 'processing'))
    OR (OLD.status = 'failed'          AND NEW.status IN ('processing'))
)
BEGIN
    SELECT RAISE(ABORT, 'invalid_status_transition');
END;

-- Publishing requires at least one chunk and every chunk inside the physical page range.
CREATE TRIGGER document_versions_publish_guard
BEFORE UPDATE OF status ON document_versions
WHEN NEW.status = 'published' AND OLD.status IN ('ready', 'unpublished')
BEGIN
    SELECT RAISE(ABORT, 'publish_without_chunks')
    WHERE NOT EXISTS (SELECT 1 FROM chunks WHERE version_id = NEW.id);
    SELECT RAISE(ABORT, 'chunk_page_out_of_range')
    WHERE EXISTS (SELECT 1 FROM chunks WHERE version_id = NEW.id AND page_end > NEW.page_count);
    SELECT RAISE(ABORT, 'corrections_pending')
    WHERE NEW.corrections_dirty <> 0;
END;

-- Identity and provenance of a published version cannot change in place.
CREATE TRIGGER document_versions_published_identity
BEFORE UPDATE OF document_id, source_r2_key, source_sha256, source_bytes, page_count,
                 chunker_version, extraction_r2_key ON document_versions
WHEN OLD.status = 'published'
BEGIN
    SELECT RAISE(ABORT, 'published_version_immutable');
END;

-- Refresh updated_at unless the statement already set it (recursive_triggers is off).
CREATE TRIGGER document_versions_touch
AFTER UPDATE ON document_versions
WHEN NEW.updated_at IS OLD.updated_at
BEGIN
    UPDATE document_versions
    SET updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
    WHERE id = NEW.id;
END;

-- ---------------------------------------------------------------------------
-- chunks: exact retrievable text with catalog-attested location.
-- seq is an explicit INTEGER PRIMARY KEY so the FTS5 external-content rowid is
-- stable (an implicit rowid may change on VACUUM). id is the public chunk id
-- and the Vectorize vector id.
-- ---------------------------------------------------------------------------
CREATE TABLE chunks (
    seq             INTEGER PRIMARY KEY,
    id              TEXT NOT NULL UNIQUE
                    CHECK (length(CAST(id AS BLOB)) BETWEEN 1 AND 64 AND trim(id) = id),
    version_id      TEXT NOT NULL REFERENCES document_versions (id) ON DELETE CASCADE,
    ordinal         INTEGER NOT NULL CHECK (ordinal >= 0),
    text            TEXT NOT NULL CHECK (length(trim(text)) > 0),
    text_sha256     TEXT NOT NULL
                    CHECK (length(text_sha256) = 64 AND text_sha256 NOT GLOB '*[^0-9a-f]*'),
    section         TEXT CHECK (section IS NULL OR length(trim(section)) > 0),
    page_start      INTEGER NOT NULL CHECK (page_start >= 1),
    page_end        INTEGER NOT NULL,
    token_estimate  INTEGER NOT NULL CHECK (token_estimate >= 1),
    embedding_model TEXT CHECK (embedding_model IS NULL OR length(trim(embedding_model)) > 0),
    created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    UNIQUE (version_id, ordinal),
    CHECK (page_end >= page_start)
) STRICT;

-- Chunks by version: served by the UNIQUE (version_id, ordinal) index, no extra index.

-- Chunks of a published version are immutable.
CREATE TRIGGER chunks_guard_insert
BEFORE INSERT ON chunks
WHEN (SELECT status FROM document_versions WHERE id = NEW.version_id) = 'published'
BEGIN
    SELECT RAISE(ABORT, 'published_version_immutable');
END;

CREATE TRIGGER chunks_guard_update
BEFORE UPDATE ON chunks
WHEN (SELECT status FROM document_versions WHERE id = OLD.version_id) = 'published'
  OR (SELECT status FROM document_versions WHERE id = NEW.version_id) = 'published'
BEGIN
    SELECT RAISE(ABORT, 'published_version_immutable');
END;

CREATE TRIGGER chunks_guard_delete
BEFORE DELETE ON chunks
WHEN (SELECT status FROM document_versions WHERE id = OLD.version_id) = 'published'
BEGIN
    SELECT RAISE(ABORT, 'published_version_immutable');
END;

-- Reject pages beyond the known physical page count (NULL page_count is checked at publish).
CREATE TRIGGER chunks_page_range_insert
BEFORE INSERT ON chunks
WHEN NEW.page_end > (SELECT page_count FROM document_versions WHERE id = NEW.version_id)
BEGIN
    SELECT RAISE(ABORT, 'chunk_page_out_of_range');
END;

CREATE TRIGGER chunks_page_range_update
BEFORE UPDATE OF page_start, page_end, version_id ON chunks
WHEN NEW.page_end > (SELECT page_count FROM document_versions WHERE id = NEW.version_id)
BEGIN
    SELECT RAISE(ABORT, 'chunk_page_out_of_range');
END;

-- ---------------------------------------------------------------------------
-- chunks_fts: FTS5 external-content index over chunks(text, section).
-- unicode61 + remove_diacritics 2: case and diacritic insensitive, so the
-- query "inscripcion" matches "inscripción" and vice versa (OCR output often
-- loses accents). Side effect: n-tilde folds to n ("año" ~ "ano").
-- ---------------------------------------------------------------------------
CREATE VIRTUAL TABLE chunks_fts USING fts5 (
    text,
    section,
    content = 'chunks',
    content_rowid = 'seq',
    tokenize = 'unicode61 remove_diacritics 2'
);

CREATE TRIGGER chunks_fts_after_insert
AFTER INSERT ON chunks
BEGIN
    INSERT INTO chunks_fts (rowid, text, section) VALUES (NEW.seq, NEW.text, NEW.section);
END;

CREATE TRIGGER chunks_fts_after_delete
AFTER DELETE ON chunks
BEGIN
    INSERT INTO chunks_fts (chunks_fts, rowid, text, section)
    VALUES ('delete', OLD.seq, OLD.text, OLD.section);
END;

CREATE TRIGGER chunks_fts_after_update
AFTER UPDATE OF seq, text, section ON chunks
BEGIN
    INSERT INTO chunks_fts (chunks_fts, rowid, text, section)
    VALUES ('delete', OLD.seq, OLD.text, OLD.section);
    INSERT INTO chunks_fts (rowid, text, section) VALUES (NEW.seq, NEW.text, NEW.section);
END;

-- ---------------------------------------------------------------------------
-- processing_events: append-only log of ingestion steps and errors.
-- RESTRICT keeps the audit trail: versions are unpublished, not deleted.
-- detail must not contain personal data or full document text.
-- ---------------------------------------------------------------------------
CREATE TABLE processing_events (
    id         INTEGER PRIMARY KEY,
    version_id TEXT NOT NULL REFERENCES document_versions (id) ON DELETE RESTRICT,
    step       TEXT NOT NULL
               CHECK (step IN ('upload', 'extract', 'review', 'correct', 'chunk', 'embed',
                               'index_fts', 'index_vectors', 'publish', 'unpublish')),
    status     TEXT NOT NULL CHECK (status IN ('started', 'succeeded', 'failed', 'skipped')),
    error_code TEXT CHECK (error_code IS NULL OR length(trim(error_code)) > 0),
    detail     TEXT CHECK (detail IS NULL OR length(detail) <= 4000),
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    CHECK (status <> 'failed' OR error_code IS NOT NULL)
) STRICT;

CREATE INDEX processing_events_version_time ON processing_events (version_id, created_at, id);

CREATE TRIGGER processing_events_append_only_update
BEFORE UPDATE ON processing_events
BEGIN
    SELECT RAISE(ABORT, 'processing_events_append_only');
END;

CREATE TRIGGER processing_events_append_only_delete
BEFORE DELETE ON processing_events
BEGIN
    SELECT RAISE(ABORT, 'processing_events_append_only');
END;

-- ---------------------------------------------------------------------------
-- text_corrections: literal OCR corrections recorded by the administrator.
-- The ingestion chunk step applies the active ones (deleted_at IS NULL) to the
-- page text before chunking, so the OCR cached in R2 is never paid again.
-- find_text must match exactly once in its page or the version fails with
-- correction_not_applicable (see workers/ingestion/README.md).
-- Rows are never deleted nor edited: deleted_at is a one-way logical delete,
-- which keeps the audit trail. RESTRICT keeps versions with corrections.
-- Lengths are in characters. replace_text may be empty (remove OCR noise).
-- ---------------------------------------------------------------------------
CREATE TABLE text_corrections (
    id           TEXT PRIMARY KEY NOT NULL
                 CHECK (length(CAST(id AS BLOB)) BETWEEN 1 AND 64 AND trim(id) = id),
    version_id   TEXT NOT NULL REFERENCES document_versions (id) ON DELETE RESTRICT,
    page_number  INTEGER NOT NULL CHECK (page_number >= 1),
    find_text    TEXT NOT NULL CHECK (length(trim(find_text)) > 0 AND length(find_text) <= 500),
    replace_text TEXT NOT NULL CHECK (length(replace_text) <= 500),
    reason       TEXT NOT NULL CHECK (length(trim(reason)) > 0 AND length(reason) <= 300),
    created_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    deleted_at   TEXT,
    CHECK (find_text <> replace_text),
    CHECK (deleted_at IS NULL OR deleted_at >= created_at)
) STRICT;

-- Order is created_at, then rowid (insertion order): ids are random uuid4, so
-- they must never break ties. The rowid is implicit in every index entry.
CREATE INDEX text_corrections_version ON text_corrections (version_id, created_at);

-- One active correction per (version, page, find_text): a duplicate could never
-- match exactly once. Logically deleted rows do not block a new one.
CREATE UNIQUE INDEX text_corrections_active_unique
    ON text_corrections (version_id, page_number, find_text)
    WHERE deleted_at IS NULL;

-- A published version is immutable: unpublish before adding or removing corrections.
CREATE TRIGGER text_corrections_guard_insert
BEFORE INSERT ON text_corrections
WHEN (SELECT status FROM document_versions WHERE id = NEW.version_id) = 'published'
BEGIN
    SELECT RAISE(ABORT, 'published_version_immutable');
END;

-- Pages beyond the known physical page count can never match (NULL = unknown).
CREATE TRIGGER text_corrections_page_range
BEFORE INSERT ON text_corrections
WHEN NEW.page_number > (SELECT page_count FROM document_versions WHERE id = NEW.version_id)
BEGIN
    SELECT RAISE(ABORT, 'correction_page_out_of_range');
END;

-- Only deleted_at may change, once, from NULL, and never on a published version.
CREATE TRIGGER text_corrections_immutable
BEFORE UPDATE ON text_corrections
WHEN NEW.id IS NOT OLD.id
  OR NEW.version_id IS NOT OLD.version_id
  OR NEW.page_number IS NOT OLD.page_number
  OR NEW.find_text IS NOT OLD.find_text
  OR NEW.replace_text IS NOT OLD.replace_text
  OR NEW.reason IS NOT OLD.reason
  OR NEW.created_at IS NOT OLD.created_at
  OR OLD.deleted_at IS NOT NULL
  OR NEW.deleted_at IS NULL
BEGIN
    SELECT RAISE(ABORT, 'text_correction_immutable');
END;

CREATE TRIGGER text_corrections_guard_delete_published
BEFORE UPDATE OF deleted_at ON text_corrections
WHEN (SELECT status FROM document_versions WHERE id = OLD.version_id) = 'published'
BEGIN
    SELECT RAISE(ABORT, 'published_version_immutable');
END;

-- Creating or logically deleting a correction leaves the chunks stale until the
-- ingestion applies the new set: mark the version dirty and bump its revision.
CREATE TRIGGER text_corrections_dirty_insert
AFTER INSERT ON text_corrections
BEGIN
    UPDATE document_versions
    SET corrections_dirty = 1, corrections_rev = corrections_rev + 1
    WHERE id = NEW.version_id;
END;

CREATE TRIGGER text_corrections_dirty_delete
AFTER UPDATE OF deleted_at ON text_corrections
WHEN OLD.deleted_at IS NULL AND NEW.deleted_at IS NOT NULL
BEGIN
    UPDATE document_versions
    SET corrections_dirty = 1, corrections_rev = corrections_rev + 1
    WHERE id = NEW.version_id;
END;

CREATE TRIGGER text_corrections_no_delete
BEFORE DELETE ON text_corrections
BEGIN
    SELECT RAISE(ABORT, 'text_corrections_append_only');
END;

-- ---------------------------------------------------------------------------
-- page_overrides: full replacement text for one page, recorded by the
-- administrator when literal corrections cannot fix it (diagrams, tables as
-- images, text invented by the OCR). The ingestion chunk step replaces the
-- prepared page text with the prepared override text BEFORE applying the
-- literal corrections of that page. At most one active override per
-- (version, page). Same audit rules as text_corrections: rows are never
-- deleted nor edited, deleted_at is a one-way logical delete, and every create
-- or logical delete marks the version dirty and bumps corrections_rev.
-- Lengths are in characters.
-- ---------------------------------------------------------------------------
CREATE TABLE page_overrides (
    id          TEXT PRIMARY KEY NOT NULL
                CHECK (length(CAST(id AS BLOB)) BETWEEN 1 AND 64 AND trim(id) = id),
    version_id  TEXT NOT NULL REFERENCES document_versions (id) ON DELETE RESTRICT,
    page_number INTEGER NOT NULL CHECK (page_number >= 1),
    text        TEXT NOT NULL
                CHECK (length(trim(text, char(32, 9, 10, 13))) > 0 AND length(text) <= 20000),
    reason      TEXT NOT NULL CHECK (length(trim(reason)) > 0 AND length(reason) <= 300),
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    deleted_at  TEXT,
    CHECK (deleted_at IS NULL OR deleted_at >= created_at)
) STRICT;

CREATE INDEX page_overrides_version ON page_overrides (version_id, created_at);

-- At most one active override per page. Logically deleted rows do not block.
CREATE UNIQUE INDEX page_overrides_active_unique
    ON page_overrides (version_id, page_number)
    WHERE deleted_at IS NULL;

CREATE TRIGGER page_overrides_guard_insert
BEFORE INSERT ON page_overrides
WHEN (SELECT status FROM document_versions WHERE id = NEW.version_id) = 'published'
BEGIN
    SELECT RAISE(ABORT, 'published_version_immutable');
END;

CREATE TRIGGER page_overrides_page_range
BEFORE INSERT ON page_overrides
WHEN NEW.page_number > (SELECT page_count FROM document_versions WHERE id = NEW.version_id)
BEGIN
    SELECT RAISE(ABORT, 'override_page_out_of_range');
END;

CREATE TRIGGER page_overrides_immutable
BEFORE UPDATE ON page_overrides
WHEN NEW.id IS NOT OLD.id
  OR NEW.version_id IS NOT OLD.version_id
  OR NEW.page_number IS NOT OLD.page_number
  OR NEW.text IS NOT OLD.text
  OR NEW.reason IS NOT OLD.reason
  OR NEW.created_at IS NOT OLD.created_at
  OR OLD.deleted_at IS NOT NULL
  OR NEW.deleted_at IS NULL
BEGIN
    SELECT RAISE(ABORT, 'page_override_immutable');
END;

CREATE TRIGGER page_overrides_guard_delete_published
BEFORE UPDATE OF deleted_at ON page_overrides
WHEN (SELECT status FROM document_versions WHERE id = OLD.version_id) = 'published'
BEGIN
    SELECT RAISE(ABORT, 'published_version_immutable');
END;

CREATE TRIGGER page_overrides_dirty_insert
AFTER INSERT ON page_overrides
BEGIN
    UPDATE document_versions
    SET corrections_dirty = 1, corrections_rev = corrections_rev + 1
    WHERE id = NEW.version_id;
END;

CREATE TRIGGER page_overrides_dirty_delete
AFTER UPDATE OF deleted_at ON page_overrides
WHEN OLD.deleted_at IS NULL AND NEW.deleted_at IS NOT NULL
BEGIN
    UPDATE document_versions
    SET corrections_dirty = 1, corrections_rev = corrections_rev + 1
    WHERE id = NEW.version_id;
END;

CREATE TRIGGER page_overrides_no_delete
BEFORE DELETE ON page_overrides
BEGIN
    SELECT RAISE(ABORT, 'page_overrides_append_only');
END;

-- ---------------------------------------------------------------------------
-- Indexes for catalog and validity lookups.
-- ---------------------------------------------------------------------------
CREATE INDEX document_versions_document ON document_versions (document_id, status);
CREATE INDEX document_versions_published
    ON document_versions (document_id, effective_from, effective_until)
    WHERE status = 'published';
CREATE INDEX document_versions_supersedes
    ON document_versions (supersedes_version_id)
    WHERE supersedes_version_id IS NOT NULL;

-- ---------------------------------------------------------------------------
-- v_chunk_registry: authoritative catalog row per chunk (RegistryEntry + citation fields).
-- formally_superseded = another PUBLISHED version declares it supersedes this one.
-- A draft replacement never hides the published version.
-- No date filtering here: temporal policy is applied by the query with an explicit
-- local date parameter (see infra/d1/queries.md).
-- ---------------------------------------------------------------------------
CREATE VIEW v_chunk_registry AS
SELECT
    c.id               AS chunk_id,
    c.seq              AS chunk_seq,
    v.document_id      AS document_id,
    d.title            AS document_title,
    d.document_type    AS document_type,
    v.id               AS version_id,
    v.version_label    AS version_label,
    v.source_sha256    AS source_sha256,
    c.section          AS section,
    c.page_start       AS page_start,
    c.page_end         AS page_end,
    c.text             AS text,
    c.text_sha256      AS text_sha256,
    c.embedding_model  AS embedding_model,
    v.status           AS status,
    v.requires_review  AS requires_review,
    EXISTS (
        SELECT 1 FROM document_versions s
        WHERE s.supersedes_version_id = v.id AND s.status = 'published'
    )                  AS formally_superseded,
    v.issued_at        AS issued_at,
    v.effective_from   AS effective_from,
    v.effective_until  AS effective_until
FROM chunks c
JOIN document_versions v ON v.id = c.version_id
JOIN documents d ON d.id = v.document_id;
