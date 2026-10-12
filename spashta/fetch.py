"""
Polite HTTP fetching.

Every request Spashta makes goes through Fetcher, which:

  - identifies itself (SpashtaBot with a contact URL)
  - honours robots.txt for the host
  - waits between requests to the same host
  - refuses non-http(s) URLs, credentials in URLs and, unless allowed,
    private or local addresses (a hosted instance takes URLs from users),
    checking every redirect hop before requesting it
  - retries 429/503 and network errors, honouring Retry-After
  - can send conditional requests (ETag / Last-Modified) for feeds
  - caps the response size and decodes with the declared charset

The transport is a plain function so tests can swap in canned responses
without touching the network.
"""

from __future__ import annotations

import ipaddress
import re
import socket
import threading
import time
import urllib.error
import urllib.request
import urllib.robotparser
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Optional, Protocol
from urllib.parse import urljoin, urlparse

USER_AGENT = "SpashtaBot/0.1 (+https://github.com/arnabmanandhar/spashta)"
MAX_BYTES = 5 * 1024 * 1024
TIMEOUT_S = 20.0


class FetchError(RuntimeError):
    """kind is one of blocked, robots, http, network, too_large."""

    def __init__(self, kind: str, url: str, message: str = "", status: Optional[int] = None):
        self.kind, self.url, self.status = kind, url, status
        super().__init__(f"{kind}: {url}{f' (HTTP {status})' if status else ''}{' - ' + message if message else ''}")


@dataclass
class Response:
    url: str  # final URL after redirects
    status: int
    headers: dict[str, str]
    body: bytes

    @property
    def content_type(self) -> str:
        return self.headers.get("content-type", "").split(";")[0].strip().lower()

    @property
    def text(self) -> str:
        charset = None
        m = re.search(r"charset=([\w-]+)", self.headers.get("content-type", ""), re.I)
        if m:
            charset = m.group(1)
        else:
            m = re.search(rb"<meta[^>]+charset=[\"']?([\w-]+)", self.body[:4096], re.I)
            if m:
                charset = m.group(1).decode("ascii", "ignore")
        try:
            return self.body.decode(charset or "utf-8", errors="replace")
        except LookupError:
            return self.body.decode("utf-8", errors="replace")


Transport = Callable[[str, dict[str, str]], Response]


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Return 3xx responses instead of following them, so Fetcher can check
    every hop (scheme, private address, robots.txt) before requesting it."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_OPENER = urllib.request.build_opener(_NoRedirect)


def urllib_transport(url: str, headers: dict[str, str]) -> Response:
    req = urllib.request.Request(url, headers=headers)  # noqa: S310 - scheme checked by Fetcher
    try:
        with _OPENER.open(req, timeout=TIMEOUT_S) as r:
            body = r.read(MAX_BYTES + 1)
            return Response(r.geturl(), r.status, {k.lower(): v for k, v in r.headers.items()}, body)
    except urllib.error.HTTPError as e:
        return Response(url, e.code, {k.lower(): v for k, v in (e.headers or {}).items()}, b"")
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise FetchError("network", url, str(getattr(e, "reason", e))) from None


class HTTPCache(Protocol):
    """Validators and body of the last 200 response for a URL."""

    def get_cached(self, url: str) -> Optional[tuple[Optional[str], Optional[str], bytes, str]]: ...
    def put_cached(self, url: str, etag: Optional[str], last_modified: Optional[str], body: bytes, content_type: str) -> None: ...


def check_url(url: str, allow_private: bool = False) -> None:
    p = urlparse(url)
    if p.scheme not in ("http", "https") or not p.hostname:
        raise FetchError("blocked", url, "only http(s) URLs")
    if p.username or p.password:
        raise FetchError("blocked", url, "credentials in URL")
    if allow_private:
        return
    try:
        infos = socket.getaddrinfo(p.hostname, p.port or 443, proto=socket.IPPROTO_TCP)
    except socket.gaierror:
        raise FetchError("network", url, "host does not resolve") from None
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
            raise FetchError("blocked", url, "private or local address")


