"""Command line entry point: ``python -m unitropico_eval <subcommand>``."""

import argparse
import hashlib
import json
import platform
import subprocess
import sys
import urllib.parse
from datetime import UTC, datetime
from pathlib import Path

from unitropico_eval import annotation, report, runners
from unitropico_eval.client import JsonClient
from unitropico_eval.dataset import (
    CATEGORIES,
    DEFAULT_SEED,
    DatasetError,
    assign_splits,
    load_dataset,
    load_records,
    select,
    validate_dataset,
)
from unitropico_eval.docmap import DocumentMap

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DATASET = ROOT / "datasets" / "candidates.jsonl"
DEFAULT_DOCMAP = ROOT / "datasets" / "document_map.json"
DEFAULT_REPORTS = ROOT / "reports"
MODES = ("hybrid", "lexical", "semantic", "unknown")


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _git() -> tuple[str | None, bool | None]:
    try:
        commit = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=True,
            timeout=10,
        ).stdout.strip()
        dirty = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=True,
            timeout=10,
        ).stdout.strip()
    except OSError, subprocess.SubprocessError:
        return None, None
    return commit or None, bool(dirty)


def _safe_url(url: str) -> str:
    """Drop credentials, query and fragment before storing the URL."""
    parts = urllib.parse.urlsplit(url)
    host = parts.hostname or ""
    if parts.port:
        host = f"{host}:{parts.port}"
    return urllib.parse.urlunsplit((parts.scheme, host, parts.path, "", ""))


def _display_path(path: Path) -> str:
    """Path relative to the harness root when possible (no home directories)."""
    try:
        return str(path.resolve().relative_to(ROOT))
    except ValueError:
        return path.name


def _add_common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--document-map", type=Path, default=DEFAULT_DOCMAP)
    parser.add_argument("--split", choices=("dev", "test", "all"), default="dev")
    parser.add_argument("--category", action="append", choices=CATEGORIES)
    parser.add_argument("--include-blocked", action="store_true")


def _add_run(parser: argparse.ArgumentParser, default_path: str) -> None:
    _add_common(parser)
    parser.add_argument(
        "--base-url", required=True, help="p. ej. http://127.0.0.1:8790"
    )
    parser.add_argument("--path", default=default_path)
    parser.add_argument("--label", required=True, help="etiqueta de la corrida")
    parser.add_argument(
        "--mode",
        choices=MODES,
        default="unknown",
        help="RETRIEVAL_MODE configurado en el servidor (declarado, no verificable)",
    )
    parser.add_argument("--timeout", type=float, default=60.0)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_REPORTS)
    parser.add_argument("--note", default="", help="nota visible en el informe")


def build_parser() -> argparse.ArgumentParser:
    """Define subcommands and their options."""
    parser = argparse.ArgumentParser(
        prog="python -m unitropico_eval", description=__doc__
    )
    sub = parser.add_subparsers(dest="command", required=True)

    validate = sub.add_parser("validate", help="valida el dataset contra el esquema")
    validate.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    validate.add_argument("--document-map", type=Path, default=DEFAULT_DOCMAP)
    validate.add_argument("--seed", type=int, default=DEFAULT_SEED)

    retrieval = sub.add_parser("retrieval", help="evalúa POST /retrieval/query")
    _add_run(retrieval, "/retrieval/query")
    retrieval.add_argument("-k", "--k", type=int, default=5)

    answer = sub.add_parser("answer", help="evalúa POST /answer (o /chat vía API)")
    _add_run(answer, "/answer")

    compare = sub.add_parser("compare", help="tabla comparativa de corridas JSON")
    compare.add_argument("runs", nargs="+", type=Path)
    compare.add_argument("--out", type=Path)

    export = sub.add_parser("export-annotations", help="CSV para juicio de fidelidad")
    export.add_argument("run", type=Path)
    export.add_argument("--out", type=Path, required=True)
    export.add_argument("--all", action="store_true", help="incluye abstenciones")

    score = sub.add_parser("score-annotations", help="agrega un CSV ya anotado")
    score.add_argument("csv", type=Path)
    return parser


