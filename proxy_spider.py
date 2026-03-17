"""
Proxy test spiders — verify per-request and global proxy routing.

Per-request proxy (compares direct vs proxied IP):
    uv run scrapy runspider proxy_spider.py -s SCRAPY_SPIDER=proxy -a proxy="http://user:pass@host:port"

Global proxy via RNET_PROXIES (all requests routed through proxy):
    SCRAPY_PROXY="http://user:pass@host:port" uv run scrapy runspider proxy_spider.py -s SCRAPY_SPIDER=global_proxy
"""

import json
import os
from urllib.parse import urlparse

import rnet
import scrapy


PROXY_URL = os.environ.get("SCRAPY_PROXY") or None

_BASE_SETTINGS = {
    "TWISTED_REACTOR": "twisted.internet.asyncioreactor.AsyncioSelectorReactor",
    "DOWNLOAD_HANDLERS": {
        "http":  "scrapy_rnet.RnetDownloadHandler",
        "https": "scrapy_rnet.RnetDownloadHandler",
    },
    "USER_AGENT": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    ),
    "LOG_LEVEL": "WARNING",
}


def _build_proxy(url: str) -> rnet.Proxy:
    parsed = urlparse(url)
    proxy_url = f"{parsed.scheme}://{parsed.hostname}:{parsed.port}"
    kwargs = {}
    if parsed.username:
        kwargs["username"] = parsed.username
    if parsed.password:
        kwargs["password"] = parsed.password
    return rnet.Proxy.all(proxy_url, **kwargs)


class ProxySpider(scrapy.Spider):
    """Per-request proxy test: compares direct IP vs proxied IP."""

    name = "proxy"

    custom_settings = {
        **_BASE_SETTINGS,
        # Disable Scrapy's proxy middleware — it strips credentials from
        # request.meta['proxy'] before the download handler sees them.
        # rnet handles proxy auth internally via the Proxy object.
        "DOWNLOADER_MIDDLEWARES": {
            "scrapy.downloadermiddlewares.httpproxy.HttpProxyMiddleware": None,
        },
    }

    def start_requests(self):
        # First request: no proxy — establishes the real IP baseline
        yield scrapy.Request(
            "https://httpbin.org/ip",
            callback=self.parse_direct,
            meta={"label": "direct (no proxy)"},
            dont_filter=True,
        )
        # Second request: via proxy using request.meta['proxy']
        proxy = getattr(self, "proxy", None) or PROXY_URL
        if proxy:
            yield scrapy.Request(
                "https://httpbin.org/ip",
                callback=self.parse_proxy,
                meta={"proxy": proxy, "label": f"via proxy ({proxy.split('@')[-1]})"},
                dont_filter=True,
            )
        else:
            self.logger.warning(
                "No proxy configured. Pass one with: "
                "-s PROXY_URL='http://user:pass@host:port' "
                "or spider attribute -a proxy='...'"
            )

    def parse_direct(self, response):
        self._print_result(response)

    def parse_proxy(self, response):
        self._print_result(response)

    def _print_result(self, response):
        data = json.loads(response.body)
        label = response.meta.get("label", "")
        ip = data.get("origin", "n/a")
        print(f"  {label:<35} IP: {ip}")


class GlobalProxySpider(scrapy.Spider):
    """Global proxy test: all requests routed through RNET_PROXIES.

    Usage:
        SCRAPY_PROXY="http://user:pass@host:port" uv run scrapy runspider proxy_spider.py -s SCRAPY_SPIDER=global_proxy
    """

    name = "global_proxy"

    @classmethod
    def from_crawler(cls, crawler, *args, **kwargs):
        spider = super().from_crawler(crawler, *args, **kwargs)
        proxy_url = getattr(spider, "proxy", None) or PROXY_URL
        if proxy_url:
            crawler.settings.set(
                "RNET_PROXIES",
                [_build_proxy(proxy_url)],
                priority="spider",
            )
        return spider

    custom_settings = _BASE_SETTINGS

    def start_requests(self):
        proxy_url = getattr(self, "proxy", None) or PROXY_URL
        if not proxy_url:
            self.logger.warning(
                "No proxy configured. Set SCRAPY_PROXY env var "
                "or pass -a proxy='http://user:pass@host:port'"
            )
        label = f"global proxy ({proxy_url.split('@')[-1]})" if proxy_url else "global proxy (none)"
        yield scrapy.Request(
            "https://httpbin.org/ip",
            callback=self.parse,
            meta={"label": label},
        )

    def parse(self, response):
        data = json.loads(response.body)
        label = response.meta.get("label", "")
        ip = data.get("origin", "n/a")
        print(f"  {label:<40} IP: {ip}")
