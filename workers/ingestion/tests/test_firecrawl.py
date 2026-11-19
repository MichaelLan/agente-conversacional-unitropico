import asyncio
import json

import pytest

from unitropico_ingestion.domain.ingestion import IngestionError
from unitropico_ingestion.infra.firecrawl import FirecrawlOcrClient, FirecrawlRuntime

URL = "https://api.firecrawl.dev/v2/parse"
KEY = "fc-test-key"


class FakeBlob:
    """Test double: fake blob."""

    def __init__(self, parts, content_type=None):
        """Store the configuration."""
        self.parts, self.content_type = parts, content_type


class FakeFormData:
    """Test double: fake form data."""

    def __init__(self):
        """Store the configuration."""
        self.entries = []
        self.js_object = self

    def append(self, key, value, filename=None):
        """Mimic the runtime method of the same name."""
        self.entries.append((key, value, filename))

    def __setitem__(self, key, value):
        """Replace a field, like FormData.set."""
        self.entries = [e for e in self.entries if e[0] != key]
        self.entries.append((key, value, None))


class FakeResponse:
    """Test double: fake response."""

    def __init__(self, status, body):
        """Store the configuration."""
        self.status, self._body = status, body

    async def text(self):
        """Mimic the runtime method of the same name."""
        return self._body


class FakeJs:
    """Records ``js.fetch`` calls and the ``to_js`` conversion."""

    def __init__(self, response=None, error=None, delay=0.0):
        """Store the configuration."""
        self.response, self.error, self.delay = response, error, delay
        self.calls = []
        self.converted = []

    def to_js(self, value, dict_converter=None):
        """To js."""
        self.converted.append(dict_converter)
        return {"converted": value}

    async def fetch(self, url, init):
        """Mimic the runtime method of the same name."""
        self.calls.append((url, init))
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.error:
            raise self.error
        return self.response

    def runtime(self):
        """Return a FirecrawlRuntime wired to this fake."""
        return FirecrawlRuntime(
            fetch=self.fetch,
            to_js=self.to_js,
            from_entries="Object.fromEntries",
            form_data=FakeFormData,
            blob=FakeBlob,
        )


def _client(fake, key=KEY, timeout=5.0):
    return FirecrawlOcrClient(
        api_key=key, runtime=fake.runtime, url=URL, max_pages=1000, timeout=timeout
    )


def test_builds_multipart_request_with_file_options_and_auth():
    """Builds multipart request with file options and auth."""
    fake = FakeJs(FakeResponse(200, '{"success": true, "data": {}}'))
    body = asyncio.run(_client(fake).parse(b"%PDF-1.7 data", filename="abc.pdf"))
    assert body == b'{"success": true, "data": {}}'
    assert len(fake.calls) == 1
    url, init = fake.calls[0]
    assert url == URL
    assert fake.converted == ["Object.fromEntries"]
    request = init["converted"]
    assert request["method"] == "POST"
    assert request["headers"] == {"Authorization": f"Bearer {KEY}"}
    assert "Content-Type" not in request["headers"]  # runtime sets the boundary
    form = request["body"]
    assert isinstance(form, FakeFormData)
    (file_key, blob, filename), (opt_key, options, _) = form.entries
    assert (file_key, filename) == ("file", "abc.pdf")
    assert blob.parts == [b"%PDF-1.7 data"]
    assert blob.content_type == "application/pdf"
    assert opt_key == "options"
    parsed = json.loads(options)
    assert parsed["timeout"] == 300000
    assert parsed["parsers"] == [
        {
            "type": "pdf",
            "mode": "ocr",
            "maxPages": 1000,
            "pages": True,
            "blocks": True,
            "pageMarkers": True,
        }
    ]


@pytest.mark.parametrize(
    ("status", "permanent"),
    [
        (400, True),
        (401, True),
        (402, True),
        (403, True),
        (408, False),
        (429, False),
        (500, False),
        (503, False),
    ],
)
def test_maps_http_errors(status, permanent):
    """Maps http errors."""
    fake = FakeJs(FakeResponse(status, f'{{"error": "secret {KEY}"}}'))
    with pytest.raises(IngestionError) as info:
        asyncio.run(_client(fake).parse(b"%PDF", filename="a.pdf"))
    assert info.value.code == "ocr_failed"
    assert info.value.detail == f"status={status}"
    assert info.value.permanent is permanent


def test_transport_error_and_timeout_are_retryable():
    """Transport error and timeout are retryable."""
    with pytest.raises(IngestionError) as info:
        asyncio.run(
            _client(FakeJs(error=RuntimeError(KEY))).parse(b"%PDF", filename="a.pdf")
        )
    assert info.value.detail == "transport=RuntimeError" and not info.value.permanent
    with pytest.raises(IngestionError) as info:
        asyncio.run(
            _client(FakeJs(FakeResponse(200, "{}"), delay=1), timeout=0.01).parse(
                b"%PDF", filename="a.pdf"
            )
        )
    assert info.value.detail == "timeout" and not info.value.permanent


def test_missing_key_fails_before_any_request():
    """Missing key fails before any request."""
    fake = FakeJs(FakeResponse(200, "{}"))
    with pytest.raises(IngestionError) as info:
        asyncio.run(_client(fake, key=None).parse(b"%PDF", filename="a.pdf"))
    assert info.value.code == "config_missing" and info.value.permanent
    assert fake.calls == []
