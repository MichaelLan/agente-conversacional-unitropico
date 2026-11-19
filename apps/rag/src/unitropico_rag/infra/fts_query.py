r"""FTS5 query builder for chunks_fts, copied from infra/d1/tests/fts_query.py.

Keep the function body identical to the reference (pure, stdlib only). The
returned string is ALWAYS sent as a bound parameter (``WHERE chunks_fts MATCH
?``), never interpolated into SQL.

Algorithm:
1. NFKC-normalize so decomposed accents (NFD) compose before tokenizing.
2. Tokenize on Unicode letters/digits (``[^\W_]+``): every FTS5 operator,
   quote, parenthesis, asterisk, caret, colon, minus or underscore is dropped.
3. Deduplicate case- and accent-insensitively (same folding as the
   ``unicode61 remove_diacritics 2`` tokenizer), keeping first occurrence order.
4. Drop a small set of Spanish function words, unless that leaves nothing.
5. Keep at most ``max_terms`` terms to bound query cost.
6. Wrap each term in double quotes, doubling any inner quote (defensive: step 2
   already removes quotes), so AND/OR/NOT/NEAR become plain strings.
7. Join with `` OR `` (recall-oriented, bm25 ranks documents matching more and
   rarer terms higher). Return None when no term survives: skip lexical search.
"""

import re
import unicodedata

_TOKEN = re.compile(r"[^\W_]+")

SPANISH_STOPWORDS = frozenset(
    {
        "a", "al", "como", "con", "cual", "cuales", "de", "del", "el", "en",
        "es", "la", "las", "lo", "los", "me", "mi", "o", "para", "por", "que",
        "se", "si", "su", "sus", "un", "una", "unos", "unas", "y", "yo",
    }
)  # fmt: skip


def fold(term: str) -> str:
    """Casefold and strip combining marks, approximating unicode61 folding."""
    decomposed = unicodedata.normalize("NFD", term.casefold())
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


def quote_term(term: str) -> str:
    """Return an FTS5 string literal: double quotes, inner quotes doubled."""
    return '"' + term.replace('"', '""') + '"'


def build_fts5_query(
    text: str,
    *,
    max_terms: int = 16,
    stopwords: frozenset[str] = SPANISH_STOPWORDS,
) -> str | None:
    """Build a safe OR query of quoted terms, or None when nothing is searchable."""
    if not isinstance(text, str):
        raise TypeError("text must be str")
    if max_terms < 1:
        raise ValueError("max_terms must be >= 1")
    seen: set[str] = set()
    terms: list[str] = []
    for token in _TOKEN.findall(unicodedata.normalize("NFKC", text)):
        key = fold(token)
        if key not in seen:
            seen.add(key)
            terms.append(token)
    content = [term for term in terms if fold(term) not in stopwords]
    selected = (content or terms)[:max_terms]
    if not selected:
        return None
    return " OR ".join(quote_term(term) for term in selected)
