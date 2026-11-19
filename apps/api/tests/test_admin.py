import hashlib
import logging

import pytest
from admin_fakes import AUTH, TOKEN, FakeBlobs, FakeD1, FakeLauncher, call

from unitropico_api.api.admin import early_upload_rejection
from unitropico_api.core.settings import Settings
from unitropico_api.infra.d1 import D1DocumentRepository
from unitropico_api.main import create_app

PDF = b"%PDF-1.7\n1 0 obj\n<<>>\nendobj\n%%EOF\n"
META = {
    "title": "Reglamento estudiantil",
    "document_type": "reglamento",
    "version_label": "2024",
}
PDF_HEADERS = {**AUTH, "content-type": "application/pdf"}


class Admin:
    """App wired to fakes, with helpers for the admin routes."""

    def __init__(self, token: str | None = TOKEN, max_bytes: int = 1024) -> None:
        """Compose the real app with in-memory ports."""
        self.db = FakeD1()
        self.blobs = FakeBlobs()
        self.launcher = FakeLauncher()
        settings = Settings(admin_token=token, admin_max_upload_bytes=max_bytes)
        self.app = create_app(
            settings=settings,
            documents=D1DocumentRepository(lambda: self.db),
            blobs=self.blobs,
            ingestion=self.launcher,
        )

    def request(self, method: str, path: str, **kwargs):
        """Authenticated request unless headers are given."""
        kwargs.setdefault("headers", AUTH)
        return call(self.app, method, path, **kwargs)

    def upload(self, data: bytes = PDF, **meta: str):
        """POST /admin/uploads with default metadata."""
        return self.request(
            "POST",
            "/admin/uploads",
            body=data,
            headers=PDF_HEADERS,
            query={**META, **meta},
        )

    def uploaded(self, data: bytes = PDF, **meta: str) -> str:
        """Upload and return the new version id."""
        status, _, body = self.upload(data, **meta)
        assert status == 201, body
        return body["version"]["id"]

    def ready(self, review_done: bool = True) -> str:
        """Upload, simulate the Workflow until ready and optionally review."""
        version_id = self.uploaded()
        self.db.simulate_workflow(version_id, "ready")
        if review_done:
            status, _, body = self.patch(version_id, {"requires_review": False})
            assert status == 200, body
        return version_id

    def patch(self, version_id: str, body: dict):
        """PATCH /admin/versions/{id}."""
        return self.request("PATCH", f"/admin/versions/{version_id}", body=body)

    def action(self, version_id: str, name: str):
        """POST /admin/versions/{id}/{name}."""
        return self.request("POST", f"/admin/versions/{version_id}/{name}")


@pytest.fixture
def admin() -> Admin:
    """Fresh app and catalog per test."""
    return Admin()


def error_code(body: object) -> str:
    """Return the code of an admin error envelope."""
    assert isinstance(body, dict) and set(body) == {"error"}, body
    assert set(body["error"]) == {"code", "message"}
    return body["error"]["code"]


# --- auth -----------------------------------------------------------------

ROUTES = [
    ("GET", "/admin/documents"),
    ("POST", "/admin/uploads"),
    ("GET", "/admin/versions/v1"),
    ("PATCH", "/admin/versions/v1"),
    ("POST", "/admin/versions/v1/publish"),
    ("POST", "/admin/versions/v1/unpublish"),
    ("POST", "/admin/versions/v1/reprocess"),
    ("GET", "/admin/versions/v1/chunks"),
    ("GET", "/admin/versions/v1/corrections"),
    ("POST", "/admin/versions/v1/corrections"),
    ("DELETE", "/admin/versions/v1/corrections/cor_1"),
]


@pytest.mark.parametrize(("method", "path"), ROUTES)
@pytest.mark.parametrize("token", [None, ""])
def test_unconfigured_token_closes_every_route(method, path, token):
    """Without ADMIN_TOKEN all admin routes answer 503, never open."""
    app = Admin(token=token)
    for headers in ({}, AUTH, {"authorization": "Bearer "}):
        status, _, body = call(app.app, method, path, headers=headers)
        assert status == 503
        assert error_code(body) == "admin_disabled"


