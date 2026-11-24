"""Welfare synonym expansion on top of the reference FTS5 query builder."""

import pytest

from unitropico_rag.infra.fts_query import build_fts5_query, fold
from unitropico_rag.infra.fts_synonyms import (
    DEFAULT_MAX_SYNONYMS,
    WELFARE_SYNONYMS,
    expand_fts5_query,
    synonym_terms,
)


def terms(query):
    """Split an OR query into its quoted terms."""
    return query.split(" OR ")


def test_beca_expands_to_corpus_vocabulary():
    """'becas' adds apoyo/auxilio/estímulo/subsidio/beneficio after the base."""
    query = expand_fts5_query("¿Qué becas hay disponibles?")
    base = build_fts5_query("¿Qué becas hay disponibles?")
    assert query.startswith(base + " OR ")
    for word in ('"beca"', '"apoyo"', '"apoyos"', '"auxilio"', '"estímulo"'):
        assert word in terms(query)


@pytest.mark.parametrize(
    "question",
    ["¿Cómo me inscribo?", "como me INSCRIBO", "inscripcion", "postularme"],
)
def test_enrolment_variants(question):
    """Every enrolment form expands to inscripción/postulación forms."""
    found = {fold(t.strip('"')) for t in terms(expand_fts5_query(question))}
    assert {"inscripcion", "postulacion", "inscribirse"} <= found


def test_accent_insensitive_dedup():
    """Terms already in the question (any accent/case) are not repeated."""
    query = expand_fts5_query("beca ESTIMULO apoyos")
    folded = [fold(t.strip('"')) for t in terms(query)]
    assert len(folded) == len(set(folded))
    assert synonym_terms("requisitos") == ["requisito"]
    assert synonym_terms("requisito requisitos") == []


def test_no_expansion_for_other_words_or_stopwords():
    """Unknown words, specific corpus words and stopwords do not expand."""
    for text in ("transporte alimentación", "apoyo socioeconómico", "de la"):
        assert expand_fts5_query(text) == build_fts5_query(text)
    assert expand_fts5_query("¿?") is None


def test_caps_and_disable():
    """Extra terms are capped; synonyms=None returns the reference query."""
    many = "beca ayuda inscribo requisito"
    assert len(synonym_terms(many)) == DEFAULT_MAX_SYNONYMS
    assert synonym_terms(many, max_synonyms=2) == ["becas", "apoyo"]
    assert expand_fts5_query(many, synonyms=None) == build_fts5_query(many)
    with pytest.raises(ValueError):
        synonym_terms("beca", max_synonyms=-1)
    with pytest.raises(TypeError):
        synonym_terms(None)  # type: ignore[arg-type]


def test_output_stays_quoted_terms_only():
    """Injection attempts still yield only quoted literal terms."""
    query = expand_fts5_query('becas" OR NEAR(x*, 2) ^col:q -- ;')
    assert all(t.startswith('"') and t.endswith('"') for t in terms(query))
    assert "NEAR(" not in query and ";" not in query


def test_table_is_folded_and_tokenizable():
    """Keys are folded; every expansion is a single tokenizer word."""
    for key, expansions in WELFARE_SYNONYMS.items():
        assert key == fold(key)
        for term in expansions:
            assert build_fts5_query(term, stopwords=frozenset()) == f'"{term}"'
