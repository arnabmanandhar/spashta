"""
HTTP API and a minimal bilingual web page.

    pip install -e ".[api]"
    uvicorn spashta.api:app --port 8000

Endpoints
  GET  /                      paste a link or text, get a report (no JS framework)
  GET  /api/health            counts and versions
  POST /api/analyze           {"url"} or {"title", "body"}; "llm": true for the claim review
  GET  /api/search?q=&k=      hybrid search
  GET  /api/sources           sources with status
  POST /api/sources           {"url", "name"?, "lang"?}           (admin)
  POST /api/ingest            {"limit"?}                          (admin)

Configuration (environment)
  SPASHTA_DB, SPASHTA_EMBEDDER   as for the CLI
  SPASHTA_ADMIN_TOKEN            required for admin endpoints (Bearer); unset = admin disabled
  SPASHTA_RATE_PER_MIN           analyze/search requests per client per minute (default 20)

Fetching user-supplied URLs always refuses private and local addresses,
and every redirect hop is checked (see fetch.py).
"""

from __future__ import annotations

import hmac
import os
import threading
import time
from collections import defaultdict, deque
from typing import Optional

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from . import __version__
from . import lang as L
from .embed import get_embedder
from .fetch import Fetcher, FetchError
from .ingest import fetch_article, ingest_sources
from .models import Article, Source, host_of
from .report import analyze
from .retrieve import Index
from .store import Store

MAX_TEXT = 50_000


class AnalyzeRequest(BaseModel):
    url: Optional[str] = Field(default=None, max_length=2048)
    title: Optional[str] = Field(default=None, max_length=500)
    body: Optional[str] = Field(default=None, max_length=MAX_TEXT)
    llm: bool = False


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
            q = self.hits[key]
            while q and now - q[0] > 60:
                q.popleft()
            if len(q) >= self.per_minute:
                raise HTTPException(429, "Too many requests; try again in a minute.")
            q.append(now)


def create_app(index: Optional[Index] = None, fetcher: Optional[Fetcher] = None,
               admin_token: Optional[str] = None, rate_per_min: Optional[int] = None) -> FastAPI:
    app = FastAPI(title="Spashta", version=__version__, description="News integrity reports for Nepali and English.")
    state = {"index": index, "lock": threading.Lock()}
    fetcher = fetcher or Fetcher(allow_private=False)
    token = admin_token if admin_token is not None else os.environ.get("SPASHTA_ADMIN_TOKEN", "")
    limiter = RateLimiter(rate_per_min or int(os.environ.get("SPASHTA_RATE_PER_MIN", "20")))

    def get_index() -> Index:
        with state["lock"]:
            if state["index"] is None:
                state["index"] = Index(Store(os.environ.get("SPASHTA_DB", "spashta.db")),
                                       get_embedder(os.environ.get("SPASHTA_EMBEDDER", "hashing")))
            return state["index"]

    def rate(request: Request) -> None:
        limiter.check(request.client.host if request.client else "unknown")

    def admin(authorization: str = Header(default="")) -> None:
        if not token:
            raise HTTPException(403, "Admin endpoints are disabled (set SPASHTA_ADMIN_TOKEN).")
        if not hmac.compare_digest(authorization.removeprefix("Bearer ").strip(), token):
            raise HTTPException(401, "Invalid admin token.")

    @app.get("/api/health")
    def health(index: Index = Depends(get_index)) -> dict:
        return {"version": __version__, "embedder": index.embedder.name, **index.store.count()}

    @app.post("/api/analyze", dependencies=[Depends(rate)])
    def analyze_endpoint(req: AnalyzeRequest, index: Index = Depends(get_index)) -> dict:
        if req.url:
            try:
                article = fetch_article(req.url, fetcher)
            except FetchError as e:
                status = 400 if e.kind in ("blocked", "robots") else 502
                raise HTTPException(status, f"Could not fetch the page ({e.kind}).") from None
        elif req.body:
            title = (req.title or "").strip() or req.body.strip().split("\n", 1)[0][:200]
            text = f"{title}\n{req.body}"
            article = Article(url="text:", title=L.normalize(title), body=L.normalize(req.body), lang=L.detect_language(text))
        else:
            raise HTTPException(422, "Send a url, or a title and body.")
        return analyze(article, index, use_llm=req.llm).to_dict()

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
        return PAGE

    return app