@pytest.mark.parametrize(("method", "path"), ROUTES)
@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"authorization": "Bearer wrong"},
        {"authorization": f"Basic {TOKEN}"},
        {"authorization": TOKEN},
        {"authorization": f"Bearer {TOKEN}x"},
        {"authorization": "Bearer ñandú"},
    ],
)
def test_invalid_credentials_are_401(admin, method, path, headers):
    """Bad or missing bearer tokens get 401 with WWW-Authenticate."""
    status, response_headers, body = call(
        admin.app, method, path, headers=headers, body=b"{not json"
    )
    assert status == 401
    assert response_headers["www-authenticate"] == "Bearer"
    assert error_code(body) == "unauthorized"


def test_valid_token_and_case_insensitive_scheme(admin):
    """The scheme is case-insensitive; the token is exact."""
    headers = {"authorization": f"bearer {TOKEN}"}
    assert admin.request("GET", "/admin/documents", headers=headers)[0] == 200


def test_token_never_logged(admin, caplog):
    """No log record contains the token, on success or failure."""
    logger = logging.getLogger("unitropico_api")
    logger.addHandler(caplog.handler)
    caplog.set_level(logging.DEBUG, logger="unitropico_api")
    try:
        admin.launcher.fail = True
        version_id = admin.uploaded()
        admin.request("GET", f"/admin/versions/{version_id}")
        admin.request("GET", "/admin/documents", headers={"authorization": "Bearer x"})
        admin.request(
            "GET", "/admin/documents", headers={"authorization": f"Bearer {TOKEN}0"}
        )
    finally:
        logger.removeHandler(caplog.handler)
    assert caplog.records, "se esperaban logs de la acción de administración"
    assert TOKEN not in caplog.text
    assert "s3cr3t" not in caplog.text


def test_non_admin_validation_keeps_default_shape(admin):
    """The admin error envelope does not leak into public routes."""
    status, _, body = call(admin.app, "POST", "/chat", body={"question": ""})
    assert status == 422
    assert "detail" in body


# --- upload ---------------------------------------------------------------


def test_upload_creates_version_and_launches(admin):
    """201: R2 object, D1 rows, Workflow instance and events."""
    status, _, body = admin.upload()
    assert status == 201
    assert body["created"] is True
    sha = hashlib.sha256(PDF).hexdigest()
    version = body["version"]
    assert version["status"] == "uploaded"
    assert version["source_sha256"] == sha
    assert version["source_bytes"] == len(PDF)
    assert version["requires_review"] is True
    assert version["version_label"] == "2024"
    assert body["document"]["title"] == "Reglamento estudiantil"
    assert body["document"]["document_type"] == "reglamento"
    assert admin.blobs.objects == {f"raw/{sha}.pdf": PDF}
    assert body["ingestion"] == {
        "instance_id": f"ver_{version['id']}",
        "outcome": "created",
        "status": "queued",
    }
    assert admin.launcher.calls == [
        (
            "launch",
            {
                "version_id": version["id"],
                "document_id": body["document"]["id"],
                "source_r2_key": f"raw/{sha}.pdf",
                "source_sha256": sha,
            },
        )
    ]
    row = admin.db.version(version["id"])
    assert row["source_r2_key"] == f"raw/{sha}.pdf"
    assert admin.db.events(version["id"]) == [
        ("upload", "succeeded", None, "source_stored"),
        ("upload", "succeeded", None, "workflow_created"),
    ]


def test_upload_trims_metadata(admin):
    """Title and label are stripped before storage."""
    status, _, body = admin.upload(title="  Manual  ", version_label=" v1 ")
    assert status == 201
    assert body["document"]["title"] == "Manual"
    assert body["version"]["version_label"] == "v1"


def test_duplicate_upload_returns_existing(admin):
    """Same bytes: 200 with the same version, no new put, insert or launch."""
    first = admin.upload()[2]
    status, _, body = admin.upload(title=" Reglamento estudiantil ")
    assert status == 200
    assert body["created"] is False
    assert body["ingestion"] is None
    assert body["version"]["id"] == first["version"]["id"]
    assert body["document"]["title"] == "Reglamento estudiantil"
    assert admin.blobs.puts == 1
    assert len(admin.launcher.calls) == 1
    assert len(admin.db.sql("SELECT id FROM document_versions")) == 1
    same_doc = admin.upload(document_id=first["document"]["id"])
    assert same_doc[0] == 200


