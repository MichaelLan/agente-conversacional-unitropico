# infra

Definiciones de datos compartidas por los workers: esquema D1, consultas canónicas y diseño de Vectorize.
Aquí **no** hay código de runtime ni configuración de despliegue. Cada app copia lo que necesite (tipos manuales, sin paquetes compartidos, como en el resto del monorepo).

## Política

**Nada se crea en remoto sin autorización explícita del usuario.** Eso incluye bases D1, índices Vectorize, buckets R2, migraciones `--remote` y despliegues. Ningún comando de este directorio usa una cuenta de Cloudflare, salvo los marcados como remotos, que no se han ejecutado. CI no aplica migraciones remotas.

## Contenido

| Ruta | Qué es |
|---|---|
| `d1/migrations/0001_initial.sql` | Migración inicial en formato `wrangler d1 migrations` (`NNNN_nombre.sql`, sin `BEGIN`/`COMMIT`): `documents`, `document_versions`, `chunks`, `chunks_fts` (FTS5 external-content, `unicode61 remove_diacritics 2`), `processing_events` (append-only: triggers que abortan `UPDATE` y `DELETE` con `processing_events_append_only`; pasos admitidos incluyen `correct`), `text_corrections` (correcciones literales de OCR con borrado lógico) y la vista `v_chunk_registry`. |
| `d1/queries.sql` | SQL canónico de los adaptadores (búsqueda FTS, catálogo, ingesta idempotente, mantenimiento). |
| `d1/queries.md` | Cómo usar esas consultas, el algoritmo de escape FTS5 y los límites de D1. |
| `d1/tests/fts_query.py` | Referencia pura de `build_fts5_query` (el RAG la copia). |
| `d1/tests/test_schema.py`, `d1/tests/test_fts_query.py` | Verificación con SQLite real (unittest, stdlib). |
| `vectorize/README.md` | Diseño del índice, metadatos, comandos (no ejecutados) y límites. |

## Modelo de datos (resumen)

- `documents` 1─N `document_versions` 1─N `chunks`. `chunks_fts` indexa `chunks(text, section)` y lo mantienen sincronizado los triggers.
- La **vigencia no depende del orden de carga**. `effective_from`, `effective_until` (fechas `YYYY-MM-DD`) y `supersedes_version_id` son metadatos manuales del administrador. Una versión está *formalmente sustituida* solo cuando otra versión **publicada** la sustituye (`v_chunk_registry.formally_superseded`).
- La lógica de estados, la publicación (revisada, con chunks y con páginas válidas) y la inmutabilidad de los chunks publicados las aplican CHECKs y triggers. No dependen solo del código de las apps.
- `document_versions` 1─N `text_corrections`: correcciones literales del texto OCR por página (`find_text` ≤ 500 no vacío, `replace_text` ≤ 500 que puede ser vacío, `reason` ≤ 300 no vacío). Las filas no se borran ni se editan: solo `deleted_at` pasa una vez de NULL a la hora de la base (borrado lógico, trazabilidad). Triggers: no se insertan ni se borran lógicamente en una versión `published` (`published_version_immutable`), ni con `page_number > page_count` conocido (`correction_page_out_of_range`); `DELETE` aborta con `text_corrections_append_only`. La ingesta aplica las activas en el paso `chunk` (ver `workers/ingestion/README.md`); el OCR cacheado en R2 no cambia. Índice único parcial `text_corrections_active_unique (version_id, page_number, find_text) WHERE deleted_at IS NULL` (sin duplicados activos); orden de aplicación `created_at, rowid`. Cada alta o borrado lógico pone `document_versions.corrections_dirty = 1` e incrementa `corrections_rev` (triggers `text_corrections_dirty_*`); solo el paso `chunk` de la ingesta limpia la marca, por compare-and-set sobre `corrections_rev` (`clear_corrections_dirty`). Con `corrections_dirty = 1` no se puede publicar: `document_versions_publish_guard` aborta con `corrections_pending` y el `CHECK` de `published` exige `corrections_dirty = 0`.
- `chunks.seq INTEGER PRIMARY KEY` es el rowid estable del índice FTS (un rowid implícito puede cambiar con `VACUUM`). `chunks.id` (≤64 bytes) es el id público y el id del vector en Vectorize.
- Los campos de cita del contrato v1 (`document_title`, `version_label`, `section`, `page_start`, `page_end`) salen de `v_chunk_registry`, nunca del LLM.

