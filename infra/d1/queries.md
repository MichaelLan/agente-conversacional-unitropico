# Consultas canónicas D1

Fuente única del SQL: [`queries.sql`](queries.sql) (bloques `-- name: <nombre>`, una sentencia por bloque).
Todas las consultas se ejecutan contra el esquema real en `tests/test_schema.py`; si cambias una, cambia los tests.
Este documento explica **cómo** deben usarlas los adaptadores (RAG, API admin, ingesta).

Reglas comunes:
- Todo valor va como parámetro enlazado (`?1`, `?2`…). Nunca se interpola texto del usuario en el SQL.
- En D1: máximo **100 parámetros enlazados por consulta**, sentencias de hasta 100 KB, y 50 (Free) / 1000 (Paid) consultas por invocación; un `db.batch()` cuenta como una sola consulta ([límites D1](https://developers.cloudflare.com/d1/platform/limits/)).
- D1 aplica claves foráneas por defecto ([foreign keys](https://developers.cloudflare.com/d1/sql-api/foreign-keys/)).
- Las funciones de SQLite disponibles en D1 son un subconjunto ([SQL statements](https://developers.cloudflare.com/d1/sql-api/sql-statements/)). Observado con `wrangler d1 execute --local` 4.165.0: `sqlite_version()` da *not authorized*. No uses funciones fuera de las que aparecen aquí sin probarlas en local.

## 1. Búsqueda léxica (`search_chunks`)

### 1.1 Escape seguro de la pregunta: `build_fts5_query`

La implementación de referencia es [`tests/fts_query.py`](tests/fts_query.py) (Python puro, solo stdlib; está probada en `tests/test_fts_query.py` y contra FTS5 real en `tests/test_schema.py`). El agente RAG la copia tal cual dentro de `apps/rag`; no la importes entre proyectos.

Algoritmo:
1. Normaliza con NFKC, para que los acentos descompuestos (NFD) se compongan antes de tokenizar.
2. Tokeniza por letras y dígitos Unicode (`[^\W_]+`). Así desaparecen las comillas, los paréntesis, `*`, `^`, `:`, `-`, `+`, `_`, `;` y cualquier otro signo, que en FTS5 son sintaxis.
3. Quita duplicados sin distinguir mayúsculas ni tildes (el mismo plegado que hace `unicode61 remove_diacritics 2`) y conserva el orden de aparición.
4. Quita una lista corta de palabras vacías en español (`de`, `la`, `los`, `que`, `y`…), salvo que eso deje la consulta vacía.
5. Se queda con `max_terms` términos como máximo (16 por defecto), para acotar el coste.
6. Entrecomilla cada término con comillas dobles y duplica las comillas internas (defensivo, porque el paso 2 ya las elimina). Así `AND`, `OR`, `NOT` y `NEAR` quedan como palabras normales.
7. Une los términos con ` OR ` (prioriza el recall). bm25 pone arriba los fragmentos que coinciden con más términos y con los más raros.
8. Si no queda ningún término (por ejemplo `"¿?"` o `'"""'`), devuelve `None`. En ese caso el adaptador **omite la búsqueda léxica**; nunca ejecuta `MATCH NULL` ni `MATCH ''`.

Ejemplo: `¿Cuáles son los requisitos de inscripción?` → `"son" OR "requisitos" OR "inscripción"`.

### 1.2 Consulta

```sql
-- ?1 = build_fts5_query(pregunta)   ?2 = límite de candidatos (p. ej. 50, sobremuestreo para RRF)
SELECT c.id AS chunk_id, c.version_id, v.document_id, bm25(chunks_fts) AS score
FROM chunks_fts
JOIN chunks c ON c.seq = chunks_fts.rowid
JOIN document_versions v ON v.id = c.version_id
WHERE chunks_fts MATCH ?1
  AND v.status = 'published' AND v.requires_review = 0
  AND NOT EXISTS (SELECT 1 FROM document_versions s
                  WHERE s.supersedes_version_id = v.id AND s.status = 'published')
ORDER BY score, c.seq
LIMIT ?2;
```

- `bm25()` devuelve valores **negativos**; cuanto menor, más relevante. Se ordena ascendente y `c.seq` rompe los empates de forma determinista.
- Para RRF solo importa el **rango**; no mezcles las puntuaciones bm25 con las distancias coseno.
- Las ponderaciones por columna (`bm25(chunks_fts, w_text, w_section)`) quedan para la evaluación; hoy ambas valen 1.0.
- El filtro excluye las versiones no publicadas, las que requieren revisión y las **sustituidas formalmente por una versión publicada**. Un borrador que sustituye a otro no oculta la versión vigente.
- `search_chunks_in_force` añade `?3` = fecha local `YYYY-MM-DD` en **America/Bogota**, que calcula quien llama. No se usa `date('now')`, porque es UTC y adelanta cinco horas el cambio de día. Si `effective_from` o `effective_until` es NULL, ese límite se trata como «desconocido» y no excluye.
- La política temporal (si una convocatoria vencida se excluye o se cita con advertencia) **no** está decidida en el esquema. `search_chunks` no filtra por fechas; la consulta con fechas es la variante para cuando se decida.

## 2. Catálogo autoritativo

### 2.1 `registry_by_chunk_ids` (lote) y `registry_by_chunk_id` (uno)

Revalida en D1 los candidatos de Vectorize y de FTS: `is_admissible` compara `document_id`, `version_id`, `source_sha256`, la ubicación, `status`, `requires_review`, `formally_superseded` y `text_sha256`. Devuelve también los campos de cita del contrato v1: `document_title`, `version_label`, `section`, `page_start` y `page_end`.

```sql
SELECT ... FROM v_chunk_registry r
WHERE r.chunk_id IN (SELECT value FROM json_each(?1));   -- ?1 = '["id1","id2",...]'
```

- **Un solo parámetro**: la lista viaja como array JSON (D1 incluye la extensión JSON), así que nunca se alcanza el límite de 100 parámetros. El plan usa el índice único de `chunks.id`; el test lo comprueba con `EXPLAIN QUERY PLAN`.
- Mantén cada lote en ≤100 ids (el `topK` de Vectorize es ≤100 de todos modos).
- Si un id falta en el resultado, el chunk no está registrado y se descarta (`entry is None` ⇒ no admisible).
- Alternativa, si se prefiere `IN (?, ?, …)`: trocea en lotes de ≤100 menos los demás parámetros de la consulta, y ejecuta los lotes en un único `db.batch()`.
- `formally_superseded` y `requires_review` llegan como 0/1 y el adaptador los convierte a `bool`.
- Hasta que R1 cambie `EvidenceIdentity.page: int` a un rango, el adaptador no debe colapsar `page_start..page_end` en silencio. Es una pregunta abierta para el RAG.

### 2.2 `catalog_by_version_ids`

Lee los metadatos de cita por versión con el mismo patrón `json_each(?1)`.

### 2.3 `chunks_by_version`

Devuelve los chunks ordenados de una versión, para la ingesta (reindexar o regenerar vectores) y la vista previa del administrador. Usa el índice único `(version_id, ordinal)`.

### 2.4 Revisión de texto: `chunks_page_by_version` y `count_chunks_by_version`

Página de chunks de una versión para que el administrador revise el texto extraído (`GET /admin/versions/{id}/chunks`), en cualquier estado. `?2` = límite (la API lo acota a 1..100) y `?3` = offset. `chunker_version` sale de la versión (la tabla `chunks` no la guarda). Usa el índice único `(version_id, ordinal)`.

### 2.5 Correcciones de texto (`text_corrections`)

- `corrections_by_version`: todas (activas y borradas lógicamente), por `created_at, rowid`. Lo usa la API. El desempate es el `rowid` (orden de inserción), nunca el `id` (uuid4 aleatorio: el orden dejaría de ser determinista).
- `active_corrections_by_version`: las que aplica la ingesta en el paso `chunk`, **en este orden** (`created_at, rowid`; la ingesta guarda una copia literal y un test comprueba que coincide). Usa el índice `text_corrections_version (version_id, created_at)`, cuyas entradas incluyen el `rowid`.
- `corrections_rev_by_version`: revisión del conjunto de correcciones. La ingesta la lee **en el mismo batch** que `active_corrections_by_version` (una sola instantánea).
- `clear_corrections_dirty`: compare-and-set; pone `corrections_dirty = 0` solo si `corrections_rev` sigue siendo el leído y la versión está en `extracted`. La ingesta la ejecuta en el último batch de `replace_chunks`, de forma atómica con la escritura de los chunks.
- `insert_correction`: los triggers rechazan versiones `published` y páginas por encima de un `page_count` conocido. El conteo de activas (< 100) va en la misma sentencia (`INSERT … SELECT … WHERE`), así que dos peticiones concurrentes no superan el tope; `RETURNING id` vacío = tope alcanzado. Un duplicado activo (misma versión, página y `find_text`) falla con `UNIQUE constraint failed` (índice parcial `text_corrections_active_unique … WHERE deleted_at IS NULL`).
- `soft_delete_correction`: fija `deleted_at` con el reloj de la base (así `deleted_at >= created_at`) solo si estaba activa; `RETURNING id` vacío = ya borrada o de otra versión. Las filas nunca se borran físicamente.
- **Correcciones pendientes.** Los triggers `text_corrections_dirty_insert` y `text_corrections_dirty_delete` ponen `document_versions.corrections_dirty = 1` e incrementan `corrections_rev` en cada alta o borrado lógico, dentro de la misma transacción. Solo el paso `chunk` de la ingesta limpia la marca (`clear_corrections_dirty`). Mientras valga 1, la versión no puede publicarse: el trigger `document_versions_publish_guard` aborta con `corrections_pending`, y además lo impide el `CHECK` de `published`. La API también rechaza con 409 `corrections_pending` marcarla como revisada.

### 2.6 `published_versions_in_force`

Vista de administración de lo vigente en una fecha local `?1`.

## 3. Ingesta

### 3.1 Alta idempotente de versión

```sql
INSERT INTO document_versions (id, document_id, version_label, source_r2_key, source_sha256, source_bytes)
VALUES (?1, ?2, ?3, ?4, ?5, ?6)
ON CONFLICT (document_id, source_sha256) DO NOTHING;
-- después: version_by_source(?1 document_id, ?2 source_sha256) → id existente o nuevo
```

Subir otra vez los mismos bytes al mismo documento no hace nada y devuelve la versión existente. La clave R2 recomendada es determinista: `raw/{source_sha256}.pdf`.

### 3.2 Máquina de estados (la aplican triggers)

```
uploaded → processing → extracted → review_required → ready → published ⇄ unpublished
             ↑   cualquier estado (salvo published/unpublished) → failed → processing
```

- La inserción solo admite `uploaded`.
- `published` exige `requires_review = 0`, `published_at`, `page_count`, `extraction_r2_key`, `chunker_version`, que no haya `error_code`, al menos un chunk y que todos tengan `page_end <= page_count`.
- `failed` exige `error_code`.
- Los chunks de una versión `published` son inmutables (no se pueden insertar, actualizar ni borrar). Para corregir: `unpublished` → editar → `published`.
- Rechunking: borra los chunks de la versión (los triggers limpian FTS) e inserta los nuevos.
- Id de chunk recomendado: `sha256(version_id ‖ 0x1f ‖ chunker_version ‖ 0x1f ‖ ordinal ‖ 0x1f ‖ text_sha256)` en hex, 64 bytes justos. Es determinista (los upserts en Vectorize son idempotentes) y cabe en el límite de 64 bytes del id de vector.

### 3.3 `log_event`

Añade un registro a `processing_events`, que solo admite inserciones. Pasos: `upload`, `extract`, `review`, `correct` (alta, borrado lógico y aplicación de correcciones de texto), `chunk`, `embed`, `index_fts`, `index_vectors`, `publish`, `unpublish`. `detail` acepta ≤4000 caracteres y **no** debe contener datos personales ni el texto de los documentos.

## 4. Mantenimiento FTS

- `fts_integrity_check`: falla (`SQLITE_CORRUPT_VTAB`) si `chunks_fts` no coincide con `chunks`. Úsalo tras ingestas masivas.
- `fts_rebuild`: reconstruye el índice desde `chunks`. Es solo para recuperación.

## 5. Tokenizador: verificación pendiente en D1 remoto

D1 documenta que admite FTS5, pero **no publica la lista de tokenizadores admitidos** ([SQL statements](https://developers.cloudflare.com/d1/sql-api/sql-statements/)). `unicode61 remove_diacritics 2` es estándar en SQLite ≥3.27 y queda verificado en:
- SQLite 3.53.1 (Python 3.14.6, `uv run --no-project --python 3.14`), con la suite completa.
- `wrangler d1 execute --local` (wrangler 4.165.0, workerd/miniflare, sin cuenta ni login): la migración se aplica, `"inscripcion"` y `"inscripción"` encuentran los dos textos, los triggers de update/delete se mantienen sincronizados y pasan `integrity-check` y `json_each`.

El modo local **no es** la base remota. Antes de usarlo en producción, y con autorización para crear la base, confirma en remoto:

```sh
cd apps/rag   # o el worker que declare el binding DB
npx wrangler d1 migrations apply DB --remote
npx wrangler d1 execute DB --remote --command "SELECT c.id FROM chunks_fts JOIN chunks c ON c.seq = chunks_fts.rowid WHERE chunks_fts MATCH '\"inscripcion\"' LIMIT 1"
```

Comprobación local reproducible (no requiere login; usa una configuración temporal fuera del repo, ver `infra/README.md`):

```sh
npx wrangler d1 migrations apply DB --local --persist-to <dir>
npx wrangler d1 execute DB --local --persist-to <dir> --command "SELECT sql FROM sqlite_master WHERE name = 'chunks_fts'"
```