def test_same_bytes_with_other_title_is_registered_elsewhere(admin):
    """Without document_id, an existing sha with other metadata is 409, not 200."""
    admin.upload()
    for meta in ({"title": "Otro título"}, {"document_type": "manual"}):
        status, _, body = admin.upload(**meta)
        assert status == 409, body
        assert error_code(body) == "source_registered_elsewhere"
        assert "otro documento" in body["error"]["message"]
    assert len(admin.db.sql("SELECT id FROM documents")) == 1


def test_title_and_type_optional_and_ignored_with_document_id(admin):
    """With document_id both are optional and never change the document."""
    first = admin.upload()[2]
    document_id = first["document"]["id"]
    query = {"version_label": "2025", "document_id": document_id}
    status, _, body = admin.request(
        "POST", "/admin/uploads", body=PDF + b"v2", headers=PDF_HEADERS, query=query
    )
    assert status == 201, body
    status, _, body = admin.upload(
        PDF + b"v3", document_id=document_id, version_label="2026", title="Ignorado"
    )
    assert status == 201
    assert body["document"]["title"] == "Reglamento estudiantil"


@pytest.mark.parametrize("missing", ["title", "document_type"])
def test_title_and_type_required_without_document_id(admin, missing):
    """A new document needs both fields."""
    query = {k: v for k, v in META.items() if k != missing}
    status, _, body = admin.request(
        "POST", "/admin/uploads", body=PDF, headers=PDF_HEADERS, query=query
    )
    assert status == 422
    assert error_code(body) == "invalid_request"
    assert admin.blobs.objects == {}


def test_new_version_of_existing_document(admin):
    """document_id adds a version to an existing document."""
    first = admin.upload()[2]
    document_id = first["document"]["id"]
    status, _, body = admin.upload(
        PDF + b"v2", document_id=document_id, version_label="2025"
    )
    assert status == 201
    assert body["document"]["id"] == document_id
    assert body["version"]["id"] != first["version"]["id"]
    assert len(admin.db.sql("SELECT id FROM documents")) == 1


@pytest.mark.parametrize(
    ("setup", "meta", "status", "code"),
    [
        ("none", {"document_id": "missing"}, 404, "document_not_found"),
        ("other_doc", {}, 409, "source_registered_elsewhere"),
        ("label", {"version_label": "2024"}, 409, "version_label_taken"),
    ],
)
def test_upload_conflicts(admin, setup, meta, status, code):
    """Unknown document, bytes of another document and duplicated labels."""
    data = PDF
    if setup != "none":
        first = admin.upload()[2]
        other = admin.upload(PDF + b"other", version_label="x")[2]
        target = other if setup == "other_doc" else first
        meta = {**meta, "document_id": target["document"]["id"]}
        data = PDF if setup == "other_doc" else PDF + b"new bytes"
    calls = len(admin.launcher.calls)
    got_status, _, body = admin.upload(data, **meta)
    assert got_status == status, body
    assert error_code(body) == code
    assert len(admin.launcher.calls) == calls


@pytest.mark.parametrize(
    ("data", "content_type", "status", "code"),
    [
        (b"hello world", "application/pdf", 422, "invalid_pdf"),
        (b"", "application/pdf", 422, "invalid_pdf"),
        (b" %PDF-1.4", "application/pdf", 422, "invalid_pdf"),
        (PDF, "text/plain", 415, "unsupported_media_type"),
        (PDF, "application/json", 415, "unsupported_media_type"),
        (PDF, "", 415, "unsupported_media_type"),
        (PDF + b"x" * 2000, "application/pdf", 413, "payload_too_large"),
    ],
)
def test_upload_rejections_store_nothing(admin, data, content_type, status, code):
    """Invalid bodies are rejected before R2, D1 or the Workflow."""
    headers = {**AUTH, "content-type": content_type} if content_type else AUTH
    got, _, body = admin.request(
        "POST", "/admin/uploads", body=data, headers=headers, query=META
    )
    assert got == status
    assert error_code(body) == code
    assert admin.blobs.objects == {}
    assert admin.db.sql("SELECT id FROM document_versions") == []
    assert admin.launcher.calls == []


