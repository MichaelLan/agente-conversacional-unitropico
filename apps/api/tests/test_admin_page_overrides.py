"""Admin page text and full-page overrides (contract 4c)."""

import asyncio
import json

import pytest
from admin_fakes import AUTH, TOKEN, FakeD1
from test_admin import Admin, error_code
from test_admin_corrections import _canonical
from test_worker import Incoming, entry, worker  # noqa: F401  (pytest fixture)

from unitropico_api.api.rate_limit import (
    MAX_ADMIN_BODY_BYTES,
    MAX_PAGE_OVERRIDE_BODY_BYTES,
    admin_body_limit,
    early_admin_rejection,
)
from unitropico_api.domain.admin import (
    MAX_OVERRIDE_TEXT,
    Conflict,
    NewOverride,
    StorageUnavailable,
)
from unitropico_api.infra import d1 as d1_module
from unitropico_api.infra.d1 import D1DocumentRepository
from unitropico_api.infra.r2 import R2BlobStore

OVERRIDE = {
    "page_number": 2,
    "text": "ARTÍCULO 2. Texto completo de la página, transcrito del PDF.",
    "reason": "El OCR inventó una tabla.",
}
OVERRIDE_KEYS = {
    "id",
    "version_id",
    "page_number",
    "text",
    "reason",
    "created_at",
    "deleted_at",
    "active",
}
RESULT_KEYS = {
    "override",
    "version_status",
    "requires_review",
    "corrections_dirty",
    "reprocess_required",
    "message",
}


@pytest.fixture
def admin() -> Admin:
    """Fresh app and catalog per test."""
    return Admin()


def path(version_id: str, suffix: str = "") -> str:
    """Path of the overrides collection or of one override."""
    return f"/admin/versions/{version_id}/page-overrides{suffix}"


def create(admin: Admin, version_id: str, **changes: object):
    """POST an override with default values."""
    return admin.request("POST", path(version_id), body={**OVERRIDE, **changes})


def override_events(db: FakeD1, version_id: str) -> list[str]:
    """Details of the override events (step correct)."""
    return [
        e[3]
        for e in db.events(version_id)
        if e[0] == "correct" and e[3].startswith("override_")
    ]


def publish(admin: Admin) -> str:
    """Return a published version id."""
    version_id = admin.ready()
    status, _, body = admin.action(version_id, "publish")
    assert status == 200, body
    return version_id


# --- create / list / delete ---------------------------------------------------


def test_create_list_and_delete(admin):
    """201 with the contract shape, listed, then logically deleted (200)."""
    version_id = admin.ready()
    status, _, body = create(admin, version_id)
    assert status == 201, body
    assert set(body) == RESULT_KEYS
    override = body["override"]
    assert set(override) == OVERRIDE_KEYS
    assert override["id"].startswith("pov_") and len(override["id"]) == 36
    assert override["active"] is True and override["deleted_at"] is None
    assert (override["page_number"], override["text"], override["reason"]) == (
        2,
        OVERRIDE["text"],
        OVERRIDE["reason"],
    )
    assert body["version_status"] == "review_required"
    assert body["requires_review"] is True and body["corrections_dirty"] is True
    assert body["reprocess_required"] is True and "sustitución" in body["message"]

    status, _, listed = admin.request("GET", path(version_id))
    assert status == 200
    assert listed == {"version_id": version_id, "overrides": [override]}

    status, _, deleted = admin.request("DELETE", path(version_id, f"/{override['id']}"))
    assert status == 200 and set(deleted) == RESULT_KEYS
    assert deleted["override"]["active"] is False
    assert deleted["override"]["deleted_at"] is not None
    assert deleted["corrections_dirty"] is True
    # Idempotent: same answer, no second event nor revision bump.
    rev = admin.db.version(version_id)["corrections_rev"]
    status, _, again = admin.request("DELETE", path(version_id, f"/{override['id']}"))
    assert status == 200 and again["override"] == deleted["override"]
    assert admin.db.version(version_id)["corrections_rev"] == rev == 2
    assert override_events(admin.db, version_id) == [
        f"override_created id={override['id']} page=2",
        f"override_deleted id={override['id']}",
    ]


