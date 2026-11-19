"""FTS5 query builder (copy of infra/d1/tests/fts_query.py) and drift check."""

import ast

import pytest
from d1_support import REPO, require

from unitropico_rag.infra import fts_query
from unitropico_rag.infra.fts_query import build_fts5_query, fold, quote_term


def _code(source: str) -> str:
    """Dump the module AST without its docstring, for drift comparison."""
    tree = ast.parse(source)
    tree.body = tree.body[1:] if isinstance(tree.body[0], ast.Expr) else tree.body
    return ast.dump(tree)


def test_copy_matches_reference():
    """The copied code equals the infra reference (docstring aside)."""
    reference = require(REPO / "infra/d1/tests/fts_query.py").read_text("utf-8")
    with open(fts_query.__file__, encoding="utf-8") as copy:
        assert _code(copy.read()) == _code(reference)


def test_fold_and_quote():
    """Folding drops case and accents; quoting doubles inner quotes."""
    assert fold("INSCRIPCIÓN") == "inscripcion"
    assert fold("inscripción") == "inscripcion"
    assert quote_term('a"b') == '"a""b"'


@pytest.mark.parametrize(
    "text,expected",
    [
        (
            "¿Cuáles son los requisitos de inscripción?",
            '"son" OR "requisitos" OR "inscripción"',
        ),
        (
            'beca" OR NEAR(x*, 2) ^col:q -z +w',
            '"beca" OR "OR" OR "NEAR" OR "x" OR "2" OR "col" OR "q" OR "z" OR "w"',
        ),
        ("renta_joven", '"renta" OR "joven"'),
        ("inscripción", '"inscripción"'),
        ("Inscripción inscripcion INSCRIPCION beca", '"Inscripción" OR "beca"'),
        ("de la beca", '"beca"'),
        ("de la", '"de" OR "la"'),
        ("convocatoria 2026-1", '"convocatoria" OR "2026" OR "1"'),
    ],
)
def test_build_query(text, expected):
    """Reference examples from infra/d1/queries.md."""
    assert build_fts5_query(text) == expected


@pytest.mark.parametrize("text", ["", "   ", "¿?¡!", '"""', "--", "_"])
def test_nothing_searchable(text):
    """No surviving term returns None (lexical search is skipped)."""
    assert build_fts5_query(text) is None


def test_limits_and_types():
    """max_terms caps terms; invalid inputs raise."""
    terms = " ".join(f"t{i}" for i in range(40))
    assert build_fts5_query(terms, max_terms=3) == '"t0" OR "t1" OR "t2"'
    with pytest.raises(ValueError):
        build_fts5_query("x", max_terms=0)
    with pytest.raises(TypeError):
        build_fts5_query(None)  # type: ignore[arg-type]


def test_output_has_only_quoted_terms():
    """SQL/FTS metacharacters never survive."""
    query = build_fts5_query("a'b; DROP TABLE chunks; --\x00 é")
    assert query is not None
    assert all(p.startswith('"') and p.endswith('"') for p in query.split(" OR "))
    assert ";" not in query and "\x00" not in query