def test_content_type_parameters_and_in_app_declared_length_check(admin):
    """Media type parameters are accepted; FastAPI also answers 413 on Content-Length.

    This does not prove the body is unread: that is the worker pre-check's job.
    """
    headers = {**AUTH, "content-type": "Application/PDF; charset=binary"}
    assert (
        admin.request("POST", "/admin/uploads", body=PDF, headers=headers, query=META)[
            0
        ]
        == 201
    )
    headers = {**PDF_HEADERS, "content-length": "999999"}
    status, _, body = admin.request(
        "POST", "/admin/uploads", body=PDF, headers=headers, query=META
    )
    assert status == 413
    assert error_code(body) == "payload_too_large"


@pytest.mark.parametrize(
    "query",
    [
        {k: v for k, v in META.items() if k != "title"},
        {**META, "title": "   "},
        {**META, "title": "x" * 201},
        {**META, "document_type": "memo"},
        {**META, "version_label": "x" * 81},
        {**META, "document_id": "bad id!"},
        {**META, "extra": "1"},
    ],
)
def test_upload_query_validation(admin, query):
    """Invalid metadata is a 422 envelope in Spanish."""
    status, _, body = admin.request(
        "POST", "/admin/uploads", body=PDF, headers=PDF_HEADERS, query=query
    )
    assert status == 422
    assert error_code(body) == "invalid_request"
    assert body["error"]["message"].startswith("Solicitud inválida")
    assert admin.blobs.objects == {}


def test_failed_launch_keeps_uploaded_and_reprocess_recovers(admin):
    """A Workflow failure is reported, logged and recoverable via reprocess."""
    admin.launcher.fail = True
    status, _, body = admin.upload()
    assert status == 201
    version_id = body["version"]["id"]
    assert body["ingestion"]["outcome"] == "failed"
    assert body["version"]["status"] == "uploaded"
    assert admin.db.events(version_id)[-1] == (
        "upload",
        "failed",
        "workflow_launch_failed",
        "Error",
    )
    admin.launcher.fail = False
    status, _, body = admin.action(version_id, "reprocess")
    assert status == 200
    assert body["ingestion"]["outcome"] == "created"


# --- list and detail --------------------------------------------------------


def test_list_documents_with_versions(admin):
    """Documents carry their versions with state and review fields."""
    assert admin.request("GET", "/admin/documents")[2] == {"documents": []}
    first = admin.upload()[2]
    admin.upload(PDF + b"2", document_id=first["document"]["id"], version_label="2025")
    admin.upload(PDF + b"3", title="Acuerdo", document_type="resolucion")
    status, _, body = admin.request("GET", "/admin/documents")
    assert status == 200
    titles = [d["title"] for d in body["documents"]]
    assert titles == ["Acuerdo", "Reglamento estudiantil"]
    versions = body["documents"][1]["versions"]
    assert [v["version_label"] for v in versions] == ["2025", "2024"]
    assert {
        "status",
        "requires_review",
        "error_code",
        "processing_attempts",
        "effective_from",
        "effective_until",
        "created_at",
    } <= set(versions[0])


def test_version_detail(admin):
    """Detail includes events, error detail and Workflow status."""
    version_id = admin.uploaded()
    status, _, body = admin.request("GET", f"/admin/versions/{version_id}")
    assert status == 200
    assert body["version"]["id"] == version_id
    assert [e["detail"] for e in body["processing_events"]] == [
        "source_stored",
        "workflow_created",
    ]
    assert body["workflow"] == {
        "instance_id": f"ver_{version_id}",
        "status": "complete",
        "error_name": None,
        "error_message": None,
    }
    assert body["error_detail"] is None


@pytest.mark.parametrize(
    ("method", "suffix"),
    [
        ("GET", ""),
        ("PATCH", ""),
        ("POST", "/publish"),
        ("POST", "/unpublish"),
        ("POST", "/reprocess"),
        ("GET", "/chunks"),
        ("GET", "/corrections"),
        ("DELETE", "/corrections/cor_1"),
    ],
)
def test_unknown_version_is_404(admin, method, suffix):
    """Every version route answers 404 for unknown ids."""
    body = {"title": "x"} if method == "PATCH" else None
    status, _, payload = admin.request(
        method, f"/admin/versions/nope{suffix}", body=body
    )
    assert status == 404
    assert error_code(payload) == "version_not_found"


