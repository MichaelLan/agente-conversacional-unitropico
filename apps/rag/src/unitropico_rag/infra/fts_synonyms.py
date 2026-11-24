"""Conservative Spanish synonym expansion for the FTS5 lexical channel.

Why: students ask for "becas" or "cómo me inscribo", while the institutional
corpus says "apoyo socioeconómico", "estímulo", "auxilio", "inscripción" or
"postulación". The lexical channel matches tokens exactly (``unicode61``, no
stemming), so those questions miss relevant chunks. The semantic channel needs
no expansion.

How (``expand_fts5_query``):

1. Build the base query with the unchanged reference builder
   (``fts_query.build_fts5_query``, kept identical to ``infra/d1``).
2. For every content token of the question (same NFKC tokenization, folding and
   stopwords as the reference) whose folded form is a key of ``synonyms``, add
   that key's expansions that are not already present (folded comparison),
   in table order, up to ``max_synonyms`` extra terms.
3. Append them as quoted terms joined with ``OR``. Expansions are fixed literals
   from the table below, never user text, and are quoted with ``quote_term``,
   so no FTS5 syntax can be injected.

Accents: ``chunks_fts`` uses ``unicode61 remove_diacritics 2`` (see
``infra/d1/migrations/0001_initial.sql``), so "estímulo" also matches
"estimulo" and vice versa; keys are stored folded (no accents, lowercase) and
looked up with ``fold``. Plurals are listed explicitly because the tokenizer
does not stem.

The table is deliberately small and directional: generic words students use
("beca", "ayuda") expand to the corpus vocabulary, while corpus words that are
already specific ("apoyo") are not expanded, so they do not flood the query.
"""

import unicodedata
from collections.abc import Mapping

from unitropico_rag.infra.fts_query import (
    _TOKEN,
    SPANISH_STOPWORDS,
    build_fts5_query,
    fold,
    quote_term,
)

_FUNDING = (
    "apoyo",
    "apoyos",
    "auxilio",
    "auxilios",
    "estímulo",
    "estímulos",
    "subsidio",
    "subsidios",
    "beneficio",
    "beneficios",
)
_ENROLMENT = (
    "inscripción",
    "inscripciones",
    "inscribirse",
    "postulación",
    "postulaciones",
    "postularse",
)
_REQUIREMENTS = ("requisito", "requisitos")

# Folded trigger -> expansions (natural spelling; matching is accent-insensitive).
WELFARE_SYNONYMS: Mapping[str, tuple[str, ...]] = {
    "beca": ("becas", *_FUNDING),
    "becas": ("beca", *_FUNDING),
    "ayuda": ("ayudas", "apoyo", "apoyos", "auxilio", "auxilios"),
    "ayudas": ("ayuda", "apoyo", "apoyos", "auxilio", "auxilios"),
    "inscribir": _ENROLMENT,
    "inscribo": _ENROLMENT,
    "inscribe": _ENROLMENT,
    "inscribirme": _ENROLMENT,
    "inscribirse": _ENROLMENT,
    "inscripcion": _ENROLMENT,
    "inscripciones": _ENROLMENT,
    "postular": _ENROLMENT,
    "postulo": _ENROLMENT,
    "postularme": _ENROLMENT,
    "postularse": _ENROLMENT,
    "postulacion": _ENROLMENT,
    "postulaciones": _ENROLMENT,
    "requisito": _REQUIREMENTS,
    "requisitos": _REQUIREMENTS,
}

# Extra terms on top of the base query's ``max_terms``.
DEFAULT_MAX_SYNONYMS = 12


def synonym_terms(
    text: str,
    *,
    max_terms: int = 16,
    max_synonyms: int = DEFAULT_MAX_SYNONYMS,
    synonyms: Mapping[str, tuple[str, ...]] = WELFARE_SYNONYMS,
    stopwords: frozenset[str] = SPANISH_STOPWORDS,
) -> list[str]:
    """Return the expansion terms for ``text`` (not including its own tokens).

    Only the first ``max_terms`` content tokens (those kept by the reference
    builder) trigger expansions; at most ``max_synonyms`` terms are returned.
    """
    if not isinstance(text, str):
        raise TypeError("text must be str")
    if type(max_synonyms) is not int or max_synonyms < 0:
        raise ValueError("max_synonyms must be a non-negative integer")
    tokens: list[str] = []
    seen: set[str] = set()
    for token in _TOKEN.findall(unicodedata.normalize("NFKC", text)):
        key = fold(token)
        if key not in seen:
            seen.add(key)
            tokens.append(key)
    content = [key for key in tokens if key not in stopwords][:max_terms]
    extra: list[str] = []
    for key in content:
        for term in synonyms.get(key, ()):
            folded = fold(term)
            if folded in seen:
                continue
            if len(extra) == max_synonyms:
                return extra
            seen.add(folded)
            extra.append(term)
    return extra


def expand_fts5_query(
    text: str,
    *,
    max_terms: int = 16,
    max_synonyms: int = DEFAULT_MAX_SYNONYMS,
    synonyms: Mapping[str, tuple[str, ...]] | None = WELFARE_SYNONYMS,
) -> str | None:
    """Return the reference OR query plus quoted synonym terms (None if empty)."""
    base = build_fts5_query(text, max_terms=max_terms)
    if base is None or not synonyms:
        return base
    extra = synonym_terms(
        text, max_terms=max_terms, max_synonyms=max_synonyms, synonyms=synonyms
    )
    return " OR ".join([base, *(quote_term(term) for term in extra)])
