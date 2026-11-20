"""Assemble the FINAL ingestion OCR cache and the corrections seed (offline).

Inputs: ``reports/alt-ocr-v2-audit.json`` → ``final_source_plan`` (source of
every physical page of the 7 PDFs) and the required corrections of
``alt-ocr-v2-audit.json`` (61 + 4 pilot) and ``selective-ocr-v1-audit.json``
(55 applicable). See ``final_sources.py`` / ``final_corrections.py``.

Outputs (exclusive; an existing output directory blocks the run):

* ``.artifacts/ingestion-cache-v2/``: one Firecrawl-Parse-v2-shaped JSON per
  PDF under the R2 key the Workflow ``ocr`` step reads
  (``ocr/{sha256}/{OCR_VERSION}-p{OCR_MAX_PAGES}.json``), page markdown taken
  verbatim from the planned source (corrections are NOT baked in: they are
  registered in D1 for traceability), ``source`` per page and
  ``data.provenance``; ``upload-plan.md`` (unexecuted wrangler commands);
  ``assembly.json``.
* ``.artifacts/corrections-seed-v1/``: ``corrections.json``,
  ``page_overrides.json`` (DT p54 proposal, ``requires_human_verification``)
  and ``validation.json`` (per-correction outcome, chunks per document).

Validation uses the real ingestion code (``final_corrections.ingestion``):
parse_ocr_response → StructuralChunker.prepare → (override) →
apply_corrections → chunk_prepared. No network, no uploads, no ``.env``.
"""

import argparse
import hashlib
import json
import sys
from pathlib import Path

sys.dont_write_bytecode = True
import final_override
from assemble_cache import BUCKET, OCR_MAX_PAGES, OCR_VERSION, encode, write_bytes
from final_corrections import (
    Seed,
    check_seeds,
    final_check,
    ingestion,
    tidy,
    validate_document,
)
from final_sources import (
    EXPECTED_CORRECTION_PAGES,
    EXPECTED_CORRECTIONS,
    REPO,
    SOURCES,
    V1_AUDIT,
    V2_AUDIT,
    Document,
    SourceStore,
    build_documents,
    load_corrections,
    load_json,
    load_plan,
    rel,
    require,
)
from pilot import ARTIFACTS, digest, error_category, write_json
from prepare_selective import ORIGINALS
from remaining_send import safe

CACHE_OUT = ARTIFACTS / "ingestion-cache-v2"
SEED_OUT = ARTIFACTS / "corrections-seed-v1"
CACHE_SCHEMA = "unitropico-ingestion-cache/v2"
SEED_SCHEMA = "unitropico-corrections-seed/v1"


def cache_key(source_sha256: str) -> str:
    """Same key as ``unitropico_ingestion.services.ingestion.ocr_key``."""
    return f"ocr/{source_sha256}/{OCR_VERSION}-p{OCR_MAX_PAGES}.json"


def cache_body(doc: Document, audit_sha: dict) -> dict:
    """Firecrawl Parse v2 shape; ``source``/``provenance`` are ignored by the parser."""
    pages = [
        {"pageNumber": p.number, "markdown": p.markdown, "source": p.source}
        for p in doc.pages
    ]
    markdown = pages[0]["markdown"]
    for page in pages[1:]:
        markdown += f"\n\n<!-- page {page['pageNumber']} -->\n\n" + page["markdown"]
    artifacts = {}
    for p in doc.pages:
        artifacts[p.artifact] = p.artifact_sha256
    return {
        "success": True,
        "data": {
            "markdown": markdown,
            "pages": pages,
            "metadata": {"numPages": len(pages), "totalPages": len(pages)},
            "provenance": {
                "schema": CACHE_SCHEMA,
                "document": doc.slug,
                "source_path": doc.source_path,
                "source_sha256": doc.source_sha256,
                "plan": "evaluations/ocr/reports/alt-ocr-v2-audit.json final_source_plan",
                "audits_sha256": audit_sha,
                "corrections_applied": False,
                "corrections_note": (
                    "Markdown verbatim from each source; corrections and page "
                    "overrides are registered in D1 (corrections-seed-v1)."
                ),
                "artifacts_sha256": artifacts,
                "page_sources": {
                    s: [p.number for p in doc.pages if p.source == s] for s in SOURCES
                },
                "pages": [
                    {
                        "pageNumber": p.number,
                        "source": p.source,
                        "artifact": p.artifact,
                        "artifact_sha256": p.artifact_sha256,
                        "locator": p.locator,
                    }
                    for p in doc.pages
                ],
                "requires_review": True,
            },
        },
    }