def test_storage_failure_is_503(admin):
    """An unexpected D1 failure is a 503 without SQL details."""
    admin.db.broken = True
    status, _, body = admin.request("GET", "/admin/documents")
    assert status == 503
    assert error_code(body) == "storage_unavailable"
    assert "network" not in body["error"]["message"]


def test_missing_ports_fail_closed():
    """create_app without admin ports keeps routes closed with 503."""
    app = create_app(settings=Settings(admin_token=TOKEN))
    status, _, body = call(app, "GET", "/admin/documents", headers=AUTH)
    assert status == 503
    assert error_code(body) == "storage_unavailable"


# --- PATCH ----------------------------------------------------------------


def test_patch_metadata(admin):
    """All contract fields can be edited; the event records field names."""
    version_id = admin.uploaded()
    document_id = admin.db.version(version_id)["document_id"]
    older = admin.uploaded(PDF + b"old", version_label="2020", document_id=document_id)
    status, _, body = admin.patch(
        version_id,
        {
            "title": " Reglamento académico ",
            "document_type": "manual",
            "version_label": "2024-rev",
            "issued_at": "2024-02-29",
            "effective_from": "2024-03-01",
            "effective_until": None,
            "supersedes_version_id": older,
        },
    )
    assert status == 200, body
    assert body["document"]["title"] == "Reglamento académico"
    assert body["document"]["document_type"] == "manual"
    version = body["version"]
    assert version["version_label"] == "2024-rev"
    assert version["issued_at"] == "2024-02-29"
    assert version["effective_from"] == "2024-03-01"
    assert version["supersedes_version_id"] == older
    detail = admin.db.events(version_id)[-1]
    assert detail[:2] == ("review", "succeeded")
    assert detail[3] == (
        "metadata_updated:document_type,effective_from,effective_until,"
        "issued_at,supersedes_version_id,title,version_label"
    )
    status, _, body = admin.patch(version_id, {"supersedes_version_id": None})
    assert body["version"]["supersedes_version_id"] is None


@pytest.mark.parametrize(
    "patch",
    [
        {},
        {"issued_at": "2024-02-30"},
        {"issued_at": "2024/01/01"},
        {"issued_at": "20240101"},
        {"issued_at": 20240101},
        {"effective_from": "2024-05-01", "effective_until": "2024-04-30"},
        {"title": None},
        {"title": ""},
        {"version_label": None},
        {"requires_review": "false"},
        {"requires_review": None},
        {"document_type": "memo"},
        {"status": "published"},
        {"supersedes_version_id": "bad id!"},
    ],
)
def test_patch_validation(admin, patch):
    """Invalid bodies are 422 and change nothing."""
    version_id = admin.uploaded()
    before = admin.db.version(version_id)
    status, _, body = admin.patch(version_id, patch)
    assert status == 422, body
    assert error_code(body) == "invalid_request"
    assert admin.db.version(version_id) == before


def test_patch_invalid_json(admin):
    """Malformed JSON is a 422 envelope."""
    version_id = admin.uploaded()
    status, _, body = admin.request(
        "PATCH", f"/admin/versions/{version_id}", body=b"{oops"
    )
    assert status == 422
    assert error_code(body) == "invalid_request"


@pytest.mark.parametrize(
    ("patch", "code"),
    [
        ({"effective_until": "2024-01-01"}, "invalid_effective_range"),
        ({"supersedes_version_id": "SELF"}, "invalid_supersedes"),
        ({"supersedes_version_id": "missing"}, "invalid_supersedes"),
    ],
)
def test_patch_semantic_validation(admin, patch, code):
    """Range checks consider stored values; supersedes must exist and differ."""
    version_id = admin.uploaded()
    assert admin.patch(version_id, {"effective_from": "2024-06-01"})[0] == 200
    if patch.get("supersedes_version_id") == "SELF":
        patch = {"supersedes_version_id": version_id}
    status, _, body = admin.patch(version_id, patch)
    assert status == 422
    assert error_code(body) == code


