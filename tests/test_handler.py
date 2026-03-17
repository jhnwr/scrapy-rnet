"""
Tests for RnetDownloadHandler.

Unit tests use a lightweight MockRnetResponse and never touch the network.
Integration tests (marked ``integration``) make real HTTP calls to httpbin.org.

Run unit tests only:
    uv run pytest tests/ -m "not integration"

Run everything:
    uv run pytest tests/
"""

from __future__ import annotations

import ipaddress
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import rnet
from scrapy.http import HtmlResponse, Request, TextResponse
from scrapy.settings import Settings

from scrapy_rnet.handler import RnetDownloadHandler

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_settings(**overrides) -> Settings:
    """Return a Scrapy Settings object, optionally overriding RNET_* values."""
    base = {
        "RNET_IMPERSONATE": rnet.Impersonate.Chrome131,
        "RNET_TIMEOUT": 30.0,
        "RNET_FOLLOW_REDIRECTS": False,
        "RNET_VERIFY_SSL": True,
    }
    base.update(overrides)
    return Settings(values=base)


def make_handler(**setting_overrides) -> RnetDownloadHandler:
    """Instantiate a handler with the asyncio-reactor check patched out."""
    with patch("scrapy_rnet.handler.is_asyncio_reactor_installed", return_value=True):
        return RnetDownloadHandler(make_settings(**setting_overrides))


class MockVersion:
    def __init__(self, name: str):
        self._name = name

    def __str__(self) -> str:
        return self._name


class MockRnetResponse:
    """Minimal stand-in for an rnet Response."""

    def __init__(
        self,
        body: bytes = b"<html></html>",
        headers: dict | None = None,
        status: int = 200,
        remote_addr: str | None = "1.2.3.4:443",
        version: str = "Version.HTTP_2",
        url: str = "https://example.com/",
    ):
        self._body = body
        # Mimic rnet's HeaderMap.items() returning bytes pairs
        raw = headers or {"content-type": "text/html; charset=utf-8"}
        self._headers = [
            (k.encode() if isinstance(k, str) else k, v.encode() if isinstance(v, str) else v)
            for k, v in raw.items()
        ]
        self.status = status
        self.remote_addr = remote_addr
        self.version = MockVersion(version)
        self.url = url

    @property
    def headers(self):
        mock = MagicMock()
        mock.items.return_value = self._headers
        return mock

    async def bytes(self) -> bytes:
        return self._body


# ---------------------------------------------------------------------------
# _scrapy_method_to_rnet
# ---------------------------------------------------------------------------

class TestMethodMapping:
    @pytest.mark.parametrize("method,expected", [
        ("GET",     rnet.Method.GET),
        ("POST",    rnet.Method.POST),
        ("PUT",     rnet.Method.PUT),
        ("DELETE",  rnet.Method.DELETE),
        ("HEAD",    rnet.Method.HEAD),
        ("OPTIONS", rnet.Method.OPTIONS),
        ("PATCH",   rnet.Method.PATCH),
        ("TRACE",   rnet.Method.TRACE),
    ])
    def test_all_methods(self, method, expected):
        assert RnetDownloadHandler._scrapy_method_to_rnet(method) == expected

    def test_case_insensitive(self):
        assert RnetDownloadHandler._scrapy_method_to_rnet("get") == rnet.Method.GET

    def test_unknown_method_raises(self):
        with pytest.raises(ValueError, match="Unsupported HTTP method"):
            RnetDownloadHandler._scrapy_method_to_rnet("BREW")


# ---------------------------------------------------------------------------
# _scrapy_headers_to_dict
# ---------------------------------------------------------------------------