def check_parser(raw: bytes, doc: Document, api: dict) -> None:
    """Round-trip through the real ``parse_ocr_response``."""
    result = api["parse_ocr_response"](raw)
    require(
        result.page_count == doc.page_count
        and result.failed_pages == ()
        and result.text_pages == doc.page_count
        and [(p.page_number, p.markdown) for p in result.pages]
        == [(p.number, p.markdown) for p in doc.pages],
        "Ingestion parser mismatch",
    )


def upload_plan(entries: list[dict], output: Path) -> str:
    """Markdown with unexecuted wrangler commands, run from the repo root."""
    lines = [
        "# Plan de subida de la caché OCR final (NO ejecutado)",
        "",
        "Ejecutar desde la raíz del repositorio, con `wrangler` autenticado, ANTES de",
        "subir cada PDF por el panel: si la caché no está, el paso `ocr` del Workflow",
        "pagará un OCR completo del documento (Firecrawl).",
        "",
        "La caché es por SHA-256 del PDF: solo se reutiliza si el archivo subido es",
        "byte a byte el original listado aquí. Si ya existe una caché v1 con la misma",
        "clave, este `put` la sustituye (es lo buscado: v2 es la fuente final).",
        "",
        f"Claves con `OCR_VERSION={OCR_VERSION}` y `OCR_MAX_PAGES={OCR_MAX_PAGES}`",
        "(valores por defecto de workers/ingestion: settings.py y wrangler.jsonc).",
        "Si cambian, regenerar.",
        "",
        "```sh",
    ]
    for entry in entries:
        path = output / entry["key"]
        if path.is_relative_to(REPO):
            path = path.relative_to(REPO)
        lines.append(f"# {entry['source_path']} ({entry['page_count']} págs.)")
        lines.append(
            f"npx wrangler r2 object put {BUCKET}/{entry['key']} --file={path} "
            "--content-type=application/json --remote"
        )
    lines += ["```", ""]
    return "\n".join(lines)


def verify_originals(plan: dict, check_files: bool = True) -> dict[str, str]:
    """PDF identity from the pinned originals (and their bytes when available)."""
    identities = {}
    for source_path, items in plan.items():
        require(source_path in ORIGINALS, "Unknown original")
        _, sha, pages = ORIGINALS[source_path]
        require(pages == len(items), "Original page count mismatch")
        if check_files:
            data = safe(REPO / source_path).read_bytes()
            require(hashlib.sha256(data).hexdigest() == sha, "Original mismatch")
        identities[source_path] = sha
    return identities


def build(store: SourceStore | None = None, check_files: bool = True) -> dict:
    """Everything in memory, fully validated; nothing written."""
    api = ingestion()
    v2, v2_sha = load_json(V2_AUDIT)
    v1, v1_sha = load_json(V1_AUDIT)
    audit_sha = {rel(V2_AUDIT): v2_sha, rel(V1_AUDIT): v1_sha}
    plan = load_plan(v2)
    identities = verify_originals(plan, check_files)
    documents = build_documents(plan, store or SourceStore(), identities)
    corrections = load_corrections(v2, v1, plan)
    require(len(corrections) == EXPECTED_CORRECTIONS, "Correction total mismatch")
    pages_with = {(c.document, c.page) for c in corrections}
    require(len(pages_with) == EXPECTED_CORRECTION_PAGES, "Correction pages mismatch")
    planned = {
        (i["document"], i["physical_page"]): i["corrections_required"]
        for items in plan.values()
        for i in items
    }
    for key, count in planned.items():
        mine = sum((c.document, c.page) == key for c in corrections)
        require(mine == count, "Per-page correction count differs from plan")

    seeds: list[Seed] = []
    outcomes = []
    docs_out = []
    validated_pages: dict[str, dict[int, str]] = {}
    for doc in documents:
        mine = [(i, c) for i, c in enumerate(corrections) if c.document == doc.slug]
        doc_seeds, doc_outcomes, validated = validate_document(doc, mine, api)
        raw = encode(cache_body(doc, audit_sha))
        check_parser(raw, doc, api)
        prepared = {
            p.page_number: p.text
            for p in api["chunker"].prepare(
                [api["PageText"](p.number, p.markdown) for p in doc.pages]
            )
        }
        missing = [p.number for p in doc.pages if not prepared.get(p.number)]
        require(not missing, f"Page empty after preparation {doc.slug} {missing}")
        seeds += doc_seeds
        outcomes += doc_outcomes
        docs_out.append((doc, raw, prepared))
        validated_pages[doc.slug] = validated
    check_seeds(seeds, api)

    # DT p54: full-page override proposal; its pilot correction is integrated.
    dt_doc, _, dt_prepared = next(
        d for d in docs_out if d[0].slug == "documento-tecnico"
    )
    override = final_override.override_text(dt_prepared[final_override.PAGE])
    integrated = [
        s
        for s in seeds
        if s.document == final_override.DOCUMENT
        and s.page == final_override.PAGE
        and s.find_text == final_override.INTEGRATED_FIND
    ]
    require(len(integrated) == 1, "Integrated pilot correction missing")
    others_p54 = [
        s
        for s in seeds
        if s.document == final_override.DOCUMENT
        and s.page == final_override.PAGE
        and s not in integrated
    ]
    require(not others_p54, "Other corrections on the override page")

    checks = {}
    for doc, raw, _ in docs_out:
        with_override = doc.slug == final_override.DOCUMENT
        active = [s for s in seeds if not (with_override and s in integrated)]
        result = final_check(
            doc,
            active,
            raw,
            api,
            override=(final_override.PAGE, override) if with_override else None,
            expected_pages={
                n: t
                for n, t in validated_pages[doc.slug].items()
                if not (with_override and n == final_override.PAGE)
            },
        )
        entry = {k: v for k, v in result.items() if k != "corrected_pages"}
        if with_override:
            text = result["corrected_pages"][final_override.PAGE]
            require(
                tidy(text)
                == tidy(
                    api["chunker"]
                    .prepare([api["PageText"](final_override.PAGE, override)])[0]
                    .text
                ),
                "Override not applied as prepared",
            )
            plain = final_check(
                doc, seeds, raw, api, expected_pages=validated_pages[doc.slug]
            )
            entry["without_override"] = {
                k: v for k, v in plain.items() if k != "corrected_pages"
            }
        checks[doc.slug] = entry
    return {
        "api": api,
        "documents": docs_out,
        "corrections": corrections,
        "seeds": seeds,
        "outcomes": outcomes,
        "integrated": integrated,
        "override": override,
        "checks": checks,
        "audit_sha": audit_sha,
    }