def test_patch_duplicate_label_is_409(admin):
    """UNIQUE (document_id, version_label) becomes a domain conflict."""
    first = admin.upload()[2]
    second = admin.upload(
        PDF + b"2", document_id=first["document"]["id"], version_label="2025"
    )[2]
    status, _, body = admin.patch(second["version"]["id"], {"version_label": "2024"})
    assert status == 409
    assert error_code(body) == "version_label_taken"


def test_patch_published_version_is_409(admin):
    """A published version cannot be edited until unpublished."""
    version_id = admin.ready()
    assert admin.action(version_id, "publish")[0] == 200
    title = admin.db.sql("SELECT title FROM documents")[0]["title"]
    status, _, body = admin.patch(version_id, {"title": "Nuevo", "issued_at": None})
    assert status == 409
    assert error_code(body) == "version_published"
    assert admin.db.sql("SELECT title FROM documents")[0]["title"] == title
    assert admin.action(version_id, "unpublish")[0] == 200
    assert admin.patch(version_id, {"title": "Nuevo"})[0] == 200


def test_review_flag_drives_review_states(admin):
    """requires_review=false on review_required moves it to ready, and back."""
    version_id = admin.uploaded()
    status, _, body = admin.patch(version_id, {"requires_review": False})
    assert status == 409
    assert error_code(body) == "review_not_available"
    admin.db.simulate_workflow(version_id, "review_required")
    status, _, body = admin.patch(version_id, {"requires_review": False})
    assert status == 200
    assert body["version"]["status"] == "ready"
    assert body["version"]["requires_review"] is False
    status, _, body = admin.patch(version_id, {"requires_review": True})
    assert body["version"]["status"] == "review_required"
    assert body["version"]["requires_review"] is True


# --- publish / unpublish ----------------------------------------------------


def test_publish_only_reviewed_ready(admin):
    """Publishing needs ready + reviewed; it stamps published_at and logs."""
    version_id = admin.uploaded()
    status, _, body = admin.action(version_id, "publish")
    assert status == 409
    assert error_code(body) == "invalid_status_transition"

    admin.db.simulate_workflow(version_id, "ready")
    status, _, body = admin.action(version_id, "publish")
    assert status == 409
    assert error_code(body) == "review_required"

    admin.patch(version_id, {"requires_review": False})
    status, _, body = admin.action(version_id, "publish")
    assert status == 200
    assert body["version"]["status"] == "published"
    assert body["version"]["published_at"] is not None
    assert admin.db.events(version_id)[-1] == ("publish", "succeeded", None, None)

    status, _, body = admin.action(version_id, "publish")
    assert status == 409


def test_publish_trigger_errors_become_409(admin):
    """The schema's publish guard (no chunks) is translated to a domain code."""
    version_id = admin.ready()
    admin.db.sql(
        "UPDATE document_versions SET status='processing' WHERE id=?", version_id
    )
    admin.db.sql("DELETE FROM chunks WHERE version_id = ?", version_id)
    admin.db.sql(
        "UPDATE document_versions SET status='extracted' WHERE id=?", version_id
    )
    admin.db.sql("UPDATE document_versions SET status='ready' WHERE id=?", version_id)
    status, _, body = admin.action(version_id, "publish")
    assert status == 409
    assert error_code(body) == "publish_without_chunks"
    assert admin.db.version(version_id)["status"] == "ready"
    assert admin.db.events(version_id)[-1][0] != "publish"


def test_unpublish_and_republish(admin):
    """Published -> unpublished -> published; unpublish elsewhere is 409."""
    version_id = admin.ready()
    status, _, body = admin.action(version_id, "unpublish")
    assert status == 409
    assert error_code(body) == "invalid_status_transition"
    admin.action(version_id, "publish")
    status, _, body = admin.action(version_id, "unpublish")
    assert status == 200
    assert body["version"]["status"] == "unpublished"
    assert admin.db.events(version_id)[-1] == ("unpublish", "succeeded", None, None)
    assert admin.action(version_id, "publish")[0] == 200


# --- reprocess --------------------------------------------------------------


