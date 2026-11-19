"""Pure tests for the reference FTS5 query builder (no database)."""

import unittest

from fts_query import build_fts5_query, fold, quote_term


class FoldTests(unittest.TestCase):
    def test_fold_removes_case_and_accents(self) -> None:
        self.assertEqual(fold("INSCRIPCIÓN"), "inscripcion")
        self.assertEqual(fold("Año"), "ano")
        self.assertEqual(fold("inscripcio\u0301n"), "inscripcion")


class QuoteTests(unittest.TestCase):
    def test_inner_quotes_are_doubled(self) -> None:
        self.assertEqual(quote_term('a"b'), '"a""b"')
        self.assertEqual(quote_term("NEAR"), '"NEAR"')


class BuildQueryTests(unittest.TestCase):
    def test_basic_question(self) -> None:
        self.assertEqual(
            build_fts5_query("¿Cuáles son los requisitos de inscripción?"),
            '"son" OR "requisitos" OR "inscripción"',
        )

    def test_operators_and_punctuation_removed(self) -> None:
        self.assertEqual(
            build_fts5_query('beca" OR NEAR(x*, 2) ^col:q -z +w'),
            '"beca" OR "OR" OR "NEAR" OR "x" OR "2" OR "col" OR "q" OR "z" OR "w"',
        )

    def test_underscore_splits_like_unicode61(self) -> None:
        self.assertEqual(build_fts5_query("renta_joven"), '"renta" OR "joven"')

    def test_nfd_input_is_composed(self) -> None:
        self.assertEqual(build_fts5_query("inscripcio\u0301n"), '"inscripción"')

    def test_dedup_is_case_and_accent_insensitive(self) -> None:
        self.assertEqual(
            build_fts5_query("Inscripción inscripcion INSCRIPCION beca"),
            '"Inscripción" OR "beca"',
        )

    def test_stopwords_dropped_unless_only_stopwords(self) -> None:
        self.assertEqual(build_fts5_query("de la beca"), '"beca"')
        self.assertEqual(build_fts5_query("de la"), '"de" OR "la"')

    def test_numbers_kept(self) -> None:
        self.assertEqual(
            build_fts5_query("convocatoria 2026-1"), '"convocatoria" OR "2026" OR "1"'
        )

    def test_max_terms(self) -> None:
        query = build_fts5_query(" ".join(f"t{i}" for i in range(40)), max_terms=3)
        self.assertEqual(query, '"t0" OR "t1" OR "t2"')
        with self.assertRaises(ValueError):
            build_fts5_query("x", max_terms=0)

    def test_nothing_searchable_returns_none(self) -> None:
        for text in ("", "   ", "¿?¡!", '"""', "--", "_"):
            with self.subTest(text=text):
                self.assertIsNone(build_fts5_query(text))

    def test_rejects_non_string(self) -> None:
        with self.assertRaises(TypeError):
            build_fts5_query(None)  # type: ignore[arg-type]

    def test_output_has_only_quoted_terms(self) -> None:
        query = build_fts5_query("a'b; DROP TABLE chunks; --\x00 é")
        assert query is not None
        for part in query.split(" OR "):
            self.assertTrue(part.startswith('"') and part.endswith('"'), part)
        self.assertNotIn(";", query)
        self.assertNotIn("\x00", query)


if __name__ == "__main__":
    unittest.main()
