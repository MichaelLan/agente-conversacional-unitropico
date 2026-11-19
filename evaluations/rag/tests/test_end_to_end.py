"""Harness end to end against a fake local RAG (no external network)."""

import json
from pathlib import Path

import pytest

from unitropico_eval import annotation, report, runners
from unitropico_eval.cli import main
from unitropico_eval.client import HttpResult
from unitropico_eval.dataset import Gold, Item
from unitropico_eval.docmap import DocumentMap

ROOT = Path(__file__).resolve().parents[1]
SHA_R002 = "a10a246e25e22e1df0847570154f27eb8782e745e140b5335d10d44beddd53c3"

DOCMAP = DocumentMap.from_dict(
    {
        "documents": {
            "res": {
                "page_count": 16,
                "sha256": [SHA_R002],
                "titles": ["Resolución 002"],
            },
            "pol": {"page_count": 24, "titles": ["Política"]},
        }
    }
)


GOLD_RES = (Gold("res", (5,)),)


def _item(id_, behavior="answer", gold=GOLD_RES, category="in_scope", split="dev"):
    return Item(
        id=id_,
        split=split,
        category=category,
        question=f"pregunta {id_}",
        expected_behavior=behavior,
        gold=gold if behavior == "answer" else (),
        source="draft_from_corpus",
        validated=False,
        notes="",
    )


def _evidence(sha, start, end, title="Resolución 002"):
    return {
        "document_id": "doc",
        "version_id": "ver",
        "chunk_id": f"c{start}",
        "source_sha256": sha,
        "page_start": start,
        "page_end": end,
        "section": None,
        "document_title": title,
        "version_label": None,
        "text": "texto",
    }


def test_score_retrieval_hit_at_rank_two():
    body = {
        "status": "evidence_found",
        "evidence": [_evidence("x", 1, 1, "??"), _evidence(SHA_R002, 4, 6)],
    }
    record = runners.score_retrieval(_item("a"), HttpResult(200, body, 12.0), DOCMAP, 5)
    assert record["metrics"] == {
        "recall_at_k": 1.0,
        "reciprocal_rank": 0.5,
        "ndcg_at_k": pytest.approx(1 / 1.584962500721156),
        "gold_coverage_at_k": 1.0,
    }
    assert record["unmapped_results"] == 1
    assert [r["relevant"] for r in record["results"]] == [False, True]


def test_score_retrieval_unavailable_is_error_not_miss():
    record = runners.score_retrieval(
        _item("a"),
        HttpResult(503, {"status": "unavailable", "evidence": []}, 3.0),
        DOCMAP,
        5,
    )
    assert record["metrics"] is None
    assert record["error"] == "HTTP 503"


def _answered(citations):
    return {
        "status": "answered",
        "answer": "Sí.",
        "reason": None,
        "citations": citations,
        "referral": None,
    }


def test_score_answer_citations_and_outcomes():
    cites = [_evidence(None, 5, 5), _evidence(None, 9, 9, "Política")]
    good = runners.score_answer(
        _item("a"), HttpResult(200, _answered(cites), 5.0), DOCMAP
    )
    assert good["outcome"] == "correct_answer"
    assert good["citation_accuracy"] == 0.5
    assert good["citation_coverage"] == 1.0
    assert good["has_gold_citation"] is True

    unsupported = runners.score_answer(
        _item("b"),
        HttpResult(200, _answered([_evidence(None, 9, 9, "Política")]), 5.0),
        DOCMAP,
    )
    assert unsupported["has_gold_citation"] is False

    abstained = {
        "status": "abstained",
        "answer": None,
        "reason": "no_evidence",
        "citations": [],
        "referral": "r",
    }
    oos = runners.score_answer(
        _item("c", "abstain", category="out_of_scope"),
        HttpResult(200, abstained, 2.0),
        DOCMAP,
    )
    assert oos["outcome"] == "correct_abstention"
    assert oos["citation_accuracy"] is None

    down = runners.score_answer(
        _item("d"), HttpResult(None, None, 1.0, "URLError"), DOCMAP
    )
    assert down["outcome"] == "error"


def test_summarize_answer_rates_and_cost_unavailable():
    abstained = {
        "status": "abstained",
        "answer": None,
        "reason": "no_evidence",
        "citations": [],
        "referral": "r",
    }
    records = [
        runners.score_answer(
            _item("a"),
            HttpResult(200, _answered([_evidence(None, 5, 5)]), 10.0),
            DOCMAP,
        ),
        runners.score_answer(_item("b"), HttpResult(200, abstained, 20.0), DOCMAP),
        runners.score_answer(
            _item("c", "abstain", category="out_of_scope"),
            HttpResult(200, abstained, 30.0),
            DOCMAP,
        ),
        runners.score_answer(
            _item("d", "abstain", category="out_of_scope"),
            HttpResult(200, _answered([_evidence(None, 5, 5)]), 40.0),
            DOCMAP,
        ),
    ]
    summary = report.summarize_answer(records)
    block = summary["groups"]["all"]
    assert block["answer_rate_on_answerable"] == 0.5
    assert block["abstention_rate_on_out_of_scope"] == 0.5
    assert block["behavior_accuracy"] == 0.5
    assert block["citation_precision_micro"] == 1.0
    assert block["answered_without_gold_citation"] == 0.0
    assert block["reasons"] == {"no_evidence": 2}
    assert summary["latency"]["p50_ms"] == 25.0
    assert summary["cost"] == report.COST_UNAVAILABLE