def test_reprocess_from_failed(admin):
    """failed: restart the instance, require a new review and log the request."""
    version_id = admin.uploaded()
    admin.db.simulate_workflow(version_id, "failed")
    status, _, body = admin.action(version_id, "reprocess")
    assert status == 200
    assert body["ingestion"] == {
        "instance_id": f"ver_{version_id}",
        "outcome": "restarted",
        "status": "queued",
    }
    assert admin.launcher.calls[-1][0] == "relaunch"
    assert admin.db.events(version_id)[-2:] == [
        ("upload", "succeeded", None, "reprocess_requested"),
        ("upload", "succeeded", None, "workflow_restarted"),
    ]


def test_reprocess_ready_resets_review(admin):
    """Reprocessing a reviewed version requires reviewing it again."""
    version_id = admin.ready()
    status, _, body = admin.action(version_id, "reprocess")
    assert status == 200
    assert body["version"]["requires_review"] is True


@pytest.mark.parametrize("state", ["processing", "extracted"])
def test_reprocess_unsticks_dead_instances(admin, state):
    """processing/extracted with a finished instance can be reprocessed."""
    version_id = admin.uploaded()
    admin.db.simulate_workflow(version_id, "processing")
    if state == "extracted":
        admin.db.sql(
            "UPDATE document_versions SET status = 'extracted', page_count = 1, "
            "extraction_r2_key = 'e', chunker_version = 'c' WHERE id = ?",
            version_id,
        )
    admin.launcher.instances.clear()
    status, _, body = admin.action(version_id, "reprocess")
    assert status == 200, body
    assert body["ingestion"]["outcome"] == "created"
    assert body["version"]["requires_review"] is True
    assert admin.db.version(version_id)["status"] == state


def test_reprocess_active_instance_leaves_catalog_untouched(admin):
    """A 409 from a running instance writes neither requires_review nor events."""
    version_id = admin.ready()
    before = (
        admin.db.version(version_id)["requires_review"],
        admin.db.events(version_id),
    )
    admin.launcher.active = True
    status, _, body = admin.action(version_id, "reprocess")
    assert status == 409
    assert error_code(body) == "ingestion_in_progress"
    assert (
        admin.db.version(version_id)["requires_review"],
        admin.db.events(version_id),
    ) == before
    assert [kind for kind, _ in admin.launcher.calls].count("relaunch") == 0


def test_reprocess_race_after_mark_logs_error_event(admin):
    """If the instance becomes active between check and relaunch, record it."""
    version_id = admin.ready()
    admin.launcher.active_on_relaunch = True
    status, _, body = admin.action(version_id, "reprocess")
    assert status == 409
    assert error_code(body) == "ingestion_in_progress"
    assert admin.db.events(version_id)[-1][:3] == (
        "upload",
        "failed",
        "ingestion_in_progress",
    )


def test_reprocess_forbidden_published(admin):
    """Published versions cannot be reprocessed."""
    version_id = admin.ready()
    admin.action(version_id, "publish")
    calls = len(admin.launcher.calls)
    status, _, body = admin.action(version_id, "reprocess")
    assert status == 409
    assert error_code(body) == "invalid_status_transition"
    assert len(admin.launcher.calls) == calls


def test_reprocess_active_instance_and_launch_failure(admin):
    """An active instance is 409; a binding failure is 503 and logged."""
    version_id = admin.uploaded()
    admin.db.simulate_workflow(version_id, "failed")
    admin.launcher.active = True
    status, _, body = admin.action(version_id, "reprocess")
    assert status == 409
    assert error_code(body) == "ingestion_in_progress"
    admin.launcher.active = False
    admin.launcher.fail = True
    status, _, body = admin.action(version_id, "reprocess")
    assert status == 503
    assert error_code(body) == "workflow_launch_failed"
    assert admin.db.events(version_id)[-1][:3] == (
        "upload",
        "failed",
        "workflow_launch_failed",
    )


def test_settings_hide_token():
    """The secret is read from ADMIN_TOKEN and never shown in repr."""
    settings = Settings.from_mapping({"ADMIN_TOKEN": TOKEN})
    assert settings.admin_token.get_secret_value() == TOKEN
    assert TOKEN not in repr(settings)
    assert TOKEN not in str(settings.model_dump())


# --- review fixes ------------------------------------------------------------