def test_text_and_reason_are_trimmed(admin):
    """Both ends are trimmed before validating and storing."""
    version_id = admin.ready()
    status, _, body = create(
        admin, version_id, text="\n  Texto\n\nde la página \t\n", reason="  Motivo "
    )
    assert status == 201
    assert body["override"]["text"] == "Texto\n\nde la página"
    assert body["override"]["reason"] == "Motivo"


def test_one_active_override_per_page(admin):
    """409 override_exists until the active one is deleted."""
    version_id = admin.ready()
    status, _, first = create(admin, version_id)
    assert status == 201
    status, _, body = create(admin, version_id, text="Otro texto")
    assert status == 409 and error_code(body) == "override_exists"
    assert create(admin, version_id, page_number=3)[0] == 201  # other page
    admin.request("DELETE", path(version_id, f"/{first['override']['id']}"))
    assert create(admin, version_id, text="Otro texto")[0] == 201


def test_unique_index_closes_the_race(admin):
    """If the fast check misses a concurrent insert, the index answers 409."""
    version_id = admin.ready()
    repo = D1DocumentRepository(lambda: admin.db)

    def new(override_id: str) -> NewOverride:
        return NewOverride(override_id, version_id, 2, "texto", "motivo")

    asyncio.run(repo.create_override(new("pov_a")))
    with pytest.raises(Conflict) as info:
        asyncio.run(repo.create_override(new("pov_b")))
    assert info.value.code == "override_exists"
    # The refused batch wrote nothing: one row, one event, one revision bump.
    assert len(admin.db.sql("SELECT id FROM page_overrides")) == 1
    assert override_events(admin.db, version_id) == ["override_created id=pov_a page=2"]
    assert admin.db.version(version_id)["corrections_rev"] == 1


def test_published_version_refuses_overrides(admin):
    """409 version_published for create and delete; nothing written."""
    version_id = admin.ready()
    status, _, created = create(admin, version_id)
    admin.db.apply_corrections(version_id)
    assert admin.patch(version_id, {"requires_review": False})[0] == 200
    assert admin.action(version_id, "publish")[0] == 200
    status, _, body = create(admin, version_id, page_number=3)
    assert status == 409 and error_code(body) == "version_published"
    oid = created["override"]["id"]
    status, _, body = admin.request("DELETE", path(version_id, f"/{oid}"))
    assert status == 409 and error_code(body) == "version_published"
    assert admin.db.sql("SELECT deleted_at FROM page_overrides")[0] == {
        "deleted_at": None
    }
    # The repository re-checks atomically (schema trigger).
    repo = D1DocumentRepository(lambda: admin.db)
    with pytest.raises(Conflict) as info:
        asyncio.run(repo.create_override(NewOverride("pov_x", version_id, 1, "t", "r")))
    assert info.value.code == "version_published"


def test_not_found(admin):
    """Unknown version or override: 404."""
    status, _, body = create(admin, "nope")
    assert status == 404 and error_code(body) == "version_not_found"
    for method, url in (
        ("GET", path("nope")),
        ("GET", "/admin/versions/nope/pages"),
    ):
        status, _, body = admin.request(method, url, query={"page": "1"})
        assert status == 404 and error_code(body) == "version_not_found"
    version_id = admin.ready()
    status, _, body = admin.request("DELETE", path(version_id, "/pov_missing"))
    assert status == 404 and error_code(body) == "override_not_found"
    other = admin.uploaded(b"%PDF-1.7 otro")
    status, _, created = create(admin, version_id)
    status, _, body = admin.request(
        "DELETE", path(other, f"/{created['override']['id']}")
    )
    assert status == 404 and error_code(body) == "override_not_found"


def test_page_out_of_range(admin):
    """Known page_count (3 after the simulated Workflow): 422 page_out_of_range."""
    version_id = admin.ready()
    status, _, body = create(admin, version_id, page_number=4)
    assert status == 422 and error_code(body) == "page_out_of_range"
    assert create(admin, version_id, page_number=3)[0] == 201
    # Unknown page count (not processed yet): accepted, checked by the ingestion.
    fresh = admin.uploaded(b"%PDF-1.7 nuevo")
    assert create(admin, fresh, page_number=40)[0] == 201
    # The schema trigger is translated too.
    repo = D1DocumentRepository(lambda: admin.db)
    with pytest.raises(Exception) as info:
        asyncio.run(repo.create_override(NewOverride("pov_y", version_id, 9, "t", "r")))
    assert info.value.code == "page_out_of_range"


