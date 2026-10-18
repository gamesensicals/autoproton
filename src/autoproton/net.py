"""Small HTTP client on top of urllib: one User-Agent, polite pacing, retries."""

from __future__ import annotations

import gzip
import json
import os
import ssl
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass

from . import USER_AGENT

# Where distributions keep their CA bundle. The AppImage brings its own
# OpenSSL, whose built-in default path may not exist on this distro.
SYSTEM_CA_FILES = (
    "/etc/ssl/certs/ca-certificates.crt",  # Debian, Ubuntu, Arch, SteamOS
    "/etc/pki/tls/certs/ca-bundle.crt",  # Fedora, RHEL
    "/etc/ssl/ca-bundle.pem",  # openSUSE
    "/etc/pki/ca-trust/extracted/pem/tls-ca-bundle.pem",
    "/etc/ssl/cert.pem",  # Alpine, Void
)


class NetError(RuntimeError):
    """Could not reach the server."""


class HttpError(NetError):
    def __init__(self, status: int, url: str):
        self.status = status
        self.url = url
        super().__init__(f"HTTP {status} from {urllib.parse.urlsplit(url).netloc}")


class Cancelled(RuntimeError):
    pass


def ssl_context(env: Mapping[str, str]) -> ssl.SSLContext:
    for var in ("SSL_CERT_FILE", "REQUESTS_CA_BUNDLE"):
        path = env.get(var)
        if path and os.path.isfile(path):
            return ssl.create_default_context(cafile=path)
    for path in SYSTEM_CA_FILES:
        if os.path.isfile(path):
            return ssl.create_default_context(cafile=path)
    try:
        import certifi

        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()


@dataclass
class Response:
    status: int
    body: bytes
    headers: dict


class Net:
    def __init__(
        self,
        env: Mapping[str, str] | None = None,
        timeout: float = 20.0,
        min_interval: float = 0.35,
        retries: int = 2,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ):
        env = os.environ if env is None else env
        self.timeout = timeout
        self.min_interval = min_interval
        self.retries = retries
        self.clock = clock
        self.sleep = sleep
        self._last: dict[str, float] = {}
        self._lock = threading.Lock()
        self._opener = urllib.request.build_opener(urllib.request.HTTPSHandler(context=ssl_context(env)))

    def _pace(self, url: str) -> None:
        host = urllib.parse.urlsplit(url).netloc
        with self._lock:
            now = self.clock()
            wait = self._last.get(host, -1e9) + self.min_interval - now
            self._last[host] = max(now, now + wait)
        if wait > 0:
            self.sleep(wait)

    def _open(self, url: str, headers: Mapping[str, str] | None):
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, **(headers or {})})
        return self._opener.open(req, timeout=self.timeout)

    def get(self, url: str, headers: Mapping[str, str] | None = None) -> Response:
        """GET ``url``. Raises HttpError for 4xx/5xx and NetError when unreachable."""
        attempt = 0
        while True:
            self._pace(url)
            try:
                with self._open(url, {"Accept-Encoding": "gzip", **(headers or {})}) as resp:
                    body = resp.read()
                    hdrs = {k.lower(): v for k, v in resp.headers.items()}
                    if hdrs.get("content-encoding") == "gzip":
                        body = gzip.decompress(body)
                    return Response(resp.status, body, hdrs)
            except urllib.error.HTTPError as e:
                if e.code >= 500 and attempt < self.retries:
                    attempt += 1
                    self.sleep(attempt * 1.5)
                    continue
                raise HttpError(e.code, url) from None
            except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as e:
                if attempt < self.retries:
                    attempt += 1
                    self.sleep(attempt * 1.5)
                    continue
                reason = getattr(e, "reason", e)
                raise NetError(f"Could not reach {urllib.parse.urlsplit(url).netloc}: {reason}") from None

    def get_json(self, url: str, headers: Mapping[str, str] | None = None):
        resp = self.get(url, headers)
        try:
            return json.loads(resp.body)
        except ValueError:
            raise NetError(f"{urllib.parse.urlsplit(url).netloc} sent something that isn't JSON") from None

    def download(
        self,
        url: str,
        out,
        progress: Callable[[int, int], None] | None = None,
        cancel: threading.Event | None = None,
        chunk: int = 1 << 20,
    ) -> int:
        """Stream ``url`` into the binary file object ``out``. Returns the number of bytes."""
        self._pace(url)
        try:
            resp = self._open(url, None)
        except urllib.error.HTTPError as e:
            raise HttpError(e.code, url) from None
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            raise NetError(f"Could not download from {urllib.parse.urlsplit(url).netloc}: {getattr(e, 'reason', e)}") from None
        with resp:
            total = int(resp.headers.get("Content-Length") or 0)
            done = 0
            while True:
                if cancel is not None and cancel.is_set():
                    raise Cancelled()
                try:
                    block = resp.read(chunk)
                except (TimeoutError, ConnectionError, OSError) as e:
                    raise NetError(f"The download stopped part-way ({e.__class__.__name__}). Try again.") from None
                if not block:
                    break
                out.write(block)
                done += len(block)
                if progress:
                    progress(done, total)
        if total and done != total:
            raise NetError(f"Download was cut off ({done} of {total} bytes)")
        return done
