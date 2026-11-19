import asyncio
import re
from dataclasses import replace

import pytest
from admin_fakes import FakeD1

from unitropico_api.domain.admin import (
    Conflict,
    IngestionLaunchError,
    IngestionRequest,
    NewVersion,
    StorageUnavailable,
    instance_id,
)
from unitropico_api.infra.d1 import D1DocumentRepository, translate_error
from unitropico_api.infra.r2 import R2BlobStore
from unitropico_api.infra.workflow import WorkflowIngestionLauncher

REQUEST = IngestionRequest(
    version_id="abc123",
    document_id="doc1",
    source_r2_key="raw/" + "a" * 64 + ".pdf",
    source_sha256="a" * 64,
)


class Instance:
    """Workflow instance double."""

    def __init__(self, status: str, error: dict | None = None) -> None:
        """Keep the status payload and count restarts."""
        self.payload = {"status": status, "output": None, "error": error}
        self.restarts = 0

    async def status(self) -> dict:
        """Return the status payload."""
        return self.payload

    async def restart(self) -> None:
        """Count restarts."""
        self.restarts += 1


class Workflow:
    """Workflow binding double storing instances by id."""

    def __init__(self, fail_create: Exception | None = None) -> None:
        """Start empty."""
        self.instances: dict[str, Instance] = {}
        self.created: list[tuple[str, dict]] = []
        self.fail_create = fail_create

    async def create(self, *, id: str, params: dict) -> Instance:
        """Create or raise like the real binding."""
        if self.fail_create is not None:
            raise self.fail_create
        if id in self.instances:
            raise RuntimeError("instance.already_exists: instance already exists")
        self.created.append((id, params))
        self.instances[id] = Instance("queued")
        return self.instances[id]

    async def get(self, id: str) -> Instance:
        """Return or raise not_found."""
        if id not in self.instances:
            raise RuntimeError("instance.not_found")
        return self.instances[id]


def run(coroutine):
    """Run a coroutine to completion."""
    return asyncio.run(coroutine)


def test_instance_id_is_valid_and_stable():
    """Ids follow ver_{id} and the [A-Za-z0-9_-]{1,100} rule."""
    assert instance_id("abc_123-X") == "ver_abc_123-X"
    for raw in ("a b/c", "x" * 120, "ñ"):
        name = instance_id(raw)
        assert re.fullmatch(r"[A-Za-z0-9_-]{1,100}", name)
        assert name == instance_id(raw)
    assert instance_id("a b") != instance_id("a_b")


def test_launch_creates_with_params():
    """A new instance receives the ingestion params."""
    workflow = Workflow()
    outcome = run(WorkflowIngestionLauncher(lambda: workflow).launch(REQUEST))
    assert outcome.outcome == "created"
    assert workflow.created == [("ver_abc123", REQUEST.as_params())]


@pytest.mark.parametrize(
    ("status", "launch", "relaunch"),
    [
        ("errored", "restarted", "restarted"),
        ("terminated", "restarted", "restarted"),
        ("complete", "existing", "restarted"),
        ("running", "existing", "conflict"),
        ("queued", "existing", "conflict"),
        ("waiting", "existing", "conflict"),
    ],
)
def test_existing_instance_policy(status, launch, relaunch):
    """already_exists: reuse, restart when settled, conflict when active."""
    for method, expected in (("launch", launch), ("relaunch", relaunch)):
        workflow = Workflow()
        workflow.instances["ver_abc123"] = Instance(status)
        launcher = WorkflowIngestionLauncher(lambda w=workflow: w)
        if expected == "conflict":
            with pytest.raises(Conflict) as error:
                run(getattr(launcher, method)(REQUEST))
            assert error.value.code == "ingestion_in_progress"
            continue
        outcome = run(getattr(launcher, method)(REQUEST))
        assert outcome.outcome == expected
        assert workflow.instances["ver_abc123"].restarts == (
            1 if expected == "restarted" else 0
        )


def test_launch_failures_are_typed():
    """Missing binding or unexpected errors raise IngestionLaunchError."""
    with pytest.raises(IngestionLaunchError, match="BindingMissing"):
        run(WorkflowIngestionLauncher(lambda: None).launch(REQUEST))
    workflow = Workflow(fail_create=TimeoutError("secret detail"))
    with pytest.raises(IngestionLaunchError) as error:
        run(WorkflowIngestionLauncher(lambda: workflow).launch(REQUEST))
    assert error.value.reason == "TimeoutError"


def test_status():
    """Status maps the payload and returns None for unknown instances."""
    workflow = Workflow()
    launcher = WorkflowIngestionLauncher(lambda: workflow)
    assert run(launcher.status("abc123")) is None
    workflow.instances["ver_abc123"] = Instance(
        "errored", {"name": "OcrError", "message": "falló el OCR"}
    )
    state = run(launcher.status("abc123"))
    assert state.model_dump() == {
        "instance_id": "ver_abc123",
        "status": "errored",
        "error_name": "OcrError",
        "error_message": "falló el OCR",
    }


