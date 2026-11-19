"""Aggregate per-item records into summaries and render Markdown reports."""

from collections import Counter
from collections.abc import Callable, Iterable

from unitropico_eval import metrics

COST_UNAVAILABLE = (
    "no medible desde el contrato actual: /answer no expone `usage` (tokens) "
    "ni el modelo/precio efectivos"
)


def _groups(records: list[dict]) -> dict[str, list[dict]]:
    groups: dict[str, list[dict]] = {"all": records}
    for field in ("category", "split"):
        for value in sorted({r[field] for r in records}):
            groups[f"{field}={value}"] = [r for r in records if r[field] == value]
    return groups


def _latency(records: Iterable[dict]) -> dict:
    values = [r["latency_ms"] for r in records if r.get("http_status") is not None]
    return {
        "n": len(values),
        "p50_ms": metrics.percentile(values, 50),
        "p95_ms": metrics.percentile(values, 95),
    }


def _retrieval_block(records: list[dict]) -> dict:
    scored = [r for r in records if r.get("metrics")]
    oos = [r for r in records if r["expected_behavior"] == "abstain"]
    oos_ok = [r for r in oos if r.get("http_status") == 200]
    return {
        "n_items": len(records),
        "n_scored": len(scored),
        "n_errors": sum(1 for r in records if r.get("error")),
        "recall_at_k": metrics.mean(r["metrics"]["recall_at_k"] for r in scored),
        "mrr": metrics.mean(r["metrics"]["reciprocal_rank"] for r in scored),
        "ndcg_at_k": metrics.mean(r["metrics"]["ndcg_at_k"] for r in scored),
        "gold_coverage_at_k": metrics.mean(
            r["metrics"]["gold_coverage_at_k"] for r in scored
        ),
        "out_of_scope_retrieval_abstained": (
            sum(1 for r in oos_ok if r.get("status") == "abstained") / len(oos_ok)
            if oos_ok
            else None
        ),
        "unmapped_results": sum(r.get("unmapped_results", 0) for r in records),
    }


def summarize_retrieval(records: list[dict]) -> dict:
    """Recall@k, MRR, nDCG@k overall and by category/split, plus latency."""
    return {
        "groups": {name: _retrieval_block(rs) for name, rs in _groups(records).items()},
        "latency": _latency(records),
    }


