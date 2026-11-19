import socket

import pytest

from unitropico_eval.client import JsonClient


def test_post_sends_json_and_parses_response(fake_server):
    server = fake_server(lambda path, body: (200, {"echo": body, "path": path}))
    result = JsonClient(server.url + "/").post(
        "/retrieval/query", {"question": "¿Qué?"}
    )
    assert result.status == 200
    assert result.body == {"echo": {"question": "¿Qué?"}, "path": "/retrieval/query"}
    assert result.error is None
    assert result.latency_ms >= 0
    assert server.requests == [("/retrieval/query", {"question": "¿Qué?"})]


def test_http_error_keeps_status_and_body(fake_server):
    server = fake_server(lambda path, body: (503, {"status": "unavailable"}))
    result = JsonClient(server.url).post("/answer", {"question": "x"})
    assert result.status == 503
    assert result.body == {"status": "unavailable"}


def test_non_json_and_non_object_bodies(fake_server):
    server = fake_server(lambda path, body: (200, b"<html>"))
    assert JsonClient(server.url).post("/a", {}).error == "respuesta no JSON"
    server.handler = lambda path, body: (200, [1, 2])
    assert JsonClient(server.url).post("/a", {}).error == "JSON no es un objeto"


def test_redirects_are_not_followed(fake_server):
    server = fake_server(lambda path, body: (307, {}))
    result = JsonClient(server.url).post("/answer", {"question": "x"})
    assert result.status == 307
    assert len(server.requests) == 1


def test_connection_refused_is_reported_not_raised():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    result = JsonClient(f"http://127.0.0.1:{port}", timeout=2).post("/a", {})
    assert result.status is None
    assert result.body is None
    assert result.error


def test_rejects_non_http_urls():
    with pytest.raises(ValueError):
        JsonClient("file:///etc/passwd")