@pytest.mark.parametrize(
    "changes",
    [
        {"text": ""},
        {"text": " \n\t "},
        {"text": "x" * (MAX_OVERRIDE_TEXT + 1)},
        {"text": "a\rb"},
        {"text": "a\x00b"},
        {"text": "a\x1bb"},
        {"text": "a‮b"},
        {"text": "a​b"},
        {"text": "a﻿b"},
        {"reason": ""},
        {"reason": "   "},
        {"reason": "r" * 301},
        {"reason": "a\x07b"},
        {"reason": "a⁦b"},
        {"page_number": 0},
        {"page_number": "2"},
        {"page_number": 2.0},
        {"text": 5},
        {"extra": "x"},
    ],
)
def test_invalid_bodies_are_422(admin, changes):
    """Validation errors use the admin envelope and write nothing."""
    version_id = admin.ready()
    status, _, body = create(admin, version_id, **changes)
    assert status == 422 and error_code(body) == "invalid_request"
    assert "a‮b" not in body["error"]["message"]
    assert admin.db.sql("SELECT count(*) AS n FROM page_overrides")[0]["n"] == 0


def test_missing_fields_and_bad_json_are_422(admin):
    """Missing keys and malformed JSON are invalid_request."""
    version_id = admin.ready()
    for raw in (b"{not json", b"{}", json.dumps({"page_number": 1}).encode()):
        status, _, body = admin.request(
            "POST",
            path(version_id),
            body=raw,
            headers={**AUTH, "content-type": "application/json"},
        )
        assert status == 422 and error_code(body) == "invalid_request"


