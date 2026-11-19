import json
import threading
from collections.abc import Callable, Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

Handler = Callable[[str, dict], tuple[int, object]]


class FakeServer:
    """Local HTTP server on 127.0.0.1 with a pluggable JSON handler."""

    def __init__(self, handler: Handler) -> None:
        self.handler = handler
        self.requests: list[tuple[str, dict]] = []
        outer = self

        class _Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                length = int(self.headers.get("Content-Length", 0))
                body = json.loads(self.rfile.read(length) or b"{}")
                outer.requests.append((self.path, body))
                status, payload = outer.handler(self.path, body)
                raw = (
                    payload
                    if isinstance(payload, bytes)
                    else json.dumps(payload).encode()
                )
                self.send_response(status)
                if status in (301, 302, 307):
                    self.send_header("Location", "http://127.0.0.1:9/elsewhere")
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def log_message(self, *args: object) -> None:
                return

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    def __enter__(self) -> FakeServer:
        self.thread.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()


@pytest.fixture
def fake_server() -> Iterator[Callable[[Handler], FakeServer]]:
    servers: list[FakeServer] = []

    def start(handler: Handler) -> FakeServer:
        server = FakeServer(handler).__enter__()
        servers.append(server)
        return server

    yield start
    for server in servers:
        server.__exit__()
