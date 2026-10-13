"""
Label records, validation and gold labels.

One JSON object per line, written by the labelling page (labeltool.py) or
by hand. Offsets count Unicode code points in the article body exactly as
stored in the snapshot (Python string indices), end exclusive.

Article labels (task "article"):
  {"task": "article", "article_id", "annotator",
   "headline": "consistent" | "partly" | "inconsistent",
   "sensationalism": 0 | 1 | 2,
   "claims": [{"start", "end", "sourcing": "named" | "anonymous" | "unattributed"}],
   "loaded": [{"start", "end"}],
   "topic": one of topics.TOPICS, "opinion": bool,
   "skip": null or a reason ("not news", "text broken", ...), "notes": ""}

Pair labels (task "pair"):
  {"task": "pair", "pair_id", "a", "b", "annotator", "same_event": "yes" | "no" | "unsure",
   "conflicting_figures": [{"what", "a", "b"}], "notes": ""}

Relevance judgments (task "qrel"):
  {"task": "qrel", "qid", "article_id", "annotator", "grade": 0 | 1 | 2}

gold() builds the published labels: the primary annotator's labels, with
any field an adjudication record sets taking precedence.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from .topics import TOPICS

HEADLINE = ("consistent", "partly", "inconsistent")
SENSATIONALISM = (0, 1, 2)
SOURCING = ("named", "anonymous", "unattributed")
SAME_EVENT = ("yes", "no", "unsure")


def read(path: str | Path) -> list[dict]:
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def write(path: str | Path, rows) -> None:
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def key(r: dict) -> str:
    task = r.get("task", "article")
    if task == "pair":
        return "pair:" + r["pair_id"]
    if task == "qrel":
        return f"qrel:{r['qid']}:{r['article_id']}"
    return "article:" + r["article_id"]


def by_key(rows: list[dict]) -> dict[str, dict]:
    """Latest record per item (a later line replaces an earlier one)."""
    return {key(r): r for r in rows}


def validate(r: dict, body_chars: Optional[int] = None) -> list[str]:
    task = r.get("task", "article")
    p = []
    if task == "article":
        if r.get("skip"):
            return p
        if r.get("headline") not in HEADLINE:
            p.append(f"headline must be one of {HEADLINE}")
        if r.get("sensationalism") not in SENSATIONALISM:
            p.append("sensationalism must be 0, 1 or 2")
        if r.get("topic") is not None and r["topic"] not in TOPICS:
            p.append(f"topic must be one of {TOPICS}")
        for kind in ("claims", "loaded"):
            for s in r.get(kind, []):
                if not 0 <= s["start"] < s["end"] or (body_chars is not None and s["end"] > body_chars):
                    p.append(f"{kind} span {s['start']}-{s['end']} is outside the body")
                if kind == "claims" and s.get("sourcing") not in SOURCING:
                    p.append(f"claim sourcing must be one of {SOURCING}")
    elif task == "pair":
        if r.get("same_event") not in SAME_EVENT:
            p.append(f"same_event must be one of {SAME_EVENT}")
    elif task == "qrel":
        if r.get("grade") not in (0, 1, 2):
            p.append("grade must be 0, 1 or 2")
    else:
        p.append(f"unknown task {task!r}")
    return p


def gold(primary: list[dict], adjudicated: Optional[list[dict]] = None) -> list[dict]:
    """Primary labels, overridden field by field by adjudication records
    (which need only the item's id fields and the fields being decided)."""
    out = by_key(primary)
    for r in adjudicated or []:
        k = key(r)
        out[k] = {**out.get(k, {}), **r, "annotator": "adjudicated"}
    return [r for r in out.values() if not r.get("skip")]


# ---------------------------------------------- labels -> evaluation targets


def headline_bad(r: dict, strict: bool = False) -> Optional[bool]:
    if r.get("headline") not in HEADLINE:
        return None
    return r["headline"] == "inconsistent" if strict else r["headline"] != "consistent"


def sensational(r: dict, level: int = 1) -> Optional[bool]:
    return None if r.get("sensationalism") not in SENSATIONALISM else r["sensationalism"] >= level


def poorly_sourced(r: dict) -> Optional[bool]:
    """Fewer than half the key claims attributed to a named source. None
    when the annotator marked no claims (nothing to judge)."""
    claims = r.get("claims") or []
    if not claims:
        return None
    return sum(c["sourcing"] == "named" for c in claims) / len(claims) < 0.5


def unattributed_share(r: dict) -> Optional[float]:
    claims = r.get("claims") or []
    return None if not claims else sum(c["sourcing"] != "named" for c in claims) / len(claims)


def loaded(r: dict) -> Optional[bool]:
    return None if "loaded" not in r else bool(r["loaded"])
