from fastapi import APIRouter

router = APIRouter()


@router.get("/health")
async def health() -> dict[str, str]:
    """Confirma salud HTTP local, sin evaluar RAG ni servicios externos."""
    return {"status": "ok"}