def _cmd_validate(args: argparse.Namespace) -> int:
    docmap = DocumentMap.load(args.document_map)
    records = load_records(args.dataset)
    errors = validate_dataset(records, docmap.page_counts)
    for error in errors:
        print(f"ERROR {error}", file=sys.stderr)
    expected = assign_splits(
        [(r["id"], r["category"]) for r in records], seed=args.seed
    )
    drift = [r["id"] for r in records if r.get("split") != expected.get(r["id"])]
    if drift:
        print(
            f"AVISO {len(drift)} ítems no coinciden con el split sembrado "
            f"(seed={args.seed}); correcto si el split ya fue congelado.",
            file=sys.stderr,
        )
    counts: dict[str, int] = {}
    for record in records:
        key = f"{record.get('category')}/{record.get('split')}"
        counts[key] = counts.get(key, 0) + 1
    print(
        json.dumps(
            {"items": len(records), "errors": len(errors), "by": counts}, indent=2
        )
    )
    return 1 if errors else 0


def _run(args: argparse.Namespace) -> int:
    docmap = DocumentMap.load(args.document_map)
    items = load_dataset(args.dataset, docmap.page_counts)
    chosen = select(items, args.split, tuple(args.category or ()), args.include_blocked)
    if not chosen:
        print("ningún ítem seleccionado", file=sys.stderr)
        return 2
    client = JsonClient(args.base_url, timeout=args.timeout)
    started = _now()

    def progress(item_id: str) -> None:
        print(f"· {item_id}", file=sys.stderr)

    if args.command == "retrieval":
        if not 1 <= args.k <= 20:
            print("k debe estar entre 1 y 20 (contrato del RAG)", file=sys.stderr)
            return 2
        records = runners.run_retrieval(
            chosen, client.post, docmap, args.k, args.path, progress
        )
        summary = report.summarize_retrieval(records)
    else:
        records = runners.run_answer(chosen, client.post, docmap, args.path, progress)
        summary = report.summarize_answer(records)
    commit, dirty = _git()
    meta = {
        "tool": "unitropico_eval 0.1.0",
        "command": args.command,
        "label": args.label,
        "declared_retrieval_mode": args.mode,
        "base_url": _safe_url(args.base_url),
        "path": args.path,
        "k": getattr(args, "k", None),
        "split": args.split,
        "categories": args.category or "all",
        "include_blocked": args.include_blocked,
        "dataset_path": _display_path(args.dataset),
        "dataset_sha256": hashlib.sha256(args.dataset.read_bytes()).hexdigest(),
        "dataset_size": len(items),
        "items_evaluated": len(chosen),
        "validated_items": sum(i.validated for i in chosen),
        "started_at": started,
        "finished_at": _now(),
        "git_commit": commit,
        "git_dirty": dirty,
        "python": platform.python_version(),
        "note": args.note,
    }
    run = {"meta": meta, "summary": summary, "items": records}
    args.out_dir.mkdir(parents=True, exist_ok=True)
    stamp = started.replace("-", "").replace(":", "")
    slug = "".join(c if c.isalnum() or c in "-_" else "-" for c in args.label)
    stem = args.out_dir / f"{stamp}-{args.command}-{slug}"
    stem.with_suffix(".json").write_text(
        json.dumps(run, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    stem.with_suffix(".md").write_text(report.render_markdown(run), encoding="utf-8")
    print(f"{stem}.json\n{stem}.md")
    return 0


def main(argv: list[str] | None = None) -> int:
    """Parse arguments and dispatch; return a process exit code."""
    args = build_parser().parse_args(argv)
    try:
        if args.command == "validate":
            return _cmd_validate(args)
        if args.command in ("retrieval", "answer"):
            return _run(args)
        if args.command == "compare":
            runs = [json.loads(p.read_text(encoding="utf-8")) for p in args.runs]
            text = report.render_comparison(runs)
            if args.out:
                args.out.write_text(text, encoding="utf-8")
            print(text)
            return 0
        if args.command == "export-annotations":
            run = json.loads(args.run.read_text(encoding="utf-8"))
            count = annotation.export_annotations(run, args.out, not args.all)
            print(f"{count} filas → {args.out}")
            return 0
        print(json.dumps(annotation.score_annotations(args.csv), indent=2))
        return 0
    except (DatasetError, ValueError) as error:
        print(f"ERROR {error}", file=sys.stderr)
        return 1
