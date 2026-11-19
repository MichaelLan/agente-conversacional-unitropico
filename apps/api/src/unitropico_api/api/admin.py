import hmac
import posixpath
import re
from collections.abc import Callable
from typing import Annotated

from fastapi import APIRouter, Depends, FastAPI, Path, Query, Request
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi.security.utils import get_authorization_scheme_param
from pydantic import ValidationError

from unitropico_api.domain.admin import (
    AdminDisabled,
    AdminError,
    ChunkPage,
    ChunksQuery,
    Conflict,
    CorrectionCreate,
    CorrectionList,
    CorrectionResult,
    DocumentList,
    InvalidInput,
    NotFound,
    OverrideCreate,
    OverrideList,
    OverrideResult,
    PagesQuery,
    PageTextView,
    PayloadTooLarge,
    ReprocessResult,
    StorageUnavailable,
    Unauthorized,
    UnsupportedMedia,
    UploadMetadata,
    UploadResult,
    VersionDetail,
    VersionPatch,
    VersionView,
)
from unitropico_api.services.admin import AdminService

ADMIN_PREFIX = "/admin"
STATUS_BY_ERROR: dict[type[AdminError], int] = {
    Unauthorized: 401,
    NotFound: 404,
    Conflict: 409,
    PayloadTooLarge: 413,
    UnsupportedMedia: 415,
    InvalidInput: 422,
    AdminDisabled: 503,
    StorageUnavailable: 503,
}
VersionId = Annotated[str, Path(min_length=1, max_length=64)]
CorrectionId = Annotated[str, Path(min_length=1, max_length=64)]
OverrideId = Annotated[str, Path(min_length=1, max_length=64)]
_bearer = HTTPBearer(auto_error=False)


def error_body(code: str, message: str) -> dict[str, dict[str, str]]:
    """Admin error envelope."""
    return {"error": {"code": code, "message": message}}


def _invalid(error: ValidationError | RequestValidationError) -> InvalidInput:
    """Summarize validation errors in Spanish without echoing input values."""
    fields, notes = [], []
    for item in error.errors():
        location = [str(part) for part in item["loc"] if part not in ("query", "body")]
        if location:
            fields.append(".".join(location))
        if item["type"] == "value_error":
            notes.append(str(item["msg"]).removeprefix("Value error, "))
    message = "Solicitud inválida"
    if fields:
        message += ": revise " + ", ".join(dict.fromkeys(fields))
    if notes:
        message += ". " + "; ".join(dict.fromkeys(notes))
    return InvalidInput("invalid_request", message + ".")


def check_admin_token(expected: bytes | None, supplied: str | None) -> None:
    """Fail closed when unconfigured; compare in constant time otherwise."""
    if expected is None:
        raise AdminDisabled("admin_disabled", "La administración no está habilitada.")
    if not hmac.compare_digest((supplied or "").encode(), expected):
        raise Unauthorized("unauthorized", "Credenciales de administración inválidas.")


def make_admin_auth(token: str | None):
    """Build the dependency that enforces `Authorization: Bearer <ADMIN_TOKEN>`."""
    expected = token.encode() if token else None

    async def require_admin(
        credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
    ) -> None:
        """Authenticate with the shared token check."""
        check_admin_token(expected, credentials.credentials if credentials else None)

    return require_admin


UPLOAD_PATH = ADMIN_PREFIX + "/uploads"
_SLASHES = re.compile(r"/{2,}")


def is_upload_request(method: str, path: str) -> bool:
    """Whether the request is a POST to /admin/uploads (slashes normalized)."""
    normalized = posixpath.normpath(_SLASHES.sub("/", path))
    return method.upper() == "POST" and normalized == UPLOAD_PATH