def test_usage_is_aggregated_when_present():
    body = _answered([_evidence(None, 5, 5)]) | {
        "usage": {"prompt_tokens": 100, "completion_tokens": 20}
    }
    records = [runners.score_answer(_item("a"), HttpResult(200, body, 1.0), DOCMAP)] * 2
    cost = report.summarize_answer(records)["cost"]
    assert cost == {
        "responses_with_usage": 2,
        "totals": {"prompt_tokens": 200, "completion_tokens": 40},
    }


def _fake_rag(path, body):
    if path == "/retrieval/query":
        assert set(body) == {"question", "limit"}
        return 200, {
            "status": "evidence_found",
            "evidence": [_evidence(SHA_R002, 5, 5)],
        }
    if path == "/answer":
        return 200, _answered([_evidence(None, 5, 5)])
    return 404, {"detail": "Not Found"}


def test_cli_retrieval_answer_compare_and_annotations(fake_server, tmp_path, capsys):
    server = fake_server(_fake_rag)
    common = ["--base-url", server.url, "--out-dir", str(tmp_path), "--split", "all"]
    assert (
        main(["retrieval", *common, "--label", "lex", "--mode", "lexical", "-k", "3"])
        == 0
    )
    assert (
        main(["retrieval", *common, "--label", "hyb", "--mode", "hybrid", "-k", "3"])
        == 0
    )
    assert main(["answer", *common, "--label", "ans", "--category", "follow_up"]) == 0

    jsons = sorted(tmp_path.glob("*-retrieval-*.json"))
    assert len(jsons) == 2
    run = json.loads(jsons[0].read_text(encoding="utf-8"))
    assert run["meta"]["k"] == 3
    assert run["meta"]["validated_items"] == 0
    assert all(
        path == "/retrieval/query" and body["limit"] == 3
        for path, body in server.requests[:5]
    )
    markdown = jsons[0].with_suffix(".md").read_text(encoding="utf-8")
    assert "Dataset NO validado" in markdown
    assert "RETRIEVAL_MODE declarado" in markdown

    out = tmp_path / "cmp.md"
    assert main(["compare", *map(str, jsons), "--out", str(out)]) == 0
    table = out.read_text(encoding="utf-8")
    assert "| lex | lexical |" in table or "| hyb | hybrid |" in table

    answer_json = next(tmp_path.glob("*-answer-ans.json"))
    answer_run = json.loads(answer_json.read_text(encoding="utf-8"))
    follow_requests = [b for p, b in server.requests if p == "/answer"]
    assert follow_requests and all("history" in b for b in follow_requests)
    assert "no medible" in answer_json.with_suffix(".md").read_text(encoding="utf-8")

    csv_path = tmp_path / "ann.csv"
    assert main(["export-annotations", str(answer_json), "--out", str(csv_path)]) == 0
    assert annotation.export_annotations(answer_run, csv_path) == len(
        answer_run["items"]
    )
    capsys.readouterr()


def test_score_annotations(tmp_path):
    path = tmp_path / "a.csv"
    header = annotation.CONTEXT_COLUMNS + annotation.JUDGMENT_COLUMNS
    rows = [
        dict.fromkeys(header, "")
        | {
            "id": "1",
            "faithfulness_0_2": "2",
            "claims_total": "3",
            "claims_supported": "3",
            "claims_unsupported": "0",
        },
        dict.fromkeys(header, "")
        | {
            "id": "2",
            "faithfulness_0_2": "0",
            "claims_total": "1",
            "claims_supported": "0",
            "claims_unsupported": "1",
        },
        dict.fromkeys(header, "") | {"id": "3"},
    ]
    import csv

    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=header)
        writer.writeheader()
        writer.writerows(rows)
    scores = annotation.score_annotations(path)
    assert scores["annotated_rows"] == 2
    assert scores["mean_faithfulness_0_2"] == 1.0
    assert scores["unsupported_claim_rate"] == 0.25


def test_compare_rejects_mixed_commands():
    runs = [
        {"meta": {"command": "retrieval"}, "summary": {"groups": {"all": {}}}},
        {"meta": {"command": "answer"}, "summary": {"groups": {"all": {}}}},
    ]
    with pytest.raises(ValueError):
        report.render_comparison(runs)


def test_validate_command_on_repository_dataset(capsys):
    assert main(["validate"]) == 0
    assert '"errors": 0' in capsys.readouterr().out