class TestHeadersToDict:
    def _make(self, raw: dict) -> dict:
        from scrapy.http import Headers
        return RnetDownloadHandler._scrapy_headers_to_dict(Headers(raw))

    def test_basic(self):
        result = self._make({"Content-Type": "text/html"})
        assert result["Content-Type"] == "text/html"

    def test_multiple_values_last_wins(self):
        from scrapy.http import Headers
        h = Headers()
        h.appendlist("X-Foo", "first")
        h.appendlist("X-Foo", "second")
        result = RnetDownloadHandler._scrapy_headers_to_dict(h)
        assert result["X-Foo"] == "second"

    def test_empty(self):
        assert self._make({}) == {}

    def test_bytes_key_and_value(self):
        from scrapy.http import Headers
        h = Headers({b"Accept": b"application/json"})
        result = RnetDownloadHandler._scrapy_headers_to_dict(h)
        assert result["Accept"] == "application/json"


# ---------------------------------------------------------------------------
# _build_scrapy_response
# ---------------------------------------------------------------------------

class TestBuildScrapyResponse:
    @pytest.mark.asyncio
    async def test_html_response_type(self):
        mock = MockRnetResponse(
            body=b"<html><body>hi</body></html>",
            headers={"content-type": "text/html; charset=utf-8"},
        )
        request = Request("https://example.com/")
        resp = await RnetDownloadHandler._build_scrapy_response(mock, request)
        assert isinstance(resp, HtmlResponse)

    @pytest.mark.asyncio
    async def test_json_response_type(self):
        mock = MockRnetResponse(
            body=b'{"ok": true}',
            headers={"content-type": "application/json"},
        )
        request = Request("https://example.com/api")
        resp = await RnetDownloadHandler._build_scrapy_response(mock, request)
        assert isinstance(resp, TextResponse)

    @pytest.mark.asyncio
    async def test_status_preserved(self):
        mock = MockRnetResponse(status=404)
        request = Request("https://example.com/missing")
        resp = await RnetDownloadHandler._build_scrapy_response(mock, request)
        assert resp.status == 404

    @pytest.mark.asyncio
    async def test_body_preserved(self):
        body = b"<html>hello</html>"
        mock = MockRnetResponse(body=body)
        request = Request("https://example.com/")
        resp = await RnetDownloadHandler._build_scrapy_response(mock, request)
        assert resp.body == body

    @pytest.mark.asyncio
    async def test_url_is_final_url(self):
        mock = MockRnetResponse(url="https://example.com/redirected")
        request = Request("https://example.com/original")
        resp = await RnetDownloadHandler._build_scrapy_response(mock, request)
        assert resp.url == "https://example.com/redirected"

    @pytest.mark.asyncio
    async def test_ip_address_parsed(self):
        mock = MockRnetResponse(remote_addr="93.184.216.34:443")
        request = Request("https://example.com/")
        resp = await RnetDownloadHandler._build_scrapy_response(mock, request)
        assert resp.ip_address == ipaddress.ip_address("93.184.216.34")

    @pytest.mark.asyncio
    async def test_ip_address_none_when_missing(self):
        mock = MockRnetResponse(remote_addr=None)
        request = Request("https://example.com/")
        resp = await RnetDownloadHandler._build_scrapy_response(mock, request)
        assert resp.ip_address is None

    @pytest.mark.asyncio
    @pytest.mark.parametrize("version_str,expected_protocol", [
        ("Version.HTTP_11", "HTTP/1.1"),
        ("Version.HTTP_2",  "h2"),
        ("Version.HTTP_3",  "h3"),
        ("Version.UNKNOWN", None),
    ])
    async def test_protocol_mapping(self, version_str, expected_protocol):
        mock = MockRnetResponse(version=version_str)
        request = Request("https://example.com/")
        resp = await RnetDownloadHandler._build_scrapy_response(mock, request)
        assert resp.protocol == expected_protocol

    @pytest.mark.asyncio
    async def test_request_attached(self):
        mock = MockRnetResponse()
        request = Request("https://example.com/")
        resp = await RnetDownloadHandler._build_scrapy_response(mock, request)
        assert resp.request is request


