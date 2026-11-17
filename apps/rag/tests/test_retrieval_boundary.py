import asyncio
from dataclasses import asdict
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from test_retrieval import CANDIDATE, ENTRY

from unitropico_rag.domain.answer import AnswerResult
from unitropico_rag.domain.evidence import RetrievalQuery, RetrievalResult
from unitropico_rag.services.answer import AnswerService
from unitropico_rag.services.retrieval import RetrievalService


@pytest.fixture
def boundary():
    """Build real retrieval with isolated async ports and matching forged fields."""
    forged = SimpleNamespace(**asdict(CANDIDATE))
    retriever = SimpleNamespace(retrieve=AsyncMock(return_value=[forged]))
    catalog = SimpleNamespace(get_current=AsyncMock(return_value=ENTRY))
    service = RetrievalService(retriever, catalog)
    return service, retriever, catalog, forged


def test_retrieval_rejects_structural_impostor(boundary, caplog, capsys):
    """Preserve valid evidence but discard it on a later malformed duplicate."""
    service, retriever, catalog, forged = boundary
    request = RetrievalQuery(question="pregunta")
    retriever.retrieve.return_value = [CANDIDATE]
    assert asyncio.run(service.query(request)) == RetrievalResult(
        "evidence_found", (CANDIDATE,)
    )
    catalog.get_current.reset_mock()
    retriever.retrieve.return_value = [CANDIDATE, forged]
    assert asyncio.run(service.query(request)) == RetrievalResult("unavailable")
    assert retriever.retrieve.await_args_list == [
        (("pregunta", 5), {}),
        (("pregunta", 5), {}),
    ]
    catalog.get_current.assert_awaited_once_with(CANDIDATE.chunk_id)
    assert asdict(CANDIDATE) == vars(forged)
    assert caplog.text == ""
    assert capsys.readouterr() == ("", "")


def test_answer_rejects_structural_impostor_before_generation(boundary, caplog, capsys):
    """Fail closed through real retrieval without serialization or generation."""
    retrieval, retriever, catalog, forged = boundary
    generator = SimpleNamespace(complete=AsyncMock())
    service = AnswerService(retrieval, generator, catalog)
    result = asyncio.run(service.query(RetrievalQuery(question="pregunta")))
    assert result == AnswerResult("unavailable")
    generator.complete.assert_not_called()
    generator.complete.assert_not_awaited()
    retriever.retrieve.assert_awaited_once_with("pregunta", 5)
    catalog.get_current.assert_not_awaited()
    assert retriever.retrieve.return_value == [forged]
    assert caplog.text == ""
    assert capsys.readouterr() == ("", "")
