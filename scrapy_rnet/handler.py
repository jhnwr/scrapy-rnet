"""
Scrapy download handler that routes HTTP/HTTPS requests through rnet,
with async support and browser impersonation/emulation.
"""

from __future__ import annotations

import ipaddress
import logging
from typing import TYPE_CHECKING
from urllib.parse import urlparse

import rnet
from scrapy.http import Headers
from scrapy.responsetypes import responsetypes
from scrapy.utils.reactor import is_asyncio_reactor_installed

if TYPE_CHECKING:
    from scrapy.crawler import Crawler
    from scrapy.http import Request, Response

logger = logging.getLogger(__name__)


class RnetDownloadHandler:
    """
    Scrapy download handler that replaces the built-in HTTP/HTTPS transport
    with rnet, enabling browser-grade TLS and HTTP/2 fingerprinting.

    Register for both schemes in settings::

        DOWNLOAD_HANDLERS = {
            "http":  "scrapy_rnet.RnetDownloadHandler",
            "https": "scrapy_rnet.RnetDownloadHandler",
        }
        TWISTED_REACTOR = "twisted.internet.asyncioreactor.AsyncioSelectorReactor"

    Settings
    --------
    RNET_IMPERSONATE : rnet.Impersonate or None
        Browser profile to impersonate (e.g. ``rnet.Impersonate.Chrome131``).
        Defaults to ``rnet.Impersonate.Chrome131``.

    RNET_IMPERSONATE_OS : rnet.ImpersonateOS or None
        OS fingerprint to pair with the impersonation profile.
        Defaults to ``None`` (library chooses automatically).

    RNET_TIMEOUT : float
        Request timeout in seconds. Defaults to 30.

    RNET_FOLLOW_REDIRECTS : bool
        Let rnet follow redirects internally. Set to ``False`` (default) so
        Scrapy's ``RedirectMiddleware`` handles them as usual.

    RNET_VERIFY_SSL : bool
        Verify TLS certificates. Defaults to ``True``.

    RNET_PROXIES : list[str] or None
        Proxy URL strings applied to every request, e.g.
        ``["http://user:pass@host:port"]``. Credentials are parsed from the
        URL automatically. Defaults to ``None``.

    Per-request proxy via ``request.meta['proxy']``
        A proxy URL string set on an individual request overrides ``RNET_PROXIES``
        for that request only. Standard Scrapy format::

            yield scrapy.Request(url, meta={"proxy": "http://user:pass@host:port"})

        .. note::
            Scrapy's built-in ``HttpProxyMiddleware`` strips credentials from the
            proxy URL and replaces them with a ``Proxy-Authorization`` header before
            the download handler sees the request. Because rnet manages proxy auth
            internally, you must disable that middleware::

                DOWNLOADER_MIDDLEWARES = {
                    "scrapy.downloadermiddlewares.httpproxy.HttpProxyMiddleware": None,
                }
    """

    lazy = False

    def __init__(self, settings) -> None:
        if not is_asyncio_reactor_installed():
            raise RuntimeError(
                "RnetDownloadHandler requires the asyncio Twisted reactor. "
                "Set TWISTED_REACTOR = "
                "'twisted.internet.asyncioreactor.AsyncioSelectorReactor' "
                "in your settings."
            )

        impersonate = settings.get("RNET_IMPERSONATE", rnet.Impersonate.Chrome131)
        impersonate_os = settings.get("RNET_IMPERSONATE_OS", None)
        timeout = settings.getfloat("RNET_TIMEOUT", 30.0)
        follow_redirects = settings.getbool("RNET_FOLLOW_REDIRECTS", False)
        verify = settings.getbool("RNET_VERIFY_SSL", True)
        proxies_raw = settings.getlist("RNET_PROXIES", []) or None
        proxies = (
            [self._parse_proxy(p) if isinstance(p, str) else p for p in proxies_raw]
            if proxies_raw else None
        )

        client_kwargs: dict = dict(
            timeout=int(timeout),
            redirect=follow_redirects,
            verify=verify,
        )
        if impersonate is not None:
            client_kwargs["impersonate"] = impersonate
        if impersonate_os is not None:
            client_kwargs["impersonate_os"] = impersonate_os
        if proxies:
            client_kwargs["proxies"] = proxies

        self._client = rnet.Client(**client_kwargs)

        logger.debug(
            "RnetDownloadHandler initialised (impersonate=%s, timeout=%s)",
            impersonate,
            timeout,
        )

    @classmethod
    def from_crawler(cls, crawler: Crawler) -> "RnetDownloadHandler":
        return cls(crawler.settings)

    # ------------------------------------------------------------------
    # Download handler interface
    # ------------------------------------------------------------------

    async def download_request(self, request: Request, spider=None) -> Response:
        """Fetch *request* via rnet and return a Scrapy ``Response``."""
        method = self._scrapy_method_to_rnet(request.method)
        headers = self._scrapy_headers_to_dict(request.headers)
        body: bytes | None = request.body or None

        request_kwargs: dict = dict(headers=headers, body=body)

        # request.meta['proxy'] overrides the global RNET_PROXIES for this request
        meta_proxy: str | None = request.meta.get("proxy")
        if meta_proxy:
            request_kwargs["proxy"] = self._parse_proxy(meta_proxy)

        try:
            rnet_response = await self._client.request(
                method,
                request.url,
                **request_kwargs,
            )
        except rnet.TimeoutError as exc:
            raise TimeoutError(f"rnet timed out: {request.url}") from exc
        except (rnet.ConnectionError, rnet.ConnectionResetError) as exc:
            raise IOError(f"rnet connection error: {request.url}") from exc
        except rnet.RequestError as exc:
            raise IOError(f"rnet request error: {request.url}") from exc

        return await self._build_scrapy_response(rnet_response, request)

    async def close(self) -> None:
        logger.debug("RnetDownloadHandler closed")

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_proxy(url: str) -> rnet.Proxy:
        """Convert a proxy URL string to an ``rnet.Proxy`` object.

        Credentials embedded in the URL (``http://user:pass@host:port``) are
        extracted and passed as separate kwargs because rnet does not parse
        userinfo from the URL itself.
        """
        parsed = urlparse(url)
        proxy_url = f"{parsed.scheme}://{parsed.hostname}:{parsed.port}"
        kwargs: dict = {}
        if parsed.username:
            kwargs["username"] = parsed.username
        if parsed.password:
            kwargs["password"] = parsed.password
        return rnet.Proxy.all(proxy_url, **kwargs)

    @staticmethod
    def _scrapy_method_to_rnet(method: str) -> rnet.Method:
        mapping = {
            "GET": rnet.Method.GET,
            "POST": rnet.Method.POST,
            "PUT": rnet.Method.PUT,
            "DELETE": rnet.Method.DELETE,
            "HEAD": rnet.Method.HEAD,
            "OPTIONS": rnet.Method.OPTIONS,
            "PATCH": rnet.Method.PATCH,
            "TRACE": rnet.Method.TRACE,
        }
        try:
            return mapping[method.upper()]
        except KeyError:
            raise ValueError(f"Unsupported HTTP method: {method!r}")

    @staticmethod
    def _scrapy_headers_to_dict(headers: Headers) -> dict[str, str]:
        result: dict[str, str] = {}
        for key, values in headers.items():
            name = key.decode() if isinstance(key, bytes) else key
            value = values[-1].decode() if isinstance(values[-1], bytes) else values[-1]
            result[name] = value
        return result

    @staticmethod
    async def _build_scrapy_response(rnet_resp, request: Request) -> Response:
        body: bytes = await rnet_resp.bytes()

        headers: dict[str, str] = {}
        for key, value in rnet_resp.headers.items():
            name = key.decode() if isinstance(key, bytes) else key
            val = value.decode() if isinstance(value, bytes) else value
            headers[name] = val

        ip_address = None
        try:
            remote = rnet_resp.remote_addr
            if remote is not None:
                raw_ip = str(remote).split(":")[0] if ":" in str(remote) else str(remote)
                ip_address = ipaddress.ip_address(raw_ip)
        except Exception:
            pass

        protocol: str | None = None
        try:
            version_map = {
                "Version.HTTP_11": "HTTP/1.1",
                "Version.HTTP_2": "h2",
                "Version.HTTP_3": "h3",
            }
            protocol = version_map.get(str(rnet_resp.version))
        except Exception:
            pass

        response_cls = responsetypes.from_args(
            headers=headers,
            url=str(rnet_resp.url),
            body=body,
        )

        return response_cls(
            url=str(rnet_resp.url),
            status=rnet_resp.status,
            headers=headers,
            body=body,
            request=request,
            ip_address=ip_address,
            protocol=protocol,
        )
