"""
Proxy test spider — compares origin IP with and without a proxy.

Usage:
    uv run scrapy runspider proxy_spider.py -s PROXY_URL="http://user:pass@host:port"
"""

import json
import os
import scrapy


PROXY_URL = os.environ.get("SCRAPY_PROXY") or None


class ProxySpider(scrapy.Spider):
    name = "proxy"

    custom_settings = {
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
