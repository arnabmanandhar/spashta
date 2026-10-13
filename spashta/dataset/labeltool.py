"""
A self-contained labelling page.

build() writes one HTML file with the items embedded: open it in any
browser, no server or install needed, so a second annotator only needs
the file. Labels are kept in the browser as you go (resume any time) and
exported as JSONL in labels.py's format. Offsets are computed in code
points, as Python indexes the snapshot text.

The page holds full article text for labelling, so share it only with
your annotators, not publicly.
"""

from __future__ import annotations

import json
from importlib import resources
from pathlib import Path
from typing import Optional

from .. import __version__
from .topics import TOPICS


def _template() -> str:
    return (resources.files("spashta") / "data" / "labeltool.html").read_text(encoding="utf-8")


def render(mode: str, items: list[dict], batch: str) -> str:
    if mode not in ("article", "pair", "qrel"):
        raise ValueError("mode must be article, pair or qrel")
    data = {"mode": mode, "batch": batch, "items": items, "topics": list(TOPICS), "version": f"spashta {__version__}"}
    # Inside <script type="application/json"> only "</" can end the element early.
    blob = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    return _template().replace("/*__DATA__*/", blob)


def article_items(sample: list[dict], records: dict[str, dict], batch: Optional[str] = None,
                  double_only: bool = False) -> list[dict]:
    out = []
    for s in sample:
        if (batch and s.get("batch") != batch) or (double_only and not s.get("double")):
            continue
        r = records[s["id"]]
        out.append({"id": r["id"], "url": r["url"], "source": r["source"], "lang": r["lang"], "topic": s.get("topic"),
                    "title": r["title"], "body": r["body"]})
    return out


def _side(r: dict) -> dict:
    return {"id": r["id"], "url": r["url"], "source": r["source"], "lang": r["lang"], "published": r.get("published"),
            "title": r["title"], "body": r["body"]}


def pair_items(pairs: list[dict], records: dict[str, dict]) -> list[dict]:
    return [{"pair_id": p["pair_id"], "a": _side(records[p["a"]]), "b": _side(records[p["b"]])}
            for p in pairs if p["a"] in records and p["b"] in records]


def qrel_items(pool: list[dict], records: dict[str, dict], max_chars: int = 3000) -> list[dict]:
    out = []
    for p in pool:
        r = records.get(p["article_id"])
        if r is None:
            continue
        out.append({"qid": p["qid"], "query": p["query"], "query_lang": p.get("lang"), "article_id": r["id"],
                    "source": r["source"], "lang": r["lang"], "title": r["title"], "body": r["body"][:max_chars]})
    return out


def build(mode: str, items: list[dict], out: str | Path, batch: str) -> Path:
    path = Path(out)
    path.write_text(render(mode, items, batch), encoding="utf-8")
    return path
