# Pruebas de integración

Proyecto autónomo (uv, Python 3.14) que prueba la costura entre `apps/api` y
`apps/rag` en CPython:

- API FastAPI real -> cliente de service binding real -> RAG FastAPI real,
  conectados por ASGI en memoria (`support.AsgiBinding`).
- Dobles solo para el binding, retrievers, catálogo y gateway de IA; en
  `test_chat_d1.py` D1 es la migración real `infra/d1/migrations` sobre sqlite.

No prueba workerd/Pyodide, bindings reales, Vectorize ni despliegue.

`conftest.py` añade `apps/api/src` y `apps/rag/src` al `sys.path`: es el único
lugar del repo donde eso es legítimo; las apps no se importan entre sí.

```sh
uvx --from uv==0.13.7 uv sync --locked --group dev
uv run ruff check .
uv run ruff format --check .
uv run pytest -q
```

CI: `.github/workflows/integration.yml`. Las versiones de FastAPI, Pydantic y
LangChain Core coinciden con las de ambas apps; actualízalas a la vez.
