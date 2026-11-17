import asyncio
import json
from dataclasses import asdict, replace
from hashlib import sha256

import pytest
from test_retrieval import CANDIDATE, ENTRY, Catalog, Retriever

from unitropico_rag.domain.evidence import RetrievalQuery
from unitropico_rag.domain.generation import GeneratedText, GenerationError, TokenUsage
from unitropico_rag.services.answer import AnswerService
from unitropico_rag.services.retrieval import RetrievalService

VALID = {
    "status": "answered",
    "answer": "Respuesta",
    "citations": [{"chunk_id": "c1", "quote": "Texto á\n"}],
}


class Generator:
    """Generate only offline and optionally change publication while awaiting."""

    def __init__(self, payload=VALID, action=None):
        """Keep calls and simulated asynchronous effects local to each test."""
        self.payload, self.action, self.calls = payload, action, []

    async def complete(self, system_prompt, user_content):
        """Record the envelope and simulate completion or dependency failure."""
        self.calls.append((system_prompt, user_content))
        await asyncio.sleep(0)
        if self.action:
            self.action()
        if isinstance(self.payload, Exception):
            raise self.payload
        if isinstance(self.payload, dict):
            return GeneratedText(json.dumps(self.payload), TokenUsage(total_tokens=7))
        return self.payload


def run(payload=VALID, *, entry=ENTRY, candidates=(CANDIDATE,), action=None):
    """Exercise real retrieval and answer orchestration through public ports."""
    catalog, retriever = Catalog(entry), Retriever(candidates)
    generator = Generator(payload, lambda: action(catalog) if action else None)
    service = AnswerService(RetrievalService(retriever, catalog), generator, catalog)
    result = asyncio.run(service.query(RetrievalQuery(question=" pregunta ", limit=1)))
    return result, generator, catalog, retriever


def test_answer_and_data_envelope():
    """Use exact server provenance and preserve reported usage, not cost estimates."""
    result, generator, catalog, retriever = run()
    assert result.status == "answered" and result.answer == "Respuesta"
    assert result.generation_attempted and result.usage == TokenUsage(total_tokens=7)
    assert asdict(result.citations[0]) == {
        **{key: value for key, value in asdict(CANDIDATE).items() if key != "text"},
        "quote": "Texto á\n",
    }
    system, user = generator.calls[0]
    assert "instructions" in system and "JSON" in system
    assert json.loads(user) == {
        "question": "pregunta",
        "candidates": [asdict(CANDIDATE)],
    }
    assert catalog.calls == ["c1", "c1"] and retriever.calls == [("pregunta", 1)]


@pytest.mark.parametrize(
    "payload",
    [
        {"status": "abstained", "answer": None, "citations": []},
        {},
        {**VALID, "status": "unknown"},
        {**VALID, "answer": None},
        {**VALID, "answer": " \n"},
        {**VALID, "answer": 1},
        {**VALID, "citations": []},
        {**VALID, "extra": "secret"},
        {**VALID, "citations": [{"chunk_id": "other", "quote": "Texto"}]},
        {**VALID, "citations": [{"chunk_id": "c1", "quote": "inventada"}]},
        {**VALID, "citations": [{"chunk_id": "c1", "quote": " "}]},
        {**VALID, "citations": [{"chunk_id": "c1", "quote": "Texto a\u0301\n"}]},
        {**VALID, "citations": [{"chunk_id": "c1", "quote": "Texto", "page": 2}]},
        {**VALID, "citations": VALID["citations"] * 2},
        {"status": "abstained", "answer": "no", "citations": []},
        {"status": "abstained", "answer": None, "citations": VALID["citations"]},
        {**VALID, "citations": None},
        {**VALID, "citations": [{"chunk_id": 1, "quote": None}]},
        GeneratedText("{broken"),
        GeneratedText("```json\n{}\n```"),
        GeneratedText('{"status":"answered","status":"abstained"}'),
        GeneratedText(json.dumps(VALID).replace('"quote":', '"quote":"x","quote":')),
        GeneratedText(json.dumps(VALID) + " {}"),
        GeneratedText("null"),
        GeneratedText(""),
    ],
)
def test_invalid_or_abstaining_candidate(payload):
    """Reject the entire candidate without repairs or partial citation salvage."""
    result, generator, catalog, _ = run(payload)
    assert (result.status, result.answer, result.citations) == ("abstained", None, ())
    assert result.generation_attempted and len(generator.calls) == 1
    assert catalog.calls == ["c1"]