@dataclass
class Fetcher:
    transport: Transport = urllib_transport
    delay_s: float = 1.0  # between requests to one host
    respect_robots: bool = True
    allow_private: bool = False
    user_agent: str = USER_AGENT
    cache: Optional[HTTPCache] = None  # conditional GET for feeds and sitemaps
    max_redirects: int = 5
    retries: int = 2  # on 429, 503 and network errors, honouring Retry-After
    _robots: dict[str, Optional[urllib.robotparser.RobotFileParser]] = field(default_factory=dict, repr=False)
    _last: dict[str, float] = field(default_factory=dict, repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def _wait(self, host: str) -> None:
        with self._lock:
            wait = self._last.get(host, 0.0) + self.delay_s - time.monotonic()
            self._last[host] = time.monotonic() + max(wait, 0.0)
        if wait > 0:
            time.sleep(wait)

    def _once(self, url: str, headers: dict[str, str]) -> Response:
        for attempt in range(self.retries + 1):
            self._wait(urlparse(url).netloc)
            try:
                r = self.transport(url, headers)
            except FetchError:
                if attempt == self.retries:
                    raise
                time.sleep(min(2 ** attempt, 8))
                continue
            if r.status in (429, 503) and attempt < self.retries:
                retry_after = r.headers.get("retry-after", "")
                time.sleep(min(float(retry_after), 30.0) if retry_after.isdigit() else min(2 ** attempt, 8))
                continue
            return r
        return r

    def _raw(self, url: str, conditional: bool = False) -> Response:
        cached = self.cache.get_cached(url) if conditional and self.cache else None
        for _ in range(self.max_redirects + 1):
            check_url(url, self.allow_private)
            headers = {"User-Agent": self.user_agent, "Accept-Language": "ne,en;q=0.8"}
            if cached:
                if cached[0]:
                    headers["If-None-Match"] = cached[0]
                if cached[1]:
                    headers["If-Modified-Since"] = cached[1]
            r = self._once(url, headers)
            if len(r.body) > MAX_BYTES:
                raise FetchError("too_large", url)
            if r.status in (301, 302, 303, 307, 308) and r.headers.get("location"):
                url = urljoin(url, r.headers["location"])  # checked at the top of the loop before any request
                cached = None
                continue
            if r.status == 304 and cached:
                return Response(url, 200, {"content-type": cached[3], "x-spashta-cache": "hit"}, cached[2])
            if r.status == 200 and conditional and self.cache:
                self.cache.put_cached(url, r.headers.get("etag"), r.headers.get("last-modified"), r.body, r.content_type)
            return Response(url, r.status, r.headers, r.body)
        raise FetchError("http", url, "too many redirects")

    def robots(self, url: str) -> Optional[urllib.robotparser.RobotFileParser]:
        p = urlparse(url)
        root = f"{p.scheme}://{p.netloc}"
        if root not in self._robots:
            parser = None
            try:
                r = self._raw(urljoin(root, "/robots.txt"))
                if r.status == 200:
                    parser = urllib.robotparser.RobotFileParser()
                    parser.parse(r.text.splitlines())
            except FetchError as e:
                if e.kind == "blocked":
                    raise
            self._robots[root] = parser
        return self._robots[root]

    def sitemaps_from_robots(self, url: str) -> list[str]:
        rp = self.robots(url)
        return list(rp.site_maps() or []) if rp else []

    def get(self, url: str, conditional: bool = False) -> Response:
        """conditional=True sends the cached ETag / Last-Modified and serves
        the cached body on 304; used for feeds and sitemaps, which are polled
        repeatedly and rarely change."""
        if self.respect_robots:
            rp = self.robots(url)
            if rp is not None and not rp.can_fetch(self.user_agent, url):
                raise FetchError("robots", url, "disallowed by robots.txt")
        r = self._raw(url, conditional)
        if r.status >= 400:
            raise FetchError("http", url, status=r.status)
        return r