def test_limits_are_inclusive_and_tabs_newlines_allowed(admin):
    """20000 characters (after trimming) and 300-character reasons are accepted."""
    version_id = admin.ready()
    text = "á\tb\n" * (MAX_OVERRIDE_TEXT // 4)
    status, _, body = create(admin, version_id, text=text, reason="r" * 300)
    assert status == 201, body
    assert len(body["override"]["text"]) == MAX_OVERRIDE_TEXT - 1  # final \n trimmed


def test_override_blocks_review_and_publish_until_reprocessed(admin):
    """Same pending mechanism as corrections: 409 corrections_pending."""
    version_id = admin.ready()
    assert create(admin, version_id)[0] == 201
    status, _, body = admin.patch(version_id, {"requires_review": False})
    assert status == 409 and error_code(body) == "corrections_pending"
    status, _, body = admin.action(version_id, "publish")
    assert status == 409
    status, _, body = admin.request("GET", "/admin/documents")
    assert body["documents"][0]["versions"][0]["corrections_dirty"] is True
    admin.db.apply_corrections(version_id)
    assert admin.patch(version_id, {"requires_review": False})[0] == 200
    assert admin.action(version_id, "publish")[0] == 200


def test_delete_also_requires_review(admin):
    """Deleting an applied override sends a ready version back to review."""
    version_id = admin.ready()
    status, _, created = create(admin, version_id)
    admin.db.apply_corrections(version_id)
    assert admin.patch(version_id, {"requires_review": False})[0] == 200
    status, _, body = admin.request(
        "DELETE", path(version_id, f"/{created['override']['id']}")
    )
    assert status == 200
    assert body["version_status"] == "review_required"
    assert body["requires_review"] is True and body["corrections_dirty"] is True


@pytest.mark.parametrize(
    ("constant", "name"),
    [
        ("OVERRIDES", "overrides_by_version"),
        ("INSERT_OVERRIDE", "insert_override"),
        ("SOFT_DELETE_OVERRIDE", "soft_delete_override"),
    ],
)
def test_statements_match_canonical_queries(constant, name):
    """The adapter SQL is the canonical SQL of infra/d1/queries.sql."""
    assert " ".join(getattr(d1_module, constant).split()) == _canonical(name)


# --- page text ------------------------------------------------------------------


def pages_document(version_id: str, **changes: object) -> bytes:
    """Pages JSON as the ingestion chunk step writes it (3 pages)."""
    document = {
        "version_id": version_id,
        "chunker_version": "chunker-1",
        "page_count": 3,
        "corrections_rev": 1,
        "pages": [
            {
                "page_number": 1,
                "text": "Página uno",
                "source": "ocr",
                "override_id": None,
                "corrections_applied": ["cor_1"],
            },
            {
                "page_number": 2,
                "text": "Texto sustituido",
                "source": "override",
                "override_id": "pov_1",
                "corrections_applied": [],
            },
            {
                "page_number": 3,
                "text": "",
                "source": "ocr",
                "override_id": None,
                "corrections_applied": [],
            },
        ],
        **changes,
    }
    return json.dumps(document).encode()


def get_page(admin: Admin, version_id: str, page: object):
    """GET /admin/versions/{id}/pages?page=N."""
    return admin.request(
        "GET", f"/admin/versions/{version_id}/pages", query={"page": str(page)}
    )


def test_page_text_from_the_pages_json(admin):
    """Reads pages/{version_id}/{chunker_version}.json from R2."""
    version_id = admin.ready()
    admin.blobs.objects[f"pages/{version_id}/chunker-1.json"] = pages_document(
        version_id
    )
    status, _, body = get_page(admin, version_id, 2)
    assert status == 200
    assert body == {
        "version_id": version_id,
        "page_number": 2,
        "page_count": 3,
        "text": "Texto sustituido",
        "source": "override",
        "override_id": "pov_1",
        "corrections_applied": [],
        "chunker_version": "chunker-1",
    }
    status, _, body = get_page(admin, version_id, 1)
    assert (body["source"], body["override_id"], body["corrections_applied"]) == (
        "ocr",
        None,
        ["cor_1"],
    )
    assert get_page(admin, version_id, 3)[2]["text"] == ""


def test_page_text_unavailable(admin):
    """404 page_text_unavailable: never chunked, no JSON, stale or malformed."""
    fresh = admin.uploaded(b"%PDF-1.7 sin procesar")
    status, _, body = get_page(admin, fresh, 1)
    assert status == 404 and error_code(body) == "page_text_unavailable"
    version_id = admin.ready()
    status, _, body = get_page(admin, version_id, 1)  # processed before 4c
    assert status == 404 and error_code(body) == "page_text_unavailable"
    key = f"pages/{version_id}/chunker-1.json"
    for stored in (
        b"{not json",
        pages_document("otra"),
        pages_document(version_id, chunker_version="chunker-0"),
        pages_document(version_id, pages=[{"page_number": 1, "text": None}]),
        pages_document(version_id, page_count="3"),
    ):
        admin.blobs.objects[key] = stored
        status, _, body = get_page(admin, version_id, 1)
        assert status == 404 and error_code(body) == "page_text_unavailable"


@pytest.mark.parametrize("page", ["0", "-1", "x", ""])
def test_page_query_is_validated(admin, page):
    """The page query parameter is a required integer >= 1."""
    version_id = admin.ready()
    status, _, body = get_page(admin, version_id, page)
    assert status == 422 and error_code(body) == "invalid_request"
    status, _, body = admin.request("GET", f"/admin/versions/{version_id}/pages")
    assert status == 422 and error_code(body) == "invalid_request"


def test_page_beyond_page_count_is_422(admin):
    """page_out_of_range against the catalog page count."""
    version_id = admin.ready()
    admin.blobs.objects[f"pages/{version_id}/chunker-1.json"] = pages_document(
        version_id
    )
    status, _, body = get_page(admin, version_id, 4)
    assert status == 422 and error_code(body) == "page_out_of_range"


# --- R2 adapter -------------------------------------------------------------------


class Body:
    """R2ObjectBody double."""

    def __init__(self, text: str) -> None:
        """Keep the text."""
        self._text = text

    async def text(self) -> str:
        """Return the body as text."""
        return self._text


class Bucket:
    """R2 bucket double with get."""

    def __init__(self, objects: dict[str, str], fail: bool = False) -> None:
        """Keep objects."""
        self.objects, self.fail = objects, fail

    async def get(self, key: str):
        """Return a body or None."""
        if self.fail:
            raise RuntimeError("r2 down")
        return Body(self.objects[key]) if key in self.objects else None


def test_r2_get_json():
    """Parsed JSON, None when absent or invalid, unavailable on failure."""
    store = R2BlobStore(lambda: Bucket({"a": '{"x": 1}', "b": "{no"}))
    assert asyncio.run(store.get_json("a")) == {"x": 1}
    assert asyncio.run(store.get_json("b")) is None
    assert asyncio.run(store.get_json("c")) is None
    for bucket in (None, Bucket({}, fail=True)):
        with pytest.raises(StorageUnavailable):
            asyncio.run(R2BlobStore(lambda b=bucket: b).get_json("a"))


# --- early rejection (Bearer + body size before the ASGI bridge) ------------------


def _headers(**values: str):
    mapping = {k.replace("_", "-"): v for k, v in values.items()}
    return mapping.get


def test_page_override_body_limit_is_256_kib_only_for_post():
    """POST .../page-overrides gets 256 KiB; every other admin route keeps 64 KiB."""
    assert MAX_PAGE_OVERRIDE_BODY_BYTES == 256 * 1024
    assert admin_body_limit("POST", "/admin/versions/v1/page-overrides") == (
        MAX_PAGE_OVERRIDE_BODY_BYTES
    )
    for method, url in (
        ("DELETE", "/admin/versions/v1/page-overrides/pov_1"),
        ("POST", "/admin/versions/v1/page-overrides/pov_1"),
        ("PATCH", "/admin/versions/v1/page-overrides"),
        ("POST", "/admin/versions/v1/corrections"),
        ("POST", "/admin/versions/v1/x/page-overrides"),
        ("PATCH", "/admin/versions/v1"),
    ):
        assert admin_body_limit(method, url) == MAX_ADMIN_BODY_BYTES, (method, url)

    auth = f"Bearer {TOKEN}"

    def run(method, url, length, **extra):
        header = _headers(authorization=auth, content_length=str(length), **extra)
        return early_admin_rejection(method, url, header, TOKEN)

    target = "/admin/versions/v1/page-overrides"
    assert run("POST", target, 200 * 1024) is None
    assert run("POST", "//admin/versions/v1//page-overrides/", 256 * 1024) is None
    rejected = run("POST", target, 256 * 1024 + 1)
    assert rejected[0] == 413 and rejected[2]["error"]["code"] == "payload_too_large"
    assert run("POST", "/admin/versions/v1/corrections", 64 * 1024 + 1)[0] == 413
    assert run("DELETE", f"{target}/pov_1", 64 * 1024 + 1)[0] == 413
    # Authentication still comes first, and chunked bodies still need a length.
    no_auth = early_admin_rejection(
        "POST", target, _headers(content_length="10"), TOKEN
    )
    assert no_auth[0] == 401
    chunked = early_admin_rejection(
        "POST",
        target,
        _headers(authorization=auth, transfer_encoding="chunked"),
        TOKEN,
    )
    assert chunked[0] == 411
    assert early_admin_rejection("GET", path("v1"), _headers(), None)[0] == 503


def test_largest_valid_override_fits_the_early_limit():
    """The worst case (astral code points JSON-escaped, 12 bytes each) fits."""
    body = json.dumps(
        {
            "page_number": 99999,
            "text": "\U0001d11e" * MAX_OVERRIDE_TEXT,
            "reason": "\U0001d11e" * 300,
        }
    ).encode()
    assert len(body) <= MAX_PAGE_OVERRIDE_BODY_BYTES
    # Raw UTF-8 (what browsers send): 4-byte characters exceed the 64 KiB limit.
    raw = json.dumps(
        {**OVERRIDE, "text": "\U0001f4c4" * MAX_OVERRIDE_TEXT}, ensure_ascii=False
    ).encode()
    assert len(raw) > MAX_ADMIN_BODY_BYTES


def test_worker_applies_the_page_override_limit_before_the_bridge(worker):  # noqa: F811
    """The worker entrypoint refuses > 256 KiB and lets 200 KiB through."""
    db = FakeD1()
    target = "/admin/versions/v1/page-overrides"
    for length, reached in ((256 * 1024 + 1, False), (200 * 1024, True)):
        worker._app = None
        worker.sdk.calls.clear()
        incoming = Incoming(target, {}, headers={**AUTH, "content-length": str(length)})
        response = asyncio.run(entry(worker, DB=db, ADMIN_TOKEN=TOKEN).fetch(incoming))
        assert bool(worker.sdk.calls) is reached
        if not reached:
            assert response.status == 413