class Bucket:
    """R2 bucket double."""

    def __init__(self, fail: bool = False) -> None:
        """Start empty."""
        self.objects: dict[str, tuple[bytes, dict]] = {}
        self.fail = fail

    async def head(self, key: str):
        """Return metadata with size or None."""
        if self.fail:
            raise RuntimeError("r2 down")
        if key not in self.objects:
            return None
        return type("R2Object", (), {"size": len(self.objects[key][0])})()

    async def put(self, key: str, value: bytes, **options) -> None:
        """Store bytes and options."""
        self.objects[key] = (value, options)


def test_r2_put_is_idempotent_by_size():
    """Writes once with PDF metadata and checksum; same size is skipped."""
    bucket = Bucket()
    store = R2BlobStore(lambda: bucket)
    assert run(store.put_pdf("raw/x.pdf", b"%PDF-1", "f" * 64)) is True
    assert bucket.objects["raw/x.pdf"] == (
        b"%PDF-1",
        {"httpMetadata": {"contentType": "application/pdf"}, "sha256": "f" * 64},
    )
    assert run(store.put_pdf("raw/x.pdf", b"%PDF-1", "f" * 64)) is False
    assert run(store.put_pdf("raw/x.pdf", b"%PDF-12", "e" * 64)) is True


@pytest.mark.parametrize("bucket", [None, Bucket(fail=True)])
def test_r2_failures_are_unavailable(bucket):
    """Missing or failing bindings fail closed."""
    with pytest.raises(StorageUnavailable):
        run(R2BlobStore(lambda: bucket).put_pdf("k", b"%PDF-", "a" * 64))


def test_d1_missing_binding_is_unavailable():
    """The repository fails closed without a DB binding."""
    with pytest.raises(StorageUnavailable):
        run(D1DocumentRepository(lambda: None).list_documents())


@pytest.mark.parametrize(
    ("message", "code"),
    [
        (
            "D1_ERROR: invalid_status_transition: SQLITE_CONSTRAINT",
            "invalid_status_transition",
        ),
        ("D1_ERROR: published_version_immutable", "published_version_immutable"),
        (
            "UNIQUE constraint failed: document_versions.document_id, "
            "document_versions.version_label",
            "version_label_taken",
        ),
        (
            "UNIQUE constraint failed: document_versions.source_r2_key",
            "source_registered_elsewhere",
        ),
        ("CHECK constraint failed: x", "constraint_violation"),
        ("FOREIGN KEY constraint failed", "reference_not_found"),
        ("socket closed", "storage_unavailable"),
    ],
)
def test_translate_error(message, code):
    """D1 messages map to stable domain codes without leaking the message."""
    error = translate_error(RuntimeError(message))
    assert error.code == code
    assert message not in error.message


def test_create_version_is_idempotent_against_real_schema():
    """ON CONFLICT DO NOTHING + re-read returns the existing version."""
    db = FakeD1()
    repo = D1DocumentRepository(lambda: db)
    new = NewVersion(
        document_id="doc1",
        version_id="v1",
        create_document=True,
        title="T",
        document_type="otro",
        version_label="1",
        source_r2_key="raw/" + "b" * 64 + ".pdf",
        source_sha256="b" * 64,
        source_bytes=10,
    )
    view, created = run(repo.create_version(new))
    assert created and view.version.id == "v1"
    retry = replace(new, version_id="v2", create_document=False)
    view, created = run(repo.create_version(retry))
    assert not created and view.version.id == "v1"
    # Same bytes for a new document hit UNIQUE(source_r2_key): resolved by re-read.
    concurrent = replace(new, version_id="v3", document_id="doc2")
    view, created = run(repo.create_version(concurrent))
    assert not created and view.version.id == "v1"
    assert len(db.sql("SELECT id FROM documents")) == 1


def test_admin_layer_boundaries():
    """Domain/services stay free of HTTP, SDK and infra; infra ignores services."""
    import ast
    from pathlib import Path

    source = Path(__file__).parents[1] / "src" / "unitropico_api"
    forbidden = {
        "domain/admin.py": ("fastapi", "workers", "unitropico_api.infra"),
        "services/admin.py": ("fastapi", "workers", "unitropico_api.infra"),
        "infra/d1.py": ("fastapi", "unitropico_api.services"),
        "infra/r2.py": ("fastapi", "unitropico_api.services"),
        "infra/workflow.py": ("fastapi", "unitropico_api.services"),
    }
    for relative, prefixes in forbidden.items():
        tree = ast.parse((source / relative).read_text())
        modules = [
            node.module
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module
        ] + [
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        ]
        for module in modules:
            assert not module.startswith(prefixes), (relative, module)