# ---------------------------------------------------------------------------
# __init__ / from_crawler
# ---------------------------------------------------------------------------

class TestInit:
    def test_raises_without_asyncio_reactor(self):
        with patch("scrapy_rnet.handler.is_asyncio_reactor_installed", return_value=False):
            with pytest.raises(RuntimeError, match="asyncio Twisted reactor"):
                RnetDownloadHandler(make_settings())

    def test_creates_client_with_defaults(self):
        handler = make_handler()
        assert handler._client is not None

    def test_impersonate_passed_to_client(self):
        with patch("scrapy_rnet.handler.is_asyncio_reactor_installed", return_value=True):
            with patch("rnet.Client") as mock_client:
                RnetDownloadHandler(make_settings(RNET_IMPERSONATE=rnet.Impersonate.Firefox133))
                call_kwargs = mock_client.call_args.kwargs
                assert call_kwargs["impersonate"] == rnet.Impersonate.Firefox133

    def test_custom_timeout_passed_to_client(self):
        with patch("scrapy_rnet.handler.is_asyncio_reactor_installed", return_value=True):
            with patch("rnet.Client") as mock_client:
                RnetDownloadHandler(make_settings(RNET_TIMEOUT=5.0))
                call_kwargs = mock_client.call_args.kwargs
                assert call_kwargs["timeout"] == 5.0

    def test_from_crawler(self):
        crawler = MagicMock()
        crawler.settings = make_settings()
        with patch("scrapy_rnet.handler.is_asyncio_reactor_installed", return_value=True):
            handler = RnetDownloadHandler.from_crawler(crawler)
        assert isinstance(handler, RnetDownloadHandler)


# ---------------------------------------------------------------------------
# download_request (unit — mocked rnet client)
# ---------------------------------------------------------------------------

class TestDownloadRequest:
    def _make_handler_with_mock_client(self, side_effect=None, return_value=None):
        """Return a handler whose _client is an AsyncMock (bypasses Rust read-only attrs)."""
        handler = make_handler()
        mock_client = MagicMock()
        mock_client.request = AsyncMock(
            return_value=return_value or MockRnetResponse(),
            side_effect=side_effect,
        )
        handler._client = mock_client
        return handler, mock_client

    @pytest.mark.asyncio
    async def test_get_request(self):
        mock_resp = MockRnetResponse(body=b"<html>ok</html>", status=200)
        handler, mock_client = self._make_handler_with_mock_client(return_value=mock_resp)

        resp = await handler.download_request(Request("https://example.com/"), spider=None)

        assert resp.status == 200
        assert resp.body == b"<html>ok</html>"
        mock_client.request.assert_called_once()
        call_args = mock_client.request.call_args
        assert call_args.args[0] == rnet.Method.GET
        assert call_args.args[1] == "https://example.com/"

    @pytest.mark.asyncio
    async def test_post_sends_body(self):
        mock_resp = MockRnetResponse(body=b"{}", headers={"content-type": "application/json"})
        handler, mock_client = self._make_handler_with_mock_client(return_value=mock_resp)

        req = Request("https://example.com/api", method="POST", body=b'{"x":1}')
        await handler.download_request(req, spider=None)

        call_kwargs = mock_client.request.call_args.kwargs
        assert mock_client.request.call_args.args[0] == rnet.Method.POST
        assert call_kwargs["body"] == b'{"x":1}'

    @pytest.mark.asyncio
    async def test_timeout_raises(self):
        handler, _ = self._make_handler_with_mock_client(side_effect=rnet.TimeoutError())
        with pytest.raises(TimeoutError, match="rnet timed out"):
            await handler.download_request(Request("https://example.com/"), spider=None)

    @pytest.mark.asyncio
    async def test_connection_error_raises(self):
        handler, _ = self._make_handler_with_mock_client(side_effect=rnet.ConnectionError())
        with pytest.raises(IOError, match="rnet connection error"):
            await handler.download_request(Request("https://example.com/"), spider=None)

    @pytest.mark.asyncio
    async def test_headers_forwarded(self):
        handler, mock_client = self._make_handler_with_mock_client()

        req = Request("https://example.com/", headers={"X-Token": "abc"})
        await handler.download_request(req, spider=None)

        call_kwargs = mock_client.request.call_args.kwargs
        assert call_kwargs["headers"].get("X-Token") == "abc"