## Verificación local

Desde la raíz del repo. No hace falta proyecto, dependencias ni red:

```sh
uv run --no-project --python 3.14 python -m unittest discover -s infra/d1/tests -v
uvx ruff check infra && uvx ruff format --check infra
```

**Por qué unittest sin `pyproject.toml`**: solo se necesita `sqlite3` de la stdlib. Un proyecto con pytest añadiría un lockfile y dependencias sin aportar nada. Además, `evaluations/ocr` ya usa unittest. Si más adelante `infra` tiene CI propio, bastará con un workflow que ejecute los dos comandos anteriores.

### Aplicar la migración con wrangler en local (sin login)

`wrangler d1 ... --local` usa la SQLite de workerd/miniflare y no contacta con la cuenta. Hace falta un `wrangler.jsonc` con el binding. Usa uno temporal fuera del repo, con un `database_id` ficticio:

```jsonc
{
  "name": "d1-schema-local-check",
  "compatibility_date": "2026-11-08",
  "d1_databases": [{
    "binding": "DB",
    "database_name": "unitropico-local-check",
    "database_id": "00000000-0000-0000-0000-000000000000",
    "migrations_dir": "<ruta absoluta>/infra/d1/migrations"
  }]
}
```

```sh
npx wrangler d1 migrations apply DB --local --persist-to ./state
npx wrangler d1 execute DB --local --persist-to ./state --command "SELECT name FROM sqlite_master WHERE type = 'table'"
```

Ejecutado el 2026-11-18 con wrangler 4.165.0 (caché local de npx): «25 commands executed successfully». FTS con y sin tildes, triggers de update/delete, `integrity-check`, `json_each` y el trigger de transición de estados se comportan igual que en Python. **D1 remoto sigue sin verificar** (ver `d1/queries.md` §5).

## Bindings que deberán declararse (propuesta, no aplicada)

Las apps son dueñas de su `wrangler.jsonc`. Este directorio no lo modifica.

| Binding | Tipo | Recurso propuesto | Quién lo usa |
|---|---|---|---|
| `DB` | D1 | `unitropico` | RAG (lectura: FTS y catálogo), API admin e ingesta (escritura) |
| `VECTORIZE` | Vectorize | `unitropico-chunks-<modelo>-<dims>` | RAG (consulta), ingesta (upsert/delete) |
| `DOCUMENTS` | R2 | `unitropico-documents` | API admin (carga), ingesta (lectura de fuentes y escritura de extracciones) |

```jsonc
// apps/<worker>/wrangler.jsonc (fragmento; database_id se obtiene al crear la base remota)
"d1_databases": [{
  "binding": "DB",
  "database_name": "unitropico",
  "database_id": "<pendiente>",
  "migrations_dir": "../../infra/d1/migrations"
}],
"vectorize": [{ "binding": "VECTORIZE", "index_name": "unitropico-chunks-1536" }],
"r2_buckets": [{ "binding": "DOCUMENTS", "bucket_name": "unitropico-documents" }]
```

- Las migraciones remotas se aplican desde **un solo** worker, para que no haya dos `migrations_dir` con historiales distintos.
- Claves R2 recomendadas, deterministas e idempotentes:
  - `raw/{source_sha256}.pdf`
  - `ocr/{source_sha256}/{ocr_version}-p{max_pages}.json` (solo respuestas válidas y completas; `max_pages` = `OCR_MAX_PAGES`), `chunks/{version_id}/{chunker_version}.jsonl`, `embeddings/...`, `vectors/{version_id}/manifest.json`
  - `embeddings/{version_id}/{modelo}.json`

Comandos remotos de referencia (**NO ejecutados**, requieren autorización):

```sh
npx wrangler d1 create unitropico
npx wrangler r2 bucket create unitropico-documents
npx wrangler d1 migrations apply DB --remote      # desde el worker que declara migrations_dir
```
