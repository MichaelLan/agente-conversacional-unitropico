# Vectorize: diseño del índice de chunks

Estado: **diseño, sin crear**. No se ha creado ningún índice. Los comandos de abajo crean recursos remotos y requieren autorización explícita del usuario. Todos los datos están verificados en la documentación oficial a fecha 2026-11-18.

## 1. Decisión pendiente: modelo de embeddings

La dimensión de un índice **no se puede cambiar** después de crearlo ([create indexes](https://developers.cloudflare.com/vectorize/best-practices/create-indexes/)). Por eso el nombre del índice incluye el modelo y la dimensión, y cambiar de modelo significa crear un índice nuevo y reindexar (azul/verde).

| Opción | Dimensión | Fuente | Notas |
|---|---|---|---|
| `@cf/baai/bge-m3` (Workers AI) | **1024** | [AI Search, modelos admitidos](https://developers.cloudflare.com/ai-search/configuration/models/supported-models/): «Vector dims 1,024», «Input tokens 512», métrica cosine | Multilingüe. La [ficha del modelo](https://developers.cloudflare.com/workers-ai/models/bge-m3/) **no** indica la dimensión de salida y su `sync-output.json` solo describe la respuesta asíncrona. El dato de 1024 viene de AI Search, que también es documentación oficial de Cloudflare. Hay que confirmarlo con una llamada real antes de crear el índice. Discrepancia: la ficha indica una ventana de contexto de 60 000 tokens y AI Search indica 512 tokens de entrada. Hasta aclararlo, mantén los chunks por debajo de 512 tokens. |
| `openai/text-embedding-3-small` | **1536** | Misma tabla de AI Search: «1,536», 8 192 tokens de entrada | 1536 es justo el máximo de Vectorize. Pasa por un proveedor externo (gasto). |

El preset `--preset` de `wrangler vectorize create` 4.165.0 no incluye bge-m3 (comprobado con `--help`), así que se pasan `--dimensions` y `--metric` de forma explícita.

## 2. Índice

- Nombre propuesto: `unitropico-chunks-<modelo>-<dims>`, por ejemplo `unitropico-chunks-1536` o `unitropico-chunks-te3small-1536`.
- Métrica: `cosine`.
- **id del vector = `chunks.id` de D1** (≤64 bytes, lo garantiza un CHECK en D1). El chunker (`workers/ingestion`) usa `{sha256(version_id‖chunker_version)[:16]}:{ordinal:04d}:{sha256(text)[:12]}` (35 bytes), determinista, con lo que los `upsert` son idempotentes.
- **No** se guarda el texto en los metadatos: el texto completo vive en D1 y en R2 ([ARQUITECTURA.md](../../ARQUITECTURA.md) §5.1).

### Metadatos por vector

| Propiedad | Tipo | Indexada | Uso |
|---|---|---|---|
| `version_id` | string | sí | Filtrar o borrar por versión y depurar. |
| `document_id` | string | sí | Filtrar por documento (por ejemplo, restringir a una convocatoria). |

- Los ids de D1 tienen ≤64 bytes. Vectorize indexa solo los primeros 64 bytes de cada string ([metadata filtering](https://developers.cloudflare.com/vectorize/reference/metadata-filtering/)), así que el filtrado por igualdad es exacto.
- Los **metadatos de Vectorize nunca son la fuente de verdad**. Pueden quedar desfasados (las mutaciones son asíncronas, «a few seconds»). Cada candidato se revalida en D1 con `registry_by_chunk_ids` + `is_admissible`. No se guarda `status` en Vectorize: la publicación vive solo en D1.
- Ciclo de vida decidido (2026-11-18, ver `docs/IMPLEMENTATION_STATUS.md`):
  - **Indexar**: el Workflow de ingesta hace `upsert` al terminar el procesamiento (versión `ready`/`review_required`). Los embeddings se guardan en R2 para no volver a pagarlos.
  - **Publicar/despublicar**: solo cambia D1; el RAG revalida en cada consulta.
  - **Reprocesar o reemplazar**: `deleteByIds` de los chunks antiguos (manifiesto `vectors/{version_id}/manifest.json` en R2).
  - `upsert` reemplaza el vector entero y no fusiona metadatos ([client API](https://developers.cloudflare.com/vectorize/reference/client-api/)). Para cambiar un metadato hay que reenviar los valores (que se recuperan de R2 o con `getByIds`).
- Los índices de metadatos se crean **antes** de insertar vectores. Los vectores insertados antes de crear un índice no aparecen en él hasta reinsertarlos ([metadata filtering](https://developers.cloudflare.com/vectorize/reference/metadata-filtering/)).

## 3. Comandos (NO ejecutados, requieren cuenta y autorización)

Modelo decidido: `openai/text-embedding-3-small`, 1536 dimensiones.

```sh
npx wrangler vectorize create unitropico-chunks-1536 --dimensions=1536 --metric=cosine \
  --description="Chunks Bienestar Unitropico, openai/text-embedding-3-small"
npx wrangler vectorize create-metadata-index unitropico-chunks-1536 --propertyName=version_id --type=string
npx wrangler vectorize create-metadata-index unitropico-chunks-1536 --propertyName=document_id --type=string
npx wrangler vectorize list-metadata-index unitropico-chunks-1536
npx wrangler vectorize info unitropico-chunks-1536
```

La opción se llama `--propertyName` según `wrangler vectorize create-metadata-index --help` (4.165.0) y la [referencia de comandos](https://developers.cloudflare.com/workers/wrangler/commands/vectorize/). La página de metadata filtering usa `--property-name` en un ejemplo, que yargs acepta como alias.

Binding (en el `wrangler.jsonc` del worker que consulte, ver `infra/README.md`):

```jsonc
"vectorize": [{ "binding": "VECTORIZE", "index_name": "unitropico-chunks-1536" }]
```

Consulta tipo (el RAG sobremuestrea y luego aplica RRF con FTS5):

```js
env.VECTORIZE.query(vector, { topK: 50, filter: { status: { $eq: "published" } }, returnMetadata: "indexed" })
```

## 4. Límites relevantes

Fuente: [Vectorize limits](https://developers.cloudflare.com/vectorize/platform/limits/) y [client API](https://developers.cloudflare.com/vectorize/reference/client-api/).

| Límite | Valor |
|---|---|
| Dimensiones máximas | 1536 (float32) |
| Longitud del id de vector | 64 bytes |
| Metadatos por vector | 10 KiB |
| Índices de metadatos por índice | 10 |
| Bytes indexados por string de metadatos | 64 B (truncado en límite UTF-8) |
| `topK` con `returnValues` o `returnMetadata: "all"` | 50 |
| `topK` sin valores ni metadatos completos (`"none"`/`"indexed"`) | 100 |
| Vectores por índice | 20 000 000 |
| Tamaño de lote de upsert | 1000 (Workers) / 5000 (HTTP API) |
| Tamaño del filtro | JSON compacto < 2048 bytes |
| Visibilidad de mutaciones | asíncrona, «a few seconds» |

El corpus actual (7 PDF, 216 páginas) queda muy por debajo de cualquier límite.