def _ratio(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def _answer_block(records: list[dict]) -> dict:
    outcomes = Counter(r["outcome"] for r in records)
    answerable = [
        r
        for r in records
        if r["expected_behavior"] == "answer" and r["outcome"] != "error"
    ]
    abstain = [
        r
        for r in records
        if r["expected_behavior"] == "abstain" and r["outcome"] != "error"
    ]
    scored = [r for r in records if r.get("citation_accuracy") is not None]
    total_citations = sum(len(r["citations"]) for r in scored)
    matching = sum(sum(c["matches_gold"] for c in r["citations"]) for r in scored)
    valid = [r for r in records if r["outcome"] != "error"]
    return {
        "n_items": len(records),
        "n_errors": outcomes["error"],
        "outcomes": dict(sorted(outcomes.items())),
        "behavior_accuracy": _ratio(
            outcomes["correct_answer"] + outcomes["correct_abstention"], len(valid)
        ),
        "answer_rate_on_answerable": _ratio(
            outcomes["correct_answer"], len(answerable)
        ),
        "abstention_rate_on_out_of_scope": _ratio(
            outcomes["correct_abstention"], len(abstain)
        ),
        "citation_precision_micro": _ratio(matching, total_citations),
        "citation_precision_macro": metrics.mean(
            r["citation_accuracy"] for r in scored
        ),
        "citation_coverage_mean": metrics.mean(r["citation_coverage"] for r in scored),
        "answered_without_gold_citation": _ratio(
            sum(1 for r in scored if not r["has_gold_citation"]), len(scored)
        ),
        "n_answered_with_gold": len(scored),
        "reasons": dict(
            sorted(
                Counter(
                    r["reason"] for r in records if r["status"] == "abstained"
                ).items()
            )
        ),
    }


def _usage(records: list[dict]) -> dict | str:
    usages = [r["usage"] for r in records if r.get("usage")]
    if not usages:
        return COST_UNAVAILABLE
    totals: Counter = Counter()
    for usage in usages:
        for key, value in usage.items():
            if isinstance(value, int | float) and not isinstance(value, bool):
                totals[key] += value
    return {"responses_with_usage": len(usages), "totals": dict(totals)}


def summarize_answer(records: list[dict]) -> dict:
    """Abstention, citation and latency metrics overall and by group."""
    return {
        "groups": {name: _answer_block(rs) for name, rs in _groups(records).items()},
        "latency": _latency(records),
        "latency_http_200": _latency(r for r in records if r.get("http_status") == 200),
        "cost": _usage(records),
    }


def _fmt(value: object, percent: bool = False) -> str:
    if value is None:
        return "—"
    if isinstance(value, float):
        return f"{value * 100:.1f} %" if percent else f"{value:.3f}"
    return str(value)


def _ms(value: float | None) -> str:
    return "—" if value is None else f"{value:.1f}"


def _table(header: list[str], rows: list[list[str]]) -> list[str]:
    lines = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    lines.extend("| " + " | ".join(row) + " |" for row in rows)
    return lines


def _metadata_lines(meta: dict) -> list[str]:
    keys = [
        ("command", "Subcomando"),
        ("label", "Etiqueta"),
        ("declared_retrieval_mode", "RETRIEVAL_MODE declarado (no verificado)"),
        ("base_url", "base_url"),
        ("path", "Ruta"),
        ("k", "k"),
        ("split", "Split"),
        ("categories", "Categorías"),
        ("include_blocked", "Incluye bloqueados por OCR"),
        ("dataset_path", "Dataset"),
        ("dataset_sha256", "sha256 del dataset"),
        ("dataset_size", "Ítems en el dataset"),
        ("items_evaluated", "Ítems evaluados"),
        ("validated_items", "Ítems validados por personal"),
        ("started_at", "Inicio (UTC)"),
        ("finished_at", "Fin (UTC)"),
        ("git_commit", "Commit git"),
        ("git_dirty", "Árbol con cambios sin commit"),
    ]
    rows = [[label, f"`{meta.get(key)}`"] for key, label in keys if key in meta]
    return _table(["Campo", "Valor"], rows)


def _warnings(meta: dict) -> list[str]:
    lines = []
    if meta.get("note"):
        lines.append(f"> **Nota de la corrida:** {meta['note']}")
        lines.append("")
    if meta.get("validated_items", 0) < meta.get("items_evaluated", 0):
        lines.append(
            "> **Dataset NO validado.** "
            f"{meta['items_evaluated'] - meta.get('validated_items', 0)} de "
            f"{meta['items_evaluated']} ítems son borradores redactados desde el "
            "corpus (`validated: false`). Estas cifras no son resultados "
            "reportables hasta que Bienestar Universitario valide preguntas y oro."
        )
        lines.append("")
    return lines


def _group_rows(
    summary: dict, columns: list[tuple[str, str, bool]]
) -> tuple[list[str], list[list[str]]]:
    header = ["Grupo", *[title for _, title, _ in columns]]
    rows = [
        [name, *[_fmt(block.get(key), pct) for key, _, pct in columns]]
        for name, block in summary["groups"].items()
    ]
    return header, rows


RETRIEVAL_COLUMNS = [
    ("n_items", "n", False),
    ("n_scored", "evaluables", False),
    ("n_errors", "errores", False),
    ("recall_at_k", "Recall@k", True),
    ("mrr", "MRR", False),
    ("ndcg_at_k", "nDCG@k", False),
    ("gold_coverage_at_k", "cobertura oro@k", True),
    ("unmapped_results", "sin mapear", False),
]
ANSWER_COLUMNS = [
    ("n_items", "n", False),
    ("n_errors", "errores", False),
    ("behavior_accuracy", "acierto conducta", True),
    ("answer_rate_on_answerable", "responde (answer)", True),
    ("abstention_rate_on_out_of_scope", "se abstiene (abstain)", True),
    ("citation_precision_micro", "exactitud citas (micro)", True),
    ("citation_coverage_mean", "cobertura citas", True),
    ("answered_without_gold_citation", "sin cita de oro", True),
]


def render_markdown(run: dict) -> str:
    """Render a run (meta + summary + items) as a Markdown report."""
    meta, summary = run["meta"], run["summary"]
    lines = [
        f"# Evaluación RAG — {meta['command']} — {meta.get('label') or 'sin etiqueta'}",
        "",
    ]
    lines += _warnings(meta)
    lines += ["## Metadatos", "", *_metadata_lines(meta), ""]
    columns = RETRIEVAL_COLUMNS if meta["command"] == "retrieval" else ANSWER_COLUMNS
    header, rows = _group_rows(summary, columns)
    lines += ["## Resultados por grupo", "", *_table(header, rows), ""]
    latency = summary["latency"]
    lines += [
        "## Latencia (cliente)",
        "",
        f"p50 {_ms(latency['p50_ms'])} ms · p95 {_ms(latency['p95_ms'])} ms "
        f"(n={latency['n']}, incluye respuestas no 200).",
        "",
    ]
    if meta["command"] == "retrieval":
        oos = summary["groups"]["all"]["out_of_scope_retrieval_abstained"]
        unmapped = summary["groups"]["all"]["unmapped_results"]
        if unmapped:
            lines += [
                f"> **{unmapped} resultados sin mapear** a un documento del dataset: "
                "completar `datasets/document_map.json` o revisar el índice.",
                "",
            ]
        lines += [
            "Fuera de alcance: fracción de `abstained` en `/retrieval/query` = "
            f"{_fmt(oos, True)} (informativo: la recuperación no decide alcance).",
            "",
        ]
    else:
        reasons = summary["groups"]["all"]["reasons"]
        lines += ["## Motivos de abstención", ""]
        lines += [f"- `{k}`: {v}" for k, v in reasons.items()] or ["- (ninguna)"]
        cost = summary["cost"]
        lines += [
            "",
            "## Costo",
            "",
            cost if isinstance(cost, str) else f"`{cost}`",
            "",
        ]
        lines += [
            "## Fidelidad",
            "",
            "No calculada automáticamente. Exportar con `export-annotations` y "
            "anotar a mano (o con un juez externo documentado en el README).",
            "",
        ]
    lines += _failures(run)
    return "\n".join(lines).rstrip() + "\n"


def _failures(run: dict) -> list[str]:
    command = run["meta"]["command"]
    if command == "retrieval":
        misses = [
            r
            for r in run["items"]
            if r.get("metrics") and not r["metrics"]["recall_at_k"]
        ]
        title, describe = (
            "Fallos de recuperación (sin oro en top-k)",
            _describe_retrieval,
        )
    else:
        misses = [
            r
            for r in run["items"]
            if r["outcome"] in ("wrong_answer", "wrong_abstention", "error")
            or r.get("has_gold_citation") is False
        ]
        title, describe = "Casos a revisar", _describe_answer
    if not misses:
        return []
    return ["## " + title, "", *[describe(r) for r in misses], ""]


def _describe_retrieval(record: dict) -> str:
    got = ", ".join(
        f"{r['document'] or r['document_title']} p{r['page_start']}-{r['page_end']}"
        for r in record["results"][:3]
    )
    return f"- `{record['id']}` ({record['category']}): top3 = {got or 'vacío'}"


def _describe_answer(record: dict) -> str:
    detail = record["error"] or record["reason"] or ""
    head = f"- `{record['id']}` ({record['category']}, "
    head += f"esperado {record['expected_behavior']}): "
    return f"{head}{record['outcome']} {detail}".rstrip()


def _cell(run: dict, key: str, pct: bool) -> str:
    return _fmt(run["summary"]["groups"]["all"].get(key), pct)


def render_comparison(runs: list[dict]) -> str:
    """Side-by-side table of several runs of the same subcommand."""
    commands = {r["meta"]["command"] for r in runs}
    if len(commands) != 1:
        raise ValueError("solo se comparan corridas del mismo subcomando")
    command = commands.pop()
    columns = RETRIEVAL_COLUMNS if command == "retrieval" else ANSWER_COLUMNS
    header = ["Etiqueta", "Modo declarado", "k", "split", *[t for _, t, _ in columns]]
    rows = [
        [
            str(r["meta"].get("label")),
            str(r["meta"].get("declared_retrieval_mode")),
            str(r["meta"].get("k")),
            str(r["meta"].get("split")),
            *[_cell(r, key, pct) for key, _, pct in columns],
        ]
        for r in runs
    ]
    lines = [f"# Comparación de corridas — {command}", ""]
    lines += _comparison_warnings(runs, lambda m: m.get("dataset_sha256"), "dataset")
    lines += _comparison_warnings(
        runs, lambda m: (m.get("k"), m.get("split")), "k/split"
    )
    if any(
        r["meta"].get("validated_items", 0) < r["meta"].get("items_evaluated", 0)
        for r in runs
    ):
        lines += ["> **Al menos una corrida usa ítems no validados.**", ""]
    lines += _table(header, rows)
    return "\n".join(lines) + "\n"


def _comparison_warnings(
    runs: list[dict], key: Callable[[dict], object], what: str
) -> list[str]:
    if len({str(key(r["meta"])) for r in runs}) > 1:
        return [
            f"> **Atención:** las corridas difieren en {what}; no son comparables.",
            "",
        ]
    return []
