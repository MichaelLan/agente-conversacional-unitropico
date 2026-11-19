"""Map RAG document identifiers to the dataset's stable document ids."""

import json
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path


def normalize_title(title: str) -> str:
    """Lowercase, strip accents and punctuation, collapse whitespace."""
    decomposed = unicodedata.normalize("NFKD", title)
    ascii_only = "".join(c for c in decomposed if not unicodedata.combining(c))
    cleaned = re.sub(r"[^0-9a-z]+", " ", ascii_only.lower())
    return " ".join(cleaned.split())


@dataclass(frozen=True)
class DocumentMap:
    """Lookup tables built from ``datasets/document_map.json``.

    Resolution order: ``source_sha256`` (only ``/retrieval/query`` exposes it),
    ``document_id``, ``version_id`` and finally the normalized title. The first
    hit wins; ``None`` means the RAG returned a document the map does not know.
    """

    page_counts: dict[str, int]
    by_sha: dict[str, str]
    by_document_id: dict[str, str]
    by_version_id: dict[str, str]
    by_title: dict[str, str]

    @classmethod
    def from_dict(cls, data: dict) -> DocumentMap:
        """Build the lookup tables; duplicated keys across documents are errors."""
        tables: dict[str, dict[str, str]] = {
            "sha256": {},
            "document_ids": {},
            "version_ids": {},
            "titles": {},
        }
        page_counts: dict[str, int] = {}
        for stable_id, entry in data["documents"].items():
            page_counts[stable_id] = int(entry["page_count"])
            for field, table in tables.items():
                for raw in entry.get(field, []):
                    key = normalize_title(raw) if field == "titles" else raw.lower()
                    if key in table and table[key] != stable_id:
                        raise ValueError(f"{field} {raw!r} asignado a dos documentos")
                    table[key] = stable_id
        return cls(
            page_counts=page_counts,
            by_sha=tables["sha256"],
            by_document_id=tables["document_ids"],
            by_version_id=tables["version_ids"],
            by_title=tables["titles"],
        )

    @classmethod
    def load(cls, path: Path) -> DocumentMap:
        """Read the JSON map from disk."""
        return cls.from_dict(json.loads(path.read_text(encoding="utf-8")))

    def resolve(self, record: dict) -> str | None:
        """Return the stable id for an evidence/citation dict, or ``None``."""
        sha = record.get("source_sha256")
        if isinstance(sha, str) and sha.lower() in self.by_sha:
            return self.by_sha[sha.lower()]
        for key, table in (
            ("document_id", self.by_document_id),
            ("version_id", self.by_version_id),
        ):
            value = record.get(key)
            if isinstance(value, str) and value.lower() in table:
                return table[value.lower()]
        title = record.get("document_title")
        if isinstance(title, str):
            return self.by_title.get(normalize_title(title))
        return None
