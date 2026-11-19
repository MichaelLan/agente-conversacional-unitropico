"""Admin text review: chunks listing and literal OCR corrections."""

import asyncio
import re
from pathlib import Path

import pytest
from admin_fakes import FakeD1
from test_admin import Admin, error_code

from unitropico_api.domain.admin import (
    MAX_ACTIVE_CORRECTIONS,
    Conflict,
    InvalidInput,
    NewCorrection,
)
from unitropico_api.infra import d1 as d1_module
from unitropico_api.infra.d1 import D1DocumentRepository

QUERIES = Path(__file__).resolve().parents[3] / "infra" / "d1" / "queries.sql"
CORRECTION = {
    "page_number": 2,
    "find_text": "igual a 3.0",
    "replace_text": "igual o superior a 3.0",
    "reason": "Cotejado con el PDF original, página 2.",
}
CORRECTION_KEYS = {
    "id",
    "version_id",
    "page_number",
    "find_text",
    "replace_text",
    "reason",
    "created_at",
    "deleted_at",
    "active",
}


@pytest.fixture
def admin() -> Admin:
    """Fresh app and catalog per test."""
    return Admin()


def corrections_path(version_id: str, suffix: str = "") -> str:
    """Path of the corrections collection or of one correction."""
    return f"/admin/versions/{version_id}/corrections{suffix}"


def create(admin: Admin, version_id: str, **changes: object):
    """POST a correction with default values."""
    body = {**CORRECTION, **changes}
    return admin.request("POST", corrections_path(version_id), body=body)


def correct_events(db: FakeD1, version_id: str) -> list[tuple]:
    """Events of the correct step."""
    return [e for e in db.events(version_id) if e[0] == "correct"]


def publish(admin: Admin) -> str:
    """Return a published version id."""
    version_id = admin.ready()
    status, _, body = admin.action(version_id, "publish")
    assert status == 200, body
    return version_id


# --- chunks -----------------------------------------------------------------


def add_chunks(db: FakeD1, version_id: str, count: int) -> None:
    """Append chunks after the one inserted by simulate_workflow."""
    for ordinal in range(1, count + 1):
        db.sql(
            "INSERT INTO chunks (id, version_id, ordinal, text, text_sha256, section, "
            "page_start, page_end, token_estimate) VALUES (?, ?, ?, ?, ?, ?, 3, 3, 1)",
            f"ch{ordinal}",
            version_id,
            ordinal,
            f"Texto {ordinal}",
            "b" * 64,
            "Artículo 2",
        )


def test_chunks_of_a_processed_version(admin):
    """Chunks come from D1 with the version chunker, in ordinal order."""
    version_id = admin.ready(review_done=False)
    status, _, body = admin.request("GET", f"/admin/versions/{version_id}/chunks")
    assert status == 200
    assert body == {
        "version_id": version_id,
        "status": "ready",
        "total": 1,
        "offset": 0,
        "limit": 50,
        "chunks": [
            {
                "ordinal": 0,
                "id": f"ch_{version_id}",
                "section": None,
                "page_start": 1,
                "page_end": 2,
                "text": "Texto",
                "chunker_version": "chunker-1",
            }
        ],
    }


def test_chunks_pagination_and_any_status(admin):
    """offset/limit page through chunks; published and empty versions work."""
    version_id = admin.ready(review_done=False)
    add_chunks(admin.db, version_id, 5)
    status, _, body = admin.request(
        "GET",
        f"/admin/versions/{version_id}/chunks",
        query={"offset": "2", "limit": "3"},
    )
    assert status == 200
    assert body["total"] == 6 and (body["offset"], body["limit"]) == (2, 3)
    assert [c["ordinal"] for c in body["chunks"]] == [2, 3, 4]
    assert body["chunks"][0]["section"] == "Artículo 2"

    status, _, body = admin.request(
        "GET",
        f"/admin/versions/{version_id}/chunks",
        query={"offset": "10"},
    )
    assert status == 200 and body["chunks"] == [] and body["total"] == 6

    uploaded = admin.uploaded(b"%PDF-1.7 otro")
    status, _, body = admin.request("GET", f"/admin/versions/{uploaded}/chunks")
    assert status == 200
    assert (body["status"], body["total"], body["chunks"]) == ("uploaded", 0, [])