def seed_payloads(state: dict) -> tuple[dict, dict, dict]:
    """corrections.json, page_overrides.json and validation.json bodies."""
    corrections = state["corrections"]
    integrated = state["integrated"]
    docs = []
    for doc, _, _ in state["documents"]:
        entries = []
        for seq, s in enumerate(
            (s for s in state["seeds"] if s.document == doc.slug), 1
        ):
            entry = {
                "seq": seq,
                "page_number": s.page,
                "find_text": s.find_text,
                "replace_text": s.replace_text,
                "reason": s.reason,
                "origin": s.origin,
                "status": s.status,
                "only_without_override": s in integrated,
            }
            if s.reformulated_from:
                entry["reformulated_from"] = [
                    {
                        "origin": corrections[i].origin,
                        "find_text": corrections[i].find_text,
                        "replace_text": corrections[i].replace_text,
                    }
                    for i in s.reformulated_from
                ]
            entries.append(entry)
        docs.append(
            {
                "document": doc.slug,
                "source_filename": Path(doc.source_path).name,
                "source_path": doc.source_path,
                "source_sha256": doc.source_sha256,
                "page_count": doc.page_count,
                "cache_key": cache_key(doc.source_sha256),
                "corrections": entries,
            }
        )
    omitted = [
        {
            "document": corrections[o.index].document,
            "page_number": corrections[o.index].page,
            "find_text": corrections[o.index].find_text,
            "replace_text": corrections[o.index].replace_text,
            "origin": corrections[o.index].origin,
            "note": o.note,
        }
        for o in state["outcomes"]
        if o.status == "omitted"
    ]
    statuses = {}
    for o in state["outcomes"]:
        statuses[o.status] = statuses.get(o.status, 0) + 1
    dt = next(d for d, _, _ in state["documents"] if d.slug == final_override.DOCUMENT)
    corrections_body = {
        "schema": SEED_SCHEMA,
        "requires_review": True,
        "match_rule": (
            "find_text se busca en el texto PREPARADO de la página (normalizado, "
            "sin cabeceras/pies repetidos, tablas HTML como filas Markdown); "
            "literal salvo espacios; exactamente una aparición. Registrar en el "
            "orden de 'seq' (las de una misma página se aplican por orden de alta)."
        ),
        "inputs_sha256": state["audit_sha"],
        "totals": {
            "audit_corrections": len(corrections),
            "audit_outcomes": statuses,
            "seeds": len(state["seeds"]),
            "seeds_verbatim": sum(s.status == "verbatim" for s in state["seeds"]),
            "seeds_reformulated": sum(
                s.status == "reformulated" for s in state["seeds"]
            ),
            "seeds_only_without_override": len(integrated),
        },
        "documents": docs,
        "omitted": omitted,
    }
    overrides_body = {
        "schema": "unitropico-page-overrides-seed/v1",
        "overrides": [
            {
                "document": dt.slug,
                "source_filename": Path(dt.source_path).name,
                "source_path": dt.source_path,
                "source_sha256": dt.source_sha256,
                "page_number": final_override.PAGE,
                "text": state["override"],
                "reason": final_override.REASON,
                "requires_human_verification": True,
                "verification": (
                    "Comparar el bloque 'Insumos' con el PNG de la página 54 "
                    "(render PyMuPDF del original, ver README). Rótulos del flujo "
                    "superior (salvo 'Insumos') de muy bajo contraste: lectura "
                    "propuesta. Integra la corrección del piloto 'argo plaza' → "
                    "'largo plazo'."
                ),
                "base": (
                    "texto preparado de azure_pilot_v1 (alt-ocr-pilot-v1 export) "
                    "con el bloque <figure> de Insumos sustituido"
                ),
                "integrates_corrections": [
                    {"find_text": s.find_text, "replace_text": s.replace_text}
                    for s in integrated
                ],
            }
        ],
    }
    validation = {
        "schema": "unitropico-corrections-validation/v1",
        "ingestion_code": "workers/ingestion/src (sys.path explícito, solo lectura)",
        "chunker": "StructuralChunker() por defecto (worker.py)",
        "outcomes": [
            {
                "index": o.index,
                "document": corrections[o.index].document,
                "page_number": corrections[o.index].page,
                "origin": corrections[o.index].origin,
                "status": o.status,
                "matches_in_prepared_page": o.matches,
                "note": o.note,
            }
            for o in state["outcomes"]
        ],
        "documents": state["checks"],
    }
    return corrections_body, overrides_body, validation


