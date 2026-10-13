"""
HTTP API and a minimal bilingual web page.

    pip install -e ".[api]"
    uvicorn spashta.api:app --port 8000

Endpoints
  GET  /                      paste a link or text, get a report (no JS framework)
  GET  /api/health            counts and versions
  GET  /r/<id>                a stored report as a page (bilingual, no script, works on phones)
  GET  /api/reports/<id>      a stored report as JSON
  POST /api/analyze           {"url"} or {"title", "body"}; "llm": true for the claim review; "share" stores the
                              report under a permanent link (default: yes for a URL, no for pasted text)
  GET  /api/search?q=&k=      hybrid search
  GET  /api/sources           sources with status
  POST /api/sources           {"url", "name"?, "lang"?}           (admin)
  POST /api/ingest            {"limit"?}                          (admin)

Configuration (environment)
  SPASHTA_DB, SPASHTA_EMBEDDER   as for the CLI
  SPASHTA_ADMIN_TOKEN            required for admin endpoints (Bearer); unset = admin disabled
  SPASHTA_RATE_PER_MIN           analyze/search requests per client per minute (default 20)
  SPASHTA_GLOBAL_RATE_PER_MIN    analyze/search requests per minute from everyone together (default 120)
  SPASHTA_PUBLIC_LLM             "1" lets anyone request the LLM claim review; default off, so only
                                 requests with the admin token can spend the model key's quota
  SPASHTA_LLM_DAILY_LIMIT        LLM reviews per UTC day across all callers (default 50; cached reviews are free)
  SPASHTA_PUBLIC_URL             canonical https://host for permanent links (default: the request's host)
  SPASHTA_TRUST_PROXY            "1" when behind a host's proxy (Hugging Face, Render, Fly, Caddy): rate limit by
                                 the client address in X-Forwarded-For instead of the proxy's
  SPASHTA_TRUSTED_HOPS           how many proxies append to X-Forwarded-For (default 1): the client address is
                                 taken that many entries from the right, so a forged header can't dodge the limit
  SPASHTA_REQUEST_TIMEOUT        seconds an analysis may take before the request gets 504 (default 30)
  SPASHTA_MAX_CONCURRENT         analyses running at once; more get 503 (default 4)
  SPASHTA_MAX_BODY_BYTES         largest request body accepted (default 262144)

Model keys (GEMINI_API_KEY, GROQ_API_KEY, ...) are read from the environment
by anchor.llm and never appear in responses, errors or logs.

Fetching user-supplied URLs always refuses private and local addresses,
every redirect hop is checked, connections are pinned to the checked
address, and one fetch has a total time limit (see fetch.py).

Privacy: reader IP addresses are never written to logs or disk. The log
line for a request carries a keyed hash of the address that changes
every UTC day (the key lives only in memory), so a day's requests from
one client can be told apart without the address being recoverable. Run
uvicorn with --no-access-log (the Dockerfile does) so it doesn't log
addresses itself. Rate-limit state is in memory, keyed the same way, and
idle entries are dropped.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import logging
import os
import re
import secrets
import threading
import time
from collections import defaultdict, deque
from typing import Optional

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field

from . import __version__, web
from . import lang as L
from .embed import get_embedder
from .fetch import Fetcher, FetchError
from .ingest import fetch_article, ingest_sources
from .models import Article, Source, host_of
from .report import DISCLAIMER_NE, analyze
from .retrieve import Index
from .store import Store

MAX_TEXT = 50_000
REPORT_ID = re.compile(r"[A-Za-z0-9_-]{6,24}")
MAX_ARTICLE_CHARS = 60_000  # a fetched page's text is cut here before analysis
SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Content-Security-Policy": ("default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; "
                                "img-src 'self' data:; connect-src 'self'; base-uri 'none'; form-action 'self'"),
}
log = logging.getLogger("spashta.api")


class DailyKey:
    """Keyed hash of a client address with a key that is regenerated every
    UTC day and never stored: same client, same tag, for one day only."""

    def __init__(self):
        self.day, self.key = "", b""
        self.lock = threading.Lock()

    def __call__(self, address: str) -> str:
        today = time.strftime("%Y-%m-%d", time.gmtime())
        with self.lock:
            if today != self.day:
                self.day, self.key = today, secrets.token_bytes(32)
            key = self.key
        return hmac.new(key, address.encode(), hashlib.sha256).hexdigest()[:12]


class AnalyzeRequest(BaseModel):
    url: Optional[str] = Field(default=None, max_length=2048)
    title: Optional[str] = Field(default=None, max_length=500)
    body: Optional[str] = Field(default=None, max_length=MAX_TEXT)
    llm: bool = False
    share: Optional[bool] = None  # store the report under a permanent link; default: yes for a URL, no for pasted text


class SourceRequest(BaseModel):
    url: str = Field(max_length=2048)
    name: str = Field(default="", max_length=200)
    lang: Optional[str] = Field(default=None, pattern="^(ne|en)$")


class IngestRequest(BaseModel):
    limit: int = Field(default=20, ge=1, le=200)


class RateLimiter:
    def __init__(self, per_minute: int):
        self.per_minute = per_minute
        self.hits: dict[str, deque] = defaultdict(deque)
        self.lock = threading.Lock()

    def check(self, key: str) -> None:
        now = time.monotonic()
        with self.lock:
            if len(self.hits) > 10_000:  # drop idle clients so the table can't grow without bound
                for k in [k for k, q in self.hits.items() if not q or now - q[-1] > 60]:
                    del self.hits[k]
            q = self.hits[key]
            while q and now - q[0] > 60:
                q.popleft()
            if len(q) >= self.per_minute:
                raise HTTPException(429, "Too many requests; try again in a minute.")
            q.append(now)


class DailyBudget:
    """At most `limit` uses per UTC day, shared by every caller."""

    def __init__(self, limit: int):
        self.limit, self.day, self.used = limit, "", 0
        self.lock = threading.Lock()

    def take(self) -> bool:
        today = time.strftime("%Y-%m-%d", time.gmtime())
        with self.lock:
            if today != self.day:
                self.day, self.used = today, 0
            if self.used >= self.limit:
                return False
            self.used += 1
            return True


def _flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes", "on")


def create_app(index: Optional[Index] = None, fetcher: Optional[Fetcher] = None,
               admin_token: Optional[str] = None, rate_per_min: Optional[int] = None,
               public_llm: Optional[bool] = None, llm_daily_limit: Optional[int] = None) -> FastAPI:
    app = FastAPI(title="Spashta", version=__version__, description="News integrity reports for Nepali and English.")
    state = {"index": index, "lock": threading.Lock()}
    fetcher = fetcher or Fetcher(allow_private=False, deadline_s=20.0)
    request_timeout = float(os.environ.get("SPASHTA_REQUEST_TIMEOUT", "30"))
    max_body = int(os.environ.get("SPASHTA_MAX_BODY_BYTES", str(256 * 1024)))
    slots = threading.BoundedSemaphore(int(os.environ.get("SPASHTA_MAX_CONCURRENT", "4")))
    tag = DailyKey()
    token = admin_token if admin_token is not None else os.environ.get("SPASHTA_ADMIN_TOKEN", "")
    limiter = RateLimiter(rate_per_min or int(os.environ.get("SPASHTA_RATE_PER_MIN", "20")))
    global_limiter = RateLimiter(int(os.environ.get("SPASHTA_GLOBAL_RATE_PER_MIN", "120")))
    public_llm = _flag("SPASHTA_PUBLIC_LLM") if public_llm is None else public_llm
    llm_budget = DailyBudget(llm_daily_limit if llm_daily_limit is not None
                             else int(os.environ.get("SPASHTA_LLM_DAILY_LIMIT", "50")))
    trust_proxy = _flag("SPASHTA_TRUST_PROXY")
    trusted_hops = max(1, int(os.environ.get("SPASHTA_TRUSTED_HOPS", "1")))

    def get_index() -> Index:
        with state["lock"]:
            if state["index"] is None:
                state["index"] = Index(Store(os.environ.get("SPASHTA_DB", "spashta.db")),
                                       get_embedder(os.environ.get("SPASHTA_EMBEDDER", "hashing")))
            return state["index"]

    def client_address(request: Request) -> str:
        if trust_proxy:
            forwarded = [p.strip() for p in request.headers.get("x-forwarded-for", "").split(",") if p.strip()]
            if forwarded:
                # The entry the nearest trusted proxy added; earlier entries can be forged by the client.
                return forwarded[-trusted_hops] if len(forwarded) >= trusted_hops else forwarded[0]
        return request.client.host if request.client else "unknown"

    def client_key(request: Request) -> str:
        return tag(client_address(request))

    @app.middleware("http")
    async def guard(request: Request, call_next):
        """Body size cap, security headers, and one log line per request
        with no address in it."""
        start = time.monotonic()
        length = request.headers.get("content-length")
        if request.method in ("POST", "PUT", "PATCH") and length is None:
            return JSONResponse({"detail": "Content-Length required."}, status_code=411)
        if length is not None and (not length.isdigit() or int(length) > max_body):
            return JSONResponse({"detail": "Request body too large."}, status_code=413)
        try:
            response = await call_next(request)
        except Exception as e:  # noqa: BLE001 - log the kind, never the request content
            log.error("%s %s 500 %s client=%s", request.method, request.url.path, type(e).__name__, client_key(request))
            response = JSONResponse({"detail": "Internal error."}, status_code=500)
        for k, v in SECURITY_HEADERS.items():
            response.headers.setdefault(k, v)
        log.info("%s %s %d %dms client=%s", request.method, request.url.path, response.status_code,
                 (time.monotonic() - start) * 1000, client_key(request))
        return response

    async def bounded(fn, *args):
        """Run fn in a worker thread with a time limit and a cap on how many
        run at once. A timed-out analysis finishes in the background but
        keeps its slot until it does, so runaway work can't pile up."""
        if not slots.acquire(blocking=False):
            raise HTTPException(503, "Busy; try again in a moment.")

        def run():
            try:
                return fn(*args)
            finally:
                slots.release()
        try:
            return await asyncio.wait_for(run_in_threadpool(run), request_timeout)
        except asyncio.TimeoutError:
            raise HTTPException(504, "The analysis took too long.") from None

    def rate(request: Request) -> None:
        global_limiter.check("*")
        limiter.check(client_key(request))

    def is_admin(authorization: str) -> bool:
        return bool(token) and hmac.compare_digest(authorization.removeprefix("Bearer ").strip(), token)

    def admin(authorization: str = Header(default="")) -> None:
        if not token:
            raise HTTPException(403, "Admin endpoints are disabled (set SPASHTA_ADMIN_TOKEN).")
        if not is_admin(authorization):
            raise HTTPException(401, "Invalid admin token.")

    @app.get("/api/health")
    def health(index: Index = Depends(get_index)) -> dict:
        return {"version": __version__, "embedder": index.embedder.name, **index.store.count()}

    @app.post("/api/analyze", dependencies=[Depends(rate)])
    async def analyze_endpoint(req: AnalyzeRequest, index: Index = Depends(get_index),
                               authorization: str = Header(default="")) -> dict:
        if req.llm:
            if not (public_llm or is_admin(authorization)):
                raise HTTPException(403, "The LLM review is not enabled for public requests on this server.")
            if not llm_budget.take():
                raise HTTPException(429, "Today's LLM review budget is used up; try again tomorrow.")
        if not req.url and not req.body:
            raise HTTPException(422, "Send a url, or a title and body.")
        return await bounded(_analyze, req, index)

    def _analyze(req: AnalyzeRequest, index: Index) -> dict:
        if req.url:
            try:
                article = fetch_article(req.url, fetcher)
            except FetchError as e:
                status = 400 if e.kind in ("blocked", "robots") else 504 if e.kind == "timeout" else 502
                raise HTTPException(status, f"Could not fetch the page ({e.kind}).") from None
            article = article.model_copy(update={"title": article.title[:500], "body": article.body[:MAX_ARTICLE_CHARS]})
        else:
            title = (req.title or "").strip() or req.body.strip().split("\n", 1)[0][:200]
            text = f"{title}\n{req.body}"
            article = Article(url="text:", title=L.clean(title), body=L.clean(req.body), lang=L.detect_language(text))
        report = analyze(article, index, use_llm=req.llm).to_dict()
        report.update(version=__version__, disclaimer_ne=DISCLAIMER_NE)
        share = req.share if req.share is not None else bool(req.url)
        if share:
            report_id = secrets.token_urlsafe(8)
            index.store.put_report(report_id, article.url, report)
            report.update(id=report_id, permalink=f"/r/{report_id}")
        return report

    @app.get("/api/search", dependencies=[Depends(rate)])
    def search(q: str, k: int = 10, index: Index = Depends(get_index)) -> list[dict]:
        k = max(1, min(k, 50))
        out = []
        for h in index.search(q[:500], k=k):
            a = index.store.article(h.chunk.article_id)
            out.append({"score": round(h.score, 5), "title": a.title if a else "", "url": a.url if a else "",
                        "source": a.source if a else "", "passage": h.chunk.text, "matched_terms": h.matched_terms})
        return out

    @app.get("/api/sources")
    def sources(index: Index = Depends(get_index)) -> list[dict]:
        return index.store.source_status()

    @app.post("/api/sources", dependencies=[Depends(admin)])
    def add_source(req: SourceRequest, index: Index = Depends(get_index)) -> dict:
        index.store.add_source(Source(url=req.url, name=req.name or host_of(req.url), lang=req.lang))
        return {"added": req.url}

    @app.post("/api/ingest", dependencies=[Depends(admin)])
    def ingest(req: IngestRequest, index: Index = Depends(get_index)) -> list[dict]:
        sources = index.store.sources(enabled_only=True)
        results = ingest_sources(sources, Fetcher(cache=index.store), index, limit=req.limit)
        return [{"source": r.source, "kind": r.kind, "found": r.found, "added": r.added, "skipped": r.skipped,
                 "errors": r.errors[:3]} for r in results]

    @app.get("/", response_class=HTMLResponse)
    def home() -> str:
        return web.home_page()

    def stored(report_id: str, index: Index) -> dict:
        r = index.store.get_report(report_id) if REPORT_ID.fullmatch(report_id) else None
        if r is None:
            raise HTTPException(404, "No such report.")
        return r

    @app.get("/api/reports/{report_id}", dependencies=[Depends(rate)])
    def report_json(report_id: str, index: Index = Depends(get_index)) -> dict:
        return stored(report_id, index)

    @app.get("/r/{report_id}", response_class=HTMLResponse, dependencies=[Depends(rate)])
    def report_html(report_id: str, request: Request, index: Index = Depends(get_index)) -> str:
        r = stored(report_id, index)
        base = os.environ.get("SPASHTA_PUBLIC_URL", "").rstrip("/") or str(request.base_url).rstrip("/")
        return web.report_page(r, f"{base}/r/{report_id}")

    return app


def _configure_logging() -> None:
    """One line per request on stderr (the container log), unless the host
    already configured logging."""
    level = os.environ.get("SPASHTA_LOG_LEVEL", "INFO").upper()
    if not log.handlers and level != "OFF":
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        log.addHandler(handler)
        log.setLevel(level)


_configure_logging()
app = create_app()