def test_chunks_of_published_version_are_readable(admin):
    """Review stays available after publishing."""
    version_id = publish(admin)
    status, _, body = admin.request("GET", f"/admin/versions/{version_id}/chunks")
    assert status == 200 and body["status"] == "published" and body["total"] == 1


@pytest.mark.parametrize(
    "query",
    [
        {"limit": "101"},
        {"limit": "0"},
        {"offset": "-1"},
        {"limit": "diez"},
        {"page": "1"},
    ],
)
def test_chunks_query_validation(admin, query):
    """Limit is 1..100, offset >= 0, no extra parameters."""
    version_id = admin.ready()
    status, _, body = admin.request(
        "GET", f"/admin/versions/{version_id}/chunks", query=query
    )
    assert status == 422
    assert error_code(body) == "invalid_request"


# --- create -----------------------------------------------------------------


def test_create_correction_records_and_does_not_reprocess(admin):
    """201 with the stored correction; an event is logged; no Workflow call."""
    version_id = admin.ready()
    launches = len(admin.launcher.calls)
    status, _, body = create(
        admin, version_id, find_text="  igual a 3.0 ", reason=" Cotejo "
    )
    assert status == 201, body
    assert set(body) == {
        "correction",
        "version_status",
        "requires_review",
        "reprocess_required",
        "message",
    }
    assert body["reprocess_required"] is True
    # A pending correction invalidates the review: ready -> review_required.
    assert (body["version_status"], body["requires_review"]) == (
        "review_required",
        True,
    )
    assert "reprocese" in body["message"]
    correction = body["correction"]
    assert set(correction) == CORRECTION_KEYS
    assert re.fullmatch(r"cor_[0-9a-f]{32}", correction["id"])
    assert correction["version_id"] == version_id
    assert correction["find_text"] == "igual a 3.0"  # stripped
    assert correction["replace_text"] == "igual o superior a 3.0"
    assert correction["reason"] == "Cotejo"
    assert correction["active"] is True and correction["deleted_at"] is None
    assert len(admin.launcher.calls) == launches
    row = admin.db.version(version_id)
    assert (row["status"], row["requires_review"]) == ("review_required", 1)
    status, _, body = admin.action(version_id, "publish")
    assert status == 409 and error_code(body) == "invalid_status_transition"
    assert correct_events(admin.db, version_id) == [
        (
            "correct",
            "succeeded",
            None,
            f"correction_created id={correction['id']} page=2",
        )
    ]


def test_replace_text_is_kept_verbatim_and_may_be_empty(admin):
    """An empty replacement removes OCR noise; spaces are not stripped."""
    version_id = admin.ready()
    status, _, body = create(admin, version_id, find_text="~~ruido~~", replace_text="")
    assert status == 201 and body["correction"]["replace_text"] == ""
    status, _, body = create(admin, version_id, find_text="x", replace_text=" y ")
    assert status == 201 and body["correction"]["replace_text"] == " y "


@pytest.mark.parametrize(
    "changes",
    [
        {"page_number": 0},
        {"page_number": "2"},
        {"page_number": True},
        {"page_number": 2.0},
        {"find_text": "   "},
        {"find_text": "x" * 501},
        {"replace_text": "x" * 501},
        {"reason": ""},
        {"reason": "x" * 301},
        {"find_text": "igual\x00a"},
        {"replace_text": None},
        {"find_text": "igual", "replace_text": "igual"},
        {"extra": 1},
    ],
)
def test_create_validation(admin, changes):
    """Strict JSON, lengths and no-op corrections are rejected with 422."""
    version_id = admin.ready()
    status, _, body = create(admin, version_id, **changes)
    assert status == 422, body
    assert error_code(body) == "invalid_request"
    assert correct_events(admin.db, version_id) == []