# ---------------------------------------------------------------------------
# Integration tests (real network)
# ---------------------------------------------------------------------------

@pytest.mark.integration
class TestIntegration:
    """Hit httpbin.org to verify end-to-end behaviour. Skipped by default."""

    @pytest.mark.asyncio
    async def test_get_html(self):
        handler = make_handler()
        resp = await handler.download_request(Request("https://httpbin.org/html"), spider=None)
        assert resp.status == 200
        assert isinstance(resp, HtmlResponse)
        assert b"<html" in resp.body.lower()

    @pytest.mark.asyncio
    async def test_post_json(self):
        handler = make_handler()
        req = Request(
            "https://httpbin.org/post",
            method="POST",
            body=b'{"hello": "world"}',
            headers={"Content-Type": "application/json"},
        )
        resp = await handler.download_request(req, spider=None)
        assert resp.status == 200

    @pytest.mark.asyncio
    async def test_404_status(self):
        handler = make_handler()
        resp = await handler.download_request(
            Request("https://httpbin.org/status/404"), spider=None
        )
        assert resp.status == 404

    @pytest.mark.asyncio
    async def test_protocol_is_h2(self):
        handler = make_handler()
        resp = await handler.download_request(Request("https://httpbin.org/get"), spider=None)
        assert resp.protocol == "h2"

    @pytest.mark.asyncio
    async def test_ip_address_populated(self):
        handler = make_handler()
        resp = await handler.download_request(Request("https://httpbin.org/get"), spider=None)
        assert resp.ip_address is not None

    @pytest.mark.asyncio
    async def test_chrome_impersonation(self):
        """TLS and HTTP/2 fingerprints reported by tls.peet.ws must look like Chrome 131."""
        handler = make_handler(RNET_IMPERSONATE=rnet.Impersonate.Chrome131)
        resp = await handler.download_request(
            Request("https://tls.peet.ws/api/all"), spider=None
        )
        assert resp.status == 200

        import json
        data = json.loads(resp.body)

        # User-Agent must identify as Chrome (not Firefox/Safari/etc.)
        ua = data["user_agent"]
        assert "Chrome/131" in ua, f"Expected Chrome/131 in UA, got: {ua}"
        assert "AppleWebKit/537.36" in ua, f"Missing WebKit token in Chrome UA: {ua}"

        tls = data["tls"]

        # Chrome sends GREASE as the first cipher — a strong Chrome-specific signal.
        # GREASE values are randomised each handshake but always labelled "TLS_GREASE".
        first_cipher = tls["ciphers"][0]
        assert "GREASE" in first_cipher, (
            f"Expected GREASE as first cipher (Chrome fingerprint), got: {first_cipher}"
        )

        # JA4's middle component is the sorted-cipher hash — stable across GREASE
        # randomisation and a reliable indicator of the Chrome 131 cipher suite.
        ja4 = tls["ja4"]
        ja4_cipher_component = ja4.split("_")[1]
        assert ja4_cipher_component == "8daaf6152771", (
            f"JA4 cipher component doesn't match Chrome 131: {ja4}"
        )

        # HTTP/2 SETTINGS frame fingerprint (Akamai) is stable for Chrome 131.
        akamai_hash = data["http2"]["akamai_fingerprint_hash"]
        assert akamai_hash == "52d84b11737d980aef856699f885ca86", (
            f"Akamai HTTP/2 hash doesn't match Chrome 131: {akamai_hash}"
        )