def early_upload_rejection(
    method: str,
    path: str,
    header: Callable[[str], str | None],
    token: str | None,
    max_bytes: int,
) -> tuple[int, dict[str, str], dict[str, dict[str, str]]] | None:
    """Decide from headers alone whether an upload is refused before reading it.

    The Workers ASGI bridge buffers the whole body before FastAPI runs, so the
    worker calls this first. Order: 503 (no token), 401, 411 (no Content-Length),
    413 (too large). Returns (status, headers, body) or None to continue.
    """
    if not is_upload_request(method, path):
        return None
    scheme, supplied = get_authorization_scheme_param(header("authorization"))
    try:
        check_admin_token(
            token.encode() if token else None,
            supplied if scheme.lower() == "bearer" else None,
        )
    except AdminError as error:
        status = 503 if isinstance(error, AdminDisabled) else 401
        headers = {"WWW-Authenticate": "Bearer"} if status == 401 else {}
        return status, headers, error_body(error.code, error.message)
    length = (header("content-length") or "").strip()
    if not length.isascii() or not length.isdigit():
        return (
            411,
            {},
            error_body("length_required", "Se requiere el encabezado Content-Length."),
        )
    if int(length) > max_bytes:
        return (
            413,
            {},
            error_body(
                "payload_too_large",
                f"El archivo supera el límite de {max_bytes} bytes.",
            ),
        )
    return None


def install_admin_error_handlers(app: FastAPI) -> None:
    """Render admin errors as `{"error": {"code", "message"}}`."""

    async def admin_error(_: Request, error: Exception) -> JSONResponse:
        """Map a domain error to its HTTP status."""
        assert isinstance(error, AdminError)
        status = next(
            (s for cls, s in STATUS_BY_ERROR.items() if isinstance(error, cls)), 500
        )
        headers = {"WWW-Authenticate": "Bearer"} if status == 401 else None
        return JSONResponse(
            error_body(error.code, error.message), status_code=status, headers=headers
        )

    async def validation_error(request: Request, error: Exception) -> JSONResponse:
        """Use the admin envelope under /admin and FastAPI's default elsewhere."""
        assert isinstance(error, RequestValidationError)
        if not request.url.path.startswith(ADMIN_PREFIX + "/"):
            return await request_validation_exception_handler(request, error)
        return await admin_error(request, _invalid(error))

    app.add_exception_handler(AdminError, admin_error)
    app.add_exception_handler(RequestValidationError, validation_error)


