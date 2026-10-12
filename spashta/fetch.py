"""
Polite HTTP fetching.

Every request Spashta makes goes through Fetcher, which:

  - identifies itself (SpashtaBot with a contact URL)
  - honours robots.txt for the host
  - waits between requests to the same host
  - refuses non-http(s) URLs, credentials in URLs and, unless allowed,
    private or local addresses (a hosted instance takes URLs from users)
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
from typing import Optional
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


def urllib_transport(url: str, headers: dict[str, str]) -> Response:
    req = urllib.request.Request(url, headers=headers)  # noqa: S310 - scheme checked by Fetcher
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as r:  # noqa: S310 - scheme checked by Fetcher
            body = r.read(MAX_BYTES + 1)
            return Response(r.geturl(), r.status, {k.lower(): v for k, v in r.headers.items()}, body)
    except urllib.error.HTTPError as e:
        return Response(url, e.code, {k.lower(): v for k, v in (e.headers or {}).items()}, b"")
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise FetchError("network", url, str(getattr(e, "reason", e))) from None


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
    _robots: dict[str, Optional[urllib.robotparser.RobotFileParser]] = field(default_factory=dict, repr=False)
    _last: dict[str, float] = field(default_factory=dict, repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def _wait(self, host: str) -> None:
        with self._lock:
            wait = self._last.get(host, 0.0) + self.delay_s - time.monotonic()
            self._last[host] = time.monotonic() + max(wait, 0.0)
        if wait > 0:
            time.sleep(wait)

    def _raw(self, url: str) -> Response:
        check_url(url, self.allow_private)
        self._wait(urlparse(url).netloc)
        r = self.transport(url, {"User-Agent": self.user_agent, "Accept-Language": "ne,en;q=0.8"})
        if len(r.body) > MAX_BYTES:
            raise FetchError("too_large", url)
        if r.url != url:  # a redirect can't take us somewhere we'd refuse to go
            check_url(r.url, self.allow_private)
        return r

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

    def get(self, url: str) -> Response:
        if self.respect_robots:
            rp = self.robots(url)
            if rp is not None and not rp.can_fetch(self.user_agent, url):
                raise FetchError("robots", url, "disallowed by robots.txt")
        r = self._raw(url)
        if r.status >= 400:
            raise FetchError("http", url, status=r.status)
        return r
