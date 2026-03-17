# scrapy-rnet

A Scrapy [download handler](https://docs.scrapy.org/en/latest/topics/download-handlers.html) that routes all HTTP/HTTPS requests through [rnet](https://github.com/0x676e67/rnet), giving your spiders browser-grade TLS and HTTP/2 fingerprints via BoringSSL impersonation.

Without this, Scrapy uses Python's standard `urllib3`/`twisted` stack, which produces a fingerprint trivially identifiable as a bot. With `scrapy-rnet`, requests look indistinguishable from a real Chrome, Firefox, or Safari browser at the TLS and HTTP/2 layer.

## Requirements

- Python 3.13+
- Scrapy 2.14+
- rnet 2.4+

## Installation

```bash
uv add scrapy-rnet
# or
pip install scrapy-rnet
```

## Setup

Add to your Scrapy `settings.py`:

```python
TWISTED_REACTOR = "twisted.internet.asyncioreactor.AsyncioSelectorReactor"

DOWNLOAD_HANDLERS = {
    "http":  "scrapy_rnet.RnetDownloadHandler",
    "https": "scrapy_rnet.RnetDownloadHandler",
}
```

That's it. All requests will now go through rnet, impersonating Chrome 131 by default.

## Configuration

| Setting | Type | Default | Description |
|---|---|---|---|
| `RNET_IMPERSONATE` | `rnet.Impersonate` | `Chrome131` | Browser profile to impersonate |
| `RNET_IMPERSONATE_OS` | `rnet.ImpersonateOS` | `None` | OS to pair with the browser profile |
| `RNET_TIMEOUT` | `int` | `30` | Request timeout in seconds |
| `RNET_FOLLOW_REDIRECTS` | `bool` | `False` | Let rnet follow redirects (disables Scrapy's `RedirectMiddleware` for these requests) |
| `RNET_VERIFY_SSL` | `bool` | `True` | Verify TLS certificates |
| `RNET_PROXIES` | `list[rnet.Proxy]` | `None` | Proxy list; takes precedence over Scrapy's proxy settings |

### Choosing a browser profile

```python
import rnet

# Chrome (default)
RNET_IMPERSONATE = rnet.Impersonate.Chrome131

# Firefox
RNET_IMPERSONATE = rnet.Impersonate.Firefox133

# Safari
RNET_IMPERSONATE = rnet.Impersonate.Safari18

# Pair with a specific OS fingerprint
RNET_IMPERSONATE    = rnet.Impersonate.Chrome131
RNET_IMPERSONATE_OS = rnet.ImpersonateOS.Windows
```

Full list of available profiles: [rnet docs](https://rnet.readthedocs.io/en/latest/).

### Proxies

**Global proxy** (all requests):

```python
RNET_PROXIES = ["http://user:pass@proxy.example.com:8080"]
```

**Per-request proxy** via `request.meta['proxy']`:

```python
yield scrapy.Request(url, meta={"proxy": "http://user:pass@proxy.example.com:8080"})
```

> **Important:** Scrapy's built-in `HttpProxyMiddleware` strips credentials from the proxy URL before the download handler sees the request. Since rnet manages proxy auth internally, you must disable it:
>
> ```python
> DOWNLOADER_MIDDLEWARES = {
>     "scrapy.downloadermiddlewares.httpproxy.HttpProxyMiddleware": None,
> }
> ```

## How it works

`RnetDownloadHandler` implements Scrapy's [download handler interface](https://docs.scrapy.org/en/latest/topics/download-handlers.html) (`download_request` / `close`). When Scrapy resolves a request for an `http` or `https` URL it calls `download_request`, which:

1. Translates the Scrapy `Request` (method, URL, headers, body) into an rnet call
2. Sends the request through rnet's BoringSSL-backed async client
3. Converts the rnet response back into the appropriate Scrapy response subclass (`HtmlResponse`, `TextResponse`, etc.), preserving status, headers, body, IP address, and HTTP protocol version

A single `rnet.Client` instance is shared for the spider's lifetime, so connection pooling works as normal.

## Testing

```bash
# Unit tests only (no network required)
uv run pytest tests/ -m "not integration"

# All tests including real network calls
uv run pytest tests/
```

The integration suite includes a fingerprint verification test that hits `tls.peet.ws` and asserts the TLS/HTTP2 fingerprint matches Chrome 131.