CHANGES = [
    None,
    {"status": "draft"},
    {"status": "unpublished"},
    {"requires_review": True},
    {"formally_superseded": True},
    {"version_id": "v2"},
    {"source_sha256": "b" * 64},
    {"page": 3},
    {"text_sha256": "b" * 64},
    {"section": "other"},
    {"document_id": "other"},
]


@pytest.mark.parametrize("changes", CHANGES)
@pytest.mark.parametrize("during_generation", [False, True])
def test_publication_guards(changes, during_generation):
    """Exclude stale evidence before spending and after the generation await."""
    entry = replace(ENTRY, **changes) if changes is not None else None
    action = (
        (lambda catalog: setattr(catalog, "entry", entry))
        if during_generation
        else None
    )
    result, generator, _, _ = run(
        entry=ENTRY if during_generation else entry, action=action
    )
    assert (result.status, result.answer, result.citations) == ("abstained", None, ())
    assert len(generator.calls) == int(during_generation)
    assert result.generation_attempted is during_generation


@pytest.mark.parametrize(
    "payload,attempted",
    [
        (GenerationError("SECRET", False), False),
        (GenerationError("SECRET", True), True),
        (TimeoutError("SECRET"), True),
        (RuntimeError("SECRET"), True),
        (object(), True),
        (GeneratedText(None), True),
    ],
)
def test_generation_failure(payload, attempted, caplog, capsys):
    """Fail closed without leaking provider exceptions or claiming zero consumption."""
    result, generator, _, _ = run(payload)
    assert (result.status, result.answer, result.citations) == ("unavailable", None, ())
    assert result.generation_attempted is attempted and len(generator.calls) == 1
    assert "SECRET" not in repr(result) and not caplog.text
    assert capsys.readouterr() == ("", "")


def test_absence_and_empty():
    """Distinguish missing ports from operational retrieval with no evidence."""
    assert run(candidates=())[0].status == "abstained"
    for retrieval, generator, catalog in [
        (None, Generator(), Catalog()),
        (RetrievalService(), Generator(), Catalog()),
        (RetrievalService(), None, Catalog()),
        (RetrievalService(), Generator(), None),
    ]:
        result = asyncio.run(
            AnswerService(retrieval, generator, catalog).query(
                RetrievalQuery(question="x")
            )
        )
        assert result.status == "unavailable" and not result.generation_attempted
        assert result.answer is None and result.citations == ()
        assert generator is None or generator.calls == []


def test_catalog_failure_and_oversize():
    """Discard output on catalog failure; reject oversized input before calling."""
    result, _, _, _ = run(action=lambda catalog: setattr(catalog, "fail", True))
    assert (
        result.status == "unavailable"
        and result.citations == ()
        and result.answer is None
    )
    text = "x" * 20000
    candidate = replace(CANDIDATE, text=text)
    entry = replace(ENTRY, text_sha256=sha256(text.encode()).hexdigest())
    result, generator, _, _ = run(entry=entry, candidates=(candidate,))
    assert result.status == "unavailable" and not result.generation_attempted
    assert result.answer is None and result.citations == () and generator.calls == []


@pytest.mark.parametrize("fail", [False, True])
def test_later_citation_discards_everything(fail):
    """Never salvage an earlier valid citation after a later revocation or outage."""

    class CurrentCatalog:
        """Return matching identities until generation revokes the second chunk."""

        async def get_current(self, chunk_id):
            """Read current state on each lookup, including the final cited chunk."""
            if generator.calls and chunk_id == "c2":
                if fail:
                    raise RuntimeError("SECRET")
                return None
            return replace(ENTRY, chunk_id=chunk_id)

    payload = {
        **VALID,
        "citations": [*VALID["citations"], {"chunk_id": "c2", "quote": "Texto"}],
    }
    generator, catalog = Generator(payload), CurrentCatalog()
    retriever = Retriever([CANDIDATE, replace(CANDIDATE, chunk_id="c2")])
    result = asyncio.run(
        AnswerService(RetrievalService(retriever, catalog), generator, catalog).query(
            RetrievalQuery(question="x")
        )
    )
    assert result.status == ("unavailable" if fail else "abstained")
    assert result.answer is None and result.citations == ()
    assert result.generation_attempted and len(generator.calls) == 1
    assert retriever.calls == [("x", 5)]


def test_request_validation():
    """Do not allow callers to bypass the existing query type and limit guard."""
    with pytest.raises(TypeError):
        asyncio.run(AnswerService().query({"question": "x", "limit": 21}))
    with pytest.raises(ValueError):
        RetrievalQuery(question="x", limit=21)
