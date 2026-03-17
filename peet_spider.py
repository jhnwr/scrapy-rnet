"""
Quick test spider — runs against tls.peet.ws to verify Chrome impersonation.

Usage:
    uv run scrapy runspider peet_spider.py
"""

import json
import scrapy


class PeetSpider(scrapy.Spider):
    name = "peet"
    start_urls = ["https://tls.peet.ws/api/all"]

    custom_settings = {
        "TWISTED_REACTOR": "twisted.internet.asyncioreactor.AsyncioSelectorReactor",
        "DOWNLOAD_HANDLERS": {
            "http":  "scrapy_rnet.RnetDownloadHandler",
            "https": "scrapy_rnet.RnetDownloadHandler",
        },
        # Match the UA rnet sets internally for Chrome 131
        "USER_AGENT": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/131.0.0.0 Safari/537.36"
        ),
        "LOG_LEVEL": "WARNING",    # suppress Scrapy noise so results are readable
    }

    def parse(self, response):
        data = json.loads(response.body)

        ua        = data.get("user_agent", "n/a")
        http_ver  = data.get("http_version", "n/a")
        ja4       = data["tls"].get("ja4", "n/a")
        ja4_mid   = ja4.split("_")[1] if "_" in ja4 else "n/a"
        ciphers   = data["tls"].get("ciphers", [])
        first_c   = ciphers[0] if ciphers else "n/a"
        akamai    = data.get("http2", {}).get("akamai_fingerprint_hash", "n/a")

        self.logger.warning("")  # blank line before results
        print("=" * 60)
        print(f"  User-Agent   : {ua}")
        print(f"  HTTP version : {http_ver}")
        print(f"  First cipher : {first_c}")
        print(f"  JA4          : {ja4}")
        print(f"  JA4 (cipher) : {ja4_mid}")
        print(f"  Akamai h2    : {akamai}")
        print("=" * 60)

        grease_ok  = "GREASE" in first_c
        chrome_ok  = "Chrome" in ua
        ja4_ok     = ja4_mid == "8daaf6152771"
        akamai_ok  = akamai  == "52d84b11737d980aef856699f885ca86"

        print(f"  Chrome UA    : {'PASS' if chrome_ok  else 'FAIL'}")
        print(f"  GREASE       : {'PASS' if grease_ok  else 'FAIL'}")
        print(f"  JA4 cipher   : {'PASS' if ja4_ok     else 'FAIL'} (expect 8daaf6152771)")
        print(f"  Akamai h2    : {'PASS' if akamai_ok  else 'FAIL'}")
        print("=" * 60)