def test_title_patch_blocked_when_document_has_published_version(admin):
    """A draft cannot rename a document whose other version is published."""
    v1 = admin.ready()
    assert admin.action(v1, "publish")[0] == 200
    document_id = admin.db.version(v1)["document_id"]
    v2 = admin.uploaded(PDF + b"v2", document_id=document_id, version_label="2025")
    for body in (
        {"title": "Cambiado"},
        {"document_type": "manual", "issued_at": "2025-01-01"},
    ):
        status, _, response = admin.patch(v2, body)
        assert status == 409, response
        assert error_code(response) == "document_has_published_version"
    document = admin.db.sql("SELECT title, document_type FROM documents")[0]
    assert document == {"title": META["title"], "document_type": "reglamento"}
    assert admin.db.version(v2)["issued_at"] is None  # nothing was applied
    # version-only edits of the draft remain possible
    assert admin.patch(v2, {"issued_at": "2025-01-01"})[0] == 200
    # after unpublishing, the document metadata can change again
    assert admin.action(v1, "unpublish")[0] == 200
    assert admin.patch(v2, {"title": "Cambiado"})[0] == 200
    assert admin.patch(v1, {"title": "x"})[0] == 200


def test_supersedes_must_be_same_document_and_acyclic(admin):
    """Cross-document and cyclic chains are rejected."""
    a1 = admin.uploaded()
    document_id = admin.db.version(a1)["document_id"]
    a2 = admin.uploaded(PDF + b"2", document_id=document_id, version_label="2")
    a3 = admin.uploaded(PDF + b"3", document_id=document_id, version_label="3")
    other = admin.uploaded(PDF + b"o", version_label="o", title="Otro")
    status, _, body = admin.patch(a2, {"supersedes_version_id": other})
    assert status == 422
    assert error_code(body) == "supersedes_other_document"
    assert admin.patch(a2, {"supersedes_version_id": a1})[0] == 200
    assert admin.patch(a3, {"supersedes_version_id": a2})[0] == 200
    status, _, body = admin.patch(a1, {"supersedes_version_id": a3})
    assert status == 409
    assert error_code(body) == "supersedes_cycle"
    assert admin.db.version(a1)["supersedes_version_id"] is None


def test_upload_limit_is_capped_at_ingestion_limit():
    """ADMIN_MAX_UPLOAD_BYTES cannot exceed the 50 MB the ingestion accepts."""
    assert Settings().admin_max_upload_bytes == 25 * 1024 * 1024
    assert Settings(admin_max_upload_bytes=50_000_000)
    with pytest.raises(ValueError):
        Settings(admin_max_upload_bytes=50_000_001)


@pytest.mark.parametrize(
    ("method", "path", "headers", "token", "expected"),
    [
        ("GET", "/admin/uploads", {}, TOKEN, None),
        ("POST", "/admin/documents", {}, TOKEN, None),
        ("POST", "/admin/uploads", {}, None, 503),
        ("POST", "//admin//uploads/", {}, TOKEN, 401),
        ("POST", "/admin/uploads", {"authorization": "Bearer x"}, TOKEN, 401),
        ("POST", "/admin/uploads", {"authorization": f"Basic {TOKEN}"}, TOKEN, 401),
        ("POST", "/admin/uploads", AUTH, TOKEN, 411),
        ("POST", "/admin/uploads", {**AUTH, "content-length": "x1"}, TOKEN, 411),
        ("POST", "/admin/uploads", {**AUTH, "content-length": "11"}, TOKEN, 413),
        ("POST", "/admin/uploads", {**AUTH, "content-length": "10"}, TOKEN, None),
        ("post", "/admin/uploads", {"AUTHORIZATION": f"bearer {TOKEN}"}, TOKEN, 411),
    ],
)
def test_early_upload_rejection_decision(method, path, headers, token, expected):
    """Pure header-only decision: 503, 401, 411, 413 in that order."""
    lowered = {k.lower(): v for k, v in headers.items()}
    result = early_upload_rejection(method, path, lowered.get, token, 10)
    assert (result[0] if result else None) == expected
    if result:
        assert set(result[2]) == {"error"}
        assert ("WWW-Authenticate" in result[1]) == (expected == 401)