def test_create_accepts_maximum_lengths(admin):
    """500/500/300 characters are accepted (multibyte counted as characters)."""
    version_id = admin.ready()
    status, body = create(
        admin, version_id, find_text="ñ" * 500, replace_text="é" * 500, reason="á" * 300
    )[::2]
    assert status == 201, body


def test_create_missing_fields_and_invalid_json(admin):
    """A body without required fields or not JSON is a 422 envelope."""
    version_id = admin.ready()
    status, _, body = admin.request(
        "POST", corrections_path(version_id), body={"page_number": 2}
    )
    assert status == 422 and error_code(body) == "invalid_request"
    assert "find_text" in body["error"]["message"]
    status, _, body = admin.request(
        "POST", corrections_path(version_id), body=b"{no json"
    )
    assert status == 422 and error_code(body) == "invalid_request"


def test_page_beyond_known_page_count_is_422(admin):
    """page_number must not exceed page_count when it is known."""
    version_id = admin.ready()  # simulate_workflow sets page_count = 3
    status, _, body = create(admin, version_id, page_number=4)
    assert status == 422 and error_code(body) == "page_out_of_range"
    status, _, _ = create(admin, version_id, page_number=3)
    assert status == 201


def test_unknown_page_count_accepts_any_page(admin):
    """Before OCR the page count is unknown; the ingestion checks the page."""
    version_id = admin.uploaded()
    status, _, body = create(admin, version_id, page_number=50)
    assert status == 201, body


def test_published_version_refuses_corrections(admin):
    """409 version_published for create and delete; nothing is written."""
    version_id = admin.ready()
    _, _, created = create(admin, version_id)
    assert admin.patch(version_id, {"requires_review": False})[0] == 200
    status, _, _ = admin.action(version_id, "publish")
    assert status == 200
    status, _, body = create(admin, version_id, find_text="otro")
    assert status == 409 and error_code(body) == "version_published"
    path = corrections_path(version_id, f"/{created['correction']['id']}")
    status, _, body = admin.request("DELETE", path)
    assert status == 409 and error_code(body) == "version_published"
    assert len(correct_events(admin.db, version_id)) == 1
    _, _, listed = admin.request("GET", corrections_path(version_id))
    assert [c["active"] for c in listed["corrections"]] == [True]


def test_correction_on_unpublished_version_requires_review_again(admin):
    """Unpublished goes to review_required on create and on delete."""
    version_id = publish(admin)
    assert admin.action(version_id, "unpublish")[0] == 200
    status, _, body = create(admin, version_id)
    assert status == 201 and body["version_status"] == "review_required"
    assert admin.patch(version_id, {"requires_review": False})[0] == 200
    assert admin.db.version(version_id)["status"] == "ready"
    path = corrections_path(version_id, f"/{body['correction']['id']}")
    status, _, deleted = admin.request("DELETE", path)
    assert status == 200 and deleted["version_status"] == "review_required"
    # Repeating the delete changes nothing else.
    assert admin.patch(version_id, {"requires_review": False})[0] == 200
    status, _, again = admin.request("DELETE", path)
    assert status == 200 and again["version_status"] == "ready"


def test_duplicate_active_correction_is_409(admin):
    """Same page and find_text twice would never apply: refused."""
    version_id = admin.ready()
    assert create(admin, version_id)[0] == 201
    status, _, body = create(admin, version_id, replace_text="otra cosa")
    assert status == 409 and error_code(body) == "duplicate_correction"
    assert create(admin, version_id, page_number=3)[0] == 201


def test_active_corrections_are_capped(admin):
    """At most MAX_ACTIVE_CORRECTIONS active corrections per version."""
    version_id = admin.ready()
    for n in range(MAX_ACTIVE_CORRECTIONS):
        admin.db.sql(
            "INSERT INTO text_corrections (id, version_id, page_number, find_text, "
            "replace_text, reason) VALUES (?, ?, 1, ?, 'x', 'r')",
            f"cor_{n}",
            version_id,
            f"texto {n}",
        )
    status, _, body = create(admin, version_id)
    assert status == 409 and error_code(body) == "too_many_corrections"


# --- list and delete -----------------------------------------------------------


