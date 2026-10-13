"""
The redistributable dataset, and getting the text back.

News articles are the outlets' copyright, so the public release carries
no article text. It has, per article: URL, outlet, language, dates,
SHA-256 hashes of title and body, and character offsets of labelled spans
(each with a short hash of the span text). Queries, relevance judgments
and labels are ours to publish.

rehydrate() re-fetches every URL with the same extractor, checks the
hashes, and writes the full text locally. When a page has changed (an
edited story, a new site template), the body hash won't match; each
labelled span is then searched for by its own hash, so labels survive
small edits, and spans that can't be found are reported, never guessed.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path
from typing import Optional

from . import snapshot

PUBLIC_ARTICLE_FIELDS = ("id", "url", "source", "outlet_group", "lang", "published", "title_sha256", "body_sha256",
                         "sha256", "chars")
SPAN_HASH_CHARS = 12


def span_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:SPAN_HASH_CHARS]


def with_span_hashes(label: dict, body: str) -> dict:
    """Add the hash of each span's text, so the span can be re-anchored
    after the article text is re-fetched."""
    out = dict(label)
    for key in ("claims", "loaded"):
        out[key] = [{**s, "sha": span_hash(body[s["start"]:s["end"]])} for s in label.get(key, [])]
    return out


def strip_text(label: dict) -> dict:
    """Labels without any article text: spans keep offsets and hashes only."""
    out = {k: v for k, v in label.items() if k not in ("title", "body", "text")}
    for key in ("claims", "loaded"):
        if key in out:
            out[key] = [{k: v for k, v in s.items() if k != "text"} for s in out[key]]
    return out


def _read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def _write_jsonl(path: Path, rows) -> int:
    n = 0
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
            n += 1
    return n


def build_release(snapshot_dir: str | Path, out_dir: str | Path, labels: Optional[str | Path] = None,
                  pairs: Optional[str | Path] = None, queries: Optional[str | Path] = None,
                  sample: Optional[str | Path] = None, card: Optional[str | Path] = None) -> dict:
    """Write the public release: articles.jsonl (no text), labels.jsonl,
    pairs.jsonl, queries.jsonl, manifest.json, and the dataset card as
    README.md when given. Refuses to write if any output still holds text."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    records = snapshot.read_records(snapshot_dir)
    by_id = {r["id"]: r for r in records}
    topics = {r["id"]: r.get("topic") for r in _read_jsonl(Path(sample))} if sample else {}
    counts = {"articles": _write_jsonl(out / "articles.jsonl", (
        {**{k: r.get(k) for k in PUBLIC_ARTICLE_FIELDS}, **({"topic": topics[r["id"]]} if r["id"] in topics else {})}
        for r in records))}
    if labels:
        rows = []
        for lab in _read_jsonl(Path(labels)):
            body = by_id[lab["article_id"]]["body"] if lab["article_id"] in by_id else ""
            rows.append(strip_text(with_span_hashes(lab, body)))
        counts["labels"] = _write_jsonl(out / "labels.jsonl", rows)
    if pairs:
        counts["pairs"] = _write_jsonl(out / "pairs.jsonl", (strip_text(p) for p in _read_jsonl(Path(pairs))))
    if queries:
        src = Path(queries)
        data = json.loads(src.read_text(encoding="utf-8"))
        qs = data["questions"] if isinstance(data, dict) else data
        counts["queries"] = _write_jsonl(out / "queries.jsonl", (
            {"query": q["query"], "lang": q.get("lang"), "tags": q.get("tags", []),
             "relevant_ids": q.get("relevant_ids") or [snapshot_id_of(u) for u in q.get("relevant_urls", [])],
             **({"judgments": q["judgments"]} if "judgments" in q else {})} for q in qs))
    m = snapshot.read_manifest(snapshot_dir)
    manifest = {"snapshot_id": m["snapshot_id"], "window": m["window"], "by_lang": m["by_lang"], "by_outlet": m["by_outlet"],
                "by_outlet_group": m["by_outlet_group"], "software": m["software"], "files": counts,
                "text_included": False,
                "rehydrate": "pip install spashta && spashta dataset rehydrate <this folder> <output folder>"}
    (out / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if card:
        shutil.copyfile(card, out / "README.md")
    leaks = check_no_text(out)
    if leaks:
        raise ValueError("release would include article text: " + "; ".join(leaks[:5]))
    return manifest


def snapshot_id_of(url: str) -> str:
    from ..models import article_id
    return article_id(url)


TEXT_KEYS = ("title", "body", "text")
MAX_FREE_TEXT = 300  # a note may quote a few words; anything longer is treated as a leak


def check_no_text(release_dir: str | Path) -> list[str]:
    """A last check before publishing: no released record has a text field,
    and no string in it is long enough to carry an article's text."""
    leaks = []

    def walk(obj, where: str) -> None:
        if isinstance(obj, dict):
            for k, v in obj.items():
                if k in TEXT_KEYS and v:
                    leaks.append(f"{where}: field '{k}'")
                walk(v, where)
        elif isinstance(obj, list):
            for v in obj:
                walk(v, where)
        elif isinstance(obj, str) and len(obj) > MAX_FREE_TEXT:
            leaks.append(f"{where}: {len(obj)}-character string")

    for path in sorted(Path(release_dir).glob("*.jsonl")):
        for i, row in enumerate(_read_jsonl(path), 1):
            walk(row, f"{path.name}:{i}")
    return leaks


def reanchor(span: dict, body: str, window: int = 3000) -> Optional[tuple[int, int]]:
    """Where a span with this text hash now sits in body: its old offsets
    if they still match, else the nearest position whose text has the same
    hash. None when the text is gone."""
    start, end, sha = span["start"], span["end"], span.get("sha")
    if sha is None:
        return None
    length = end - start
    if body[start:end] and span_hash(body[start:end]) == sha:
        return start, end
    candidates = sorted(range(0, max(0, len(body) - length) + 1), key=lambda i: abs(i - start))
    for i in candidates[:2 * window] + candidates[2 * window:]:
        if span_hash(body[i:i + length]) == sha:
            return i, i + length
    return None


def rehydrate(release_dir: str | Path, out_dir: str | Path, fetch=None, limit: Optional[int] = None) -> dict:
    """Re-fetch every article in a release and rebuild local full-text
    files: articles.full.jsonl (snapshot-shaped records) and
    labels.full.jsonl (labels with re-anchored offsets). fetch(url) ->
    Article defaults to Spashta's own fetcher and extractor."""
    if fetch is None:
        from ..fetch import Fetcher
        from ..ingest import fetch_article
        fetcher = Fetcher()
        fetch = lambda url: fetch_article(url, fetcher)  # noqa: E731
    rel, out = Path(release_dir), Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    articles = _read_jsonl(rel / "articles.jsonl")[:limit]
    status = {"exact": 0, "changed": 0, "failed": 0}
    full: dict[str, dict] = {}
    for a in articles:
        try:
            got = fetch(a["url"])
        except Exception as e:  # noqa: BLE001 - one dead link must not stop the rest
            status["failed"] += 1
            full[a["id"]] = {**a, "error": str(e)[:200]}
            continue
        rec = snapshot.record(got)
        rec.update({k: a[k] for k in ("id", "source", "outlet_group", "lang", "published") if a.get(k) is not None})
        rec["matches_release"] = rec["body_sha256"] == a["body_sha256"]
        status["exact" if rec["matches_release"] else "changed"] += 1
        full[a["id"]] = rec
    _write_jsonl(out / "articles.full.jsonl", full.values())
    spans = {"kept": 0, "moved": 0, "lost": 0}
    labels_out = []
    for lab in _read_jsonl(rel / "labels.jsonl"):
        body = full.get(lab["article_id"], {}).get("body")
        if body is None:
            continue
        lab = dict(lab)
        for key in ("claims", "loaded"):
            kept = []
            for s in lab.get(key, []):
                where = reanchor(s, body)
                if where is None:
                    spans["lost"] += 1
                    continue
                spans["kept" if where == (s["start"], s["end"]) else "moved"] += 1
                kept.append({**s, "start": where[0], "end": where[1], "text": body[where[0]:where[1]]})
            lab[key] = kept
        labels_out.append(lab)
    if labels_out:
        _write_jsonl(out / "labels.full.jsonl", labels_out)
    report = {"articles": status, "spans": spans}
    (out / "rehydrate_report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return report