def create_admin_router(service: AdminService, token: str | None) -> APIRouter:
    """Bind the admin use cases to `/admin/*`, all behind the bearer token."""
    router = APIRouter(
        prefix=ADMIN_PREFIX,
        tags=["admin"],
        dependencies=[Depends(make_admin_auth(token))],
        responses={401: {"description": "Token ausente o inválido"}},
    )

    @router.post(
        "/uploads",
        status_code=201,
        response_model=UploadResult,
        responses={200: {"description": "Mismo sha256 ya registrado"}},
        openapi_extra={
            "requestBody": {
                "required": True,
                "content": {
                    "application/pdf": {
                        "schema": {"type": "string", "format": "binary"}
                    }
                },
            }
        },
    )
    async def upload(
        request: Request, meta: Annotated[UploadMetadata, Query()]
    ) -> JSONResponse:
        """Store a PDF in R2, register it in D1 and launch its ingestion."""
        media = request.headers.get("content-type", "").split(";")[0].strip().lower()
        if media != "application/pdf":
            raise UnsupportedMedia(
                "unsupported_media_type", "El cuerpo debe ser application/pdf."
            )
        declared = request.headers.get("content-length", "")
        if declared.isdigit() and int(declared) > service.max_upload_bytes:
            raise PayloadTooLarge(
                "payload_too_large",
                f"El archivo supera el límite de {service.max_upload_bytes} bytes.",
            )
        result = await service.upload(meta, await request.body())
        return JSONResponse(
            result.model_dump(mode="json"), status_code=201 if result.created else 200
        )

    @router.get("/documents", response_model=DocumentList)
    async def list_documents() -> DocumentList:
        """List documents with their versions."""
        return await service.list_documents()

    @router.get("/versions/{version_id}", response_model=VersionDetail)
    async def get_version(version_id: VersionId) -> VersionDetail:
        """Return a version, its processing events and its Workflow status."""
        return await service.detail(version_id)

    @router.patch("/versions/{version_id}", response_model=VersionView)
    async def patch_version(request: Request, version_id: VersionId) -> VersionView:
        """Edit metadata; the JSON body is parsed only after authentication."""
        try:
            patch = VersionPatch.model_validate_json(await request.body())
        except ValidationError as error:
            raise _invalid(error) from None
        return await service.update_metadata(version_id, patch)

    @router.post("/versions/{version_id}/publish", response_model=VersionView)
    async def publish(version_id: VersionId) -> VersionView:
        """Publish a reviewed ready version."""
        return await service.publish(version_id)

    @router.post("/versions/{version_id}/unpublish", response_model=VersionView)
    async def unpublish(version_id: VersionId) -> VersionView:
        """Withdraw a published version."""
        return await service.unpublish(version_id)

    @router.post("/versions/{version_id}/reprocess", response_model=ReprocessResult)
    async def reprocess(version_id: VersionId) -> ReprocessResult:
        """Create or restart the ingestion Workflow of the version."""
        return await service.reprocess(version_id)

    @router.get("/versions/{version_id}/chunks", response_model=ChunkPage)
    async def list_chunks(
        version_id: VersionId, page: Annotated[ChunksQuery, Query()]
    ) -> ChunkPage:
        """Page through the stored chunks to review the extracted text."""
        return await service.list_chunks(version_id, page.offset, page.limit)

    @router.get("/versions/{version_id}/corrections", response_model=CorrectionList)
    async def list_corrections(version_id: VersionId) -> CorrectionList:
        """List active and deleted text corrections."""
        return await service.list_corrections(version_id)

    @router.post(
        "/versions/{version_id}/corrections",
        status_code=201,
        response_model=CorrectionResult,
        openapi_extra={
            "requestBody": {
                "required": True,
                "content": {
                    "application/json": {"schema": CorrectionCreate.model_json_schema()}
                },
            }
        },
    )
    async def create_correction(
        request: Request, version_id: VersionId
    ) -> CorrectionResult:
        """Record a literal correction; the JSON body is parsed after auth."""
        try:
            body = CorrectionCreate.model_validate_json(await request.body())
        except ValidationError as error:
            raise _invalid(error) from None
        return await service.create_correction(version_id, body)

    @router.delete(
        "/versions/{version_id}/corrections/{correction_id}",
        response_model=CorrectionResult,
    )
    async def delete_correction(
        version_id: VersionId, correction_id: CorrectionId
    ) -> CorrectionResult:
        """Logically delete a correction (kept for traceability)."""
        return await service.delete_correction(version_id, correction_id)

    @router.get("/versions/{version_id}/pages", response_model=PageTextView)
    async def page_text(
        version_id: VersionId, query: Annotated[PagesQuery, Query()]
    ) -> PageTextView:
        """Final prepared text of one page, as the last chunk step produced it."""
        return await service.page_text(version_id, query.page)

    @router.get("/versions/{version_id}/page-overrides", response_model=OverrideList)
    async def list_overrides(version_id: VersionId) -> OverrideList:
        """List active and deleted page overrides."""
        return await service.list_overrides(version_id)

    @router.post(
        "/versions/{version_id}/page-overrides",
        status_code=201,
        response_model=OverrideResult,
        openapi_extra={
            "requestBody": {
                "required": True,
                "content": {
                    "application/json": {"schema": OverrideCreate.model_json_schema()}
                },
            }
        },
    )
    async def create_override(
        request: Request, version_id: VersionId
    ) -> OverrideResult:
        """Record a full page override; the JSON body is parsed after auth."""
        try:
            body = OverrideCreate.model_validate_json(await request.body())
        except ValidationError as error:
            raise _invalid(error) from None
        return await service.create_override(version_id, body)

    @router.delete(
        "/versions/{version_id}/page-overrides/{override_id}",
        response_model=OverrideResult,
    )
    async def delete_override(
        version_id: VersionId, override_id: OverrideId
    ) -> OverrideResult:
        """Logically delete a page override (kept for traceability)."""
        return await service.delete_override(version_id, override_id)

    return router