def test_list_and_soft_delete(admin):
    """Deletion is logical, idempotent, logged once and listed with a flag."""
    version_id = admin.ready()
    first = create(admin, version_id)[2]["correction"]
    second = create(admin, version_id, page_number=3)[2]["correction"]
    launches = len(admin.launcher.calls)

    path = corrections_path(version_id, f"/{first['id']}")
    status, _, body = admin.request("DELETE", path)
    assert status == 200, body
    assert body["reprocess_required"] is True
    assert body["correction"]["active"] is False
    assert body["correction"]["deleted_at"] >= body["correction"]["created_at"]
    again_status, _, again = admin.request("DELETE", path)
    assert again_status == 200 and again["correction"] == body["correction"]
    assert len(admin.launcher.calls) == launches

    status, _, listed = admin.request("GET", corrections_path(version_id))
    assert status == 200 and listed["version_id"] == version_id
    assert [(c["id"], c["active"]) for c in listed["corrections"]] == [
        (first["id"], False),
        (second["id"], True),
    ]
    assert all(set(c) == CORRECTION_KEYS for c in listed["corrections"])
    details = [e[3] for e in correct_events(admin.db, version_id)]
    assert details == [
        f"correction_created id={first['id']} page=2",
        f"correction_created id={second['id']} page=3",
        f"correction_deleted id={first['id']}",
    ]
    # A deleted correction no longer blocks the same text.
    assert create(admin, version_id)[0] == 201


def test_list_empty(admin):
    """A version without corrections lists none."""
    version_id = admin.uploaded()
    status, _, body = admin.request("GET", corrections_path(version_id))
    assert status == 200
    assert body == {"version_id": version_id, "corrections": []}


def test_delete_unknown_or_foreign_correction_is_404(admin):
    """Unknown ids and corrections of another version are not found."""
    version_id = admin.ready()
    other = admin.uploaded(b"%PDF-1.7 otro")
    foreign = create(admin, other)[2]["correction"]["id"]
    for cid in ("cor_missing", foreign):
        status, _, body = admin.request(
            "DELETE", corrections_path(version_id, f"/{cid}")
        )
        assert status == 404 and error_code(body) == "correction_not_found"
    _, _, listed = admin.request("GET", corrections_path(other))
    assert listed["corrections"][0]["active"] is True


# --- adapter -------------------------------------------------------------------


def test_repository_translates_schema_guards():
    """If the status changes after the service check, triggers still refuse."""
    admin = Admin()
    version_id = publish(admin)
    repo = D1DocumentRepository(lambda: admin.db)

    def new(page: int) -> NewCorrection:
        return NewCorrection("cor_x", version_id, page, "a", "b", "r")

    with pytest.raises(Conflict) as info:
        asyncio.run(repo.create_correction(new(1)))
    assert info.value.code == "version_published"
    admin.db.sql(
        "UPDATE document_versions SET status = 'unpublished' WHERE id = ?", version_id
    )
    with pytest.raises(InvalidInput) as invalid:
        asyncio.run(repo.create_correction(new(4)))
    assert invalid.value.code == "page_out_of_range"
    assert admin.db.sql("SELECT count(*) AS n FROM text_corrections")[0]["n"] == 0


def _canonical(name: str) -> str:
    block = QUERIES.read_text().split(f"-- name: {name}\n", 1)[1]
    block = block.split("-- name:", 1)[0]
    lines = [line for line in block.splitlines() if not line.startswith("--")]
    return " ".join(" ".join(lines).split()).rstrip(";")


@pytest.mark.parametrize(
    ("constant", "name"),
    [
        ("CHUNKS_PAGE", "chunks_page_by_version"),
        ("COUNT_CHUNKS", "count_chunks_by_version"),
        ("CORRECTIONS", "corrections_by_version"),
        ("INSERT_CORRECTION", "insert_correction"),
        ("SOFT_DELETE_CORRECTION", "soft_delete_correction"),
    ],
)
def test_statements_match_canonical_queries(constant, name):
    """The adapter SQL is the canonical SQL of infra/d1/queries.sql."""
    sql = " ".join(getattr(d1_module, constant).split())
    assert sql == _canonical(name)
