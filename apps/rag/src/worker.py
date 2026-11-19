from workers import WorkerEntrypoint, asgi
from workers import fetch as workers_fetch

from unitropico_rag.core.settings import Settings
from unitropico_rag.infra.workers_fetch import WorkersFetch
from unitropico_rag.main import AppCache, Bindings, build_app, env_mapping

_apps = AppCache(
    lambda settings, bindings: build_app(
        settings, fetch=WorkersFetch(workers_fetch), bindings=bindings
    )
)


class Default(WorkerEntrypoint):
    """Compose the RAG app from this isolate's env and serve it through ASGI."""

    async def fetch(self, request):
        """Build or reuse the app for the current settings and bindings."""
        settings = Settings.from_mapping(env_mapping(self.env))
        app = _apps.get(settings, Bindings.from_env(self.env))
        return await asgi.fetch(app, request, self.env, self.ctx)