def write_outputs(
    state: dict, cache_out: Path = CACHE_OUT, seed_out: Path = SEED_OUT
) -> list[dict]:
    """Exclusive writes of both output directories."""
    require(not cache_out.exists() and not seed_out.exists(), "Output exists")
    cache_out.mkdir(parents=True)
    seed_out.mkdir(parents=True)
    entries = []
    for doc, raw, _ in state["documents"]:
        key = cache_key(doc.source_sha256)
        write_bytes(cache_out / key, raw)
        entries.append(
            {
                "document": doc.slug,
                "source_path": doc.source_path,
                "source_sha256": doc.source_sha256,
                "page_count": doc.page_count,
                "key": key,
                "sha256": digest(raw),
                "bytes": len(raw),
                "pages_by_source": {
                    s: sum(p.source == s for p in doc.pages) for s in SOURCES
                },
                "chunks": state["checks"][doc.slug]["chunks"],
            }
        )
    write_bytes(cache_out / "upload-plan.md", upload_plan(entries, cache_out).encode())
    write_json(
        cache_out / "assembly.json",
        {
            "schema_version": 2,
            "state": "assembled_offline_not_uploaded",
            "requires_review": True,
            "ocr_version": OCR_VERSION,
            "ocr_max_pages": OCR_MAX_PAGES,
            "bucket": BUCKET,
            "audits_sha256": state["audit_sha"],
            "entries": entries,
        },
    )
    corrections_body, overrides_body, validation = seed_payloads(state)
    for name, body in (
        ("corrections.json", corrections_body),
        ("page_overrides.json", overrides_body),
        ("validation.json", validation),
    ):
        write_bytes(seed_out / name, encode(body))
    return entries


def main() -> int:
    """Offline only: fixed inputs and outputs; no network, upload or retries."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check", action="store_true", help="validate only, write nothing"
    )
    args = parser.parse_args()
    try:
        state = build()
        entries = [] if args.check else write_outputs(state)
    except Exception as error:  # noqa: BLE001 - toda falla se reporta por categoría
        label = str(error) if isinstance(error, ValueError) else ""
        print(
            f"Final assembly failed: {error_category(error)} {label}", file=sys.stderr
        )
        return 1
    totals = seed_payloads(state)[0]["totals"]
    for slug, check in state["checks"].items():
        print(
            f"{slug}: pages={check['pages']} corrections={check['corrections_applied']}"
            f" chunks={check['chunks']}"
        )
    print(json.dumps(totals, ensure_ascii=False, sort_keys=True))
    for entry in entries:
        print(f"{entry['key']} {entry['pages_by_source']}")
    print("Offline; nothing uploaded or registered." if entries else "Check only.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
