from workers import asgi

from unitropico_rag.main import create_app

app = create_app()
Default = asgi.entrypoint(app)
