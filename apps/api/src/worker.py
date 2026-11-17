from workers import WorkerEntrypoint, asgi

from unitropico_api.infra.service_binding import RagServiceBindingClient
from unitropico_api.main import create_app


class Default(WorkerEntrypoint):
    """Compose the API with the current request's native RAG binding."""

    async def fetch(self, request):
        """Pass the real environment and execution context to the SDK ASGI bridge."""
        binding = getattr(self.env, "RAG", None)
        client = RagServiceBindingClient(binding) if binding is not None else None
        app = create_app(rag_client=client)
        return await asgi.fetch(app, request, self.env, self.ctx)