PAGE = """<!doctype html>
<html lang="ne"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Spashta · स्पष्ट</title>
<style>
:root{--bg:#fbfaf7;--fg:#1d1d1b;--muted:#6b6a65;--line:#e4e1d8;--card:#fff;--good:#2f7d4f;--mid:#b07d1a;--bad:#b23b2e}
@media (prefers-color-scheme:dark){:root{--bg:#161614;--fg:#ecebe6;--muted:#a3a19a;--line:#33322e;--card:#1f1f1c}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:16px/1.55 system-ui,"Noto Sans Devanagari",sans-serif}
main{max-width:760px;margin:0 auto;padding:24px 16px 64px}h1{margin:0 0 4px;font-size:28px}p.sub{margin:0 0 20px;color:var(--muted)}
textarea,input{width:100%;padding:10px 12px;border:1px solid var(--line);border-radius:8px;background:var(--card);color:var(--fg);font:inherit}
textarea{min-height:140px}button{margin-top:10px;padding:10px 18px;border:0;border-radius:8px;background:var(--fg);color:var(--bg);font:inherit;cursor:pointer}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:16px;margin-top:16px}
.score{font-size:40px;font-weight:700}.sig h3{margin:0 0 4px;font-size:16px}.sig{border-top:1px solid var(--line);padding-top:12px;margin-top:12px}
.bar{height:6px;border-radius:3px;background:var(--line);overflow:hidden;margin:6px 0}.bar span{display:block;height:100%}
ul{margin:6px 0 0 18px;padding:0;color:var(--muted)}small{color:var(--muted)}a{color:inherit}
</style></head><body><main>
<h1>स्पष्ट · Spashta</h1>
<p class="sub">समाचार कसरी लेखिएको छ, जाँच्नुहोस्। Check how a news story is told.</p>
<input id="url" placeholder="समाचारको लिङ्क / article link">
<p class="sub" style="margin:8px 0">वा / or</p>
<textarea id="text" placeholder="शीर्षक पहिलो लाइनमा, त्यसपछि समाचार / Headline on the first line, then the story"></textarea>
<button id="go">जाँच्नुहोस् · Check</button>
<div id="out"></div>
<p><small>Spashta measures presentation, sourcing and corroboration. It does not decide whether a story is true.</small></p>
</main><script>
const esc=s=>String(s).replace(/[&<>"]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
const color=c=>c<0.25?"var(--good)":c<0.6?"var(--mid)":"var(--bad)";
document.getElementById("go").onclick=async()=>{
  const url=document.getElementById("url").value.trim(), text=document.getElementById("text").value.trim();
  const out=document.getElementById("out"); out.innerHTML='<div class="card">…</div>';
  const [title,...rest]=text.split("\\n");
  const body=url?{url}:{title,body:rest.join("\\n")};
  try{
    const r=await fetch("/api/analyze",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)});
    const d=await r.json(); if(!r.ok) throw new Error(d.detail||r.status);
    let h=`<div class="card"><div class="score">${d.score}/100</div><div>${esc(d.label)}</div><small>${esc(d.title)}</small>`;
    for(const s of d.signals){h+=`<div class="sig"><h3>${esc(s.name)}</h3><div class="bar"><span style="width:${Math.round(s.concern*100)}%;background:${color(s.concern)}"></span></div>${esc(s.summary)}<ul>${s.evidence.map(e=>`<li>${esc(e)}</li>`).join("")}</ul></div>`}
    if(d.related.length){h+=`<div class="sig"><h3>Other coverage</h3><ul>${d.related.map(x=>`<li><a href="${/^https?:\\/\\//.test(x.url)?esc(x.url):"#"}" rel="noopener nofollow">${esc(x.title)}</a> · ${esc(x.source)} · ${esc(x.role)}</li>`).join("")}</ul></div>`}
    out.innerHTML=h+`<p><small>${esc(d.disclaimer)}</small></p></div>`;
  }catch(e){out.innerHTML=`<div class="card">${esc(e.message)}</div>`}
};
</script></body></html>
"""

app = create_app()
