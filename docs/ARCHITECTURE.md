# Architecture

How Spashta is put together. Why it is put together this way is in
[DESIGN_RATIONALE.md](DESIGN_RATIONALE.md); the options that were weighed
are in [CASE_STUDIES.md](CASE_STUDIES.md); what changed in each review
round is in [REVIEW_LOG.md](REVIEW_LOG.md).

```
 any URL (news site, blog, feed)            a single article (URL, saved HTML, pasted text)
        │                                                   │
        ▼                                                   │
 ingest.discover.resolve                                    │
   feed? ─► advertised feed? ─► /feed, /rss? ─►             │
   robots.txt sitemap? ─► /sitemap.xml? ─► same-site links  │
        │                                                   │
        ▼                                                   ▼
 ingest.discover.list_articles ─► fetch.Fetcher ─► ingest.extract.extract_article
   (robots.txt, per-host delay, SSRF guard,          (JSON-LD, OpenGraph, trafilatura
    size cap, charset)                                 or built-in fallback)
                                                            │
                                         lang.normalize / detect_language
                                                            │
                                                            ▼
                                  chunk.chunk_article (headline+lead, sentence packs)
                                                            │
                         ┌──────────────────────────────────┴───────────────┐
                         ▼                                                  ▼
                BM25 over lang.content_terms                   embed.* vectors (hashing / e5 / bge-m3)
                         └──────────────── retrieve.Index (RRF) ────────────┘
                                                │                    store.Store (SQLite)
                                                ▼
     analysis.signals (headline, sensationalism, sourcing, framing)   deterministic
     analysis.corroboration (related coverage, figures, tone)          retrieval-grounded
     analysis.llm_review (claims audited against passages)             optional, any provider via Anchor
                                                │
                                                ▼
                                   report.IntegrityReport (score, label, evidence)
                                                │
                                     cli · evaluate (Anchor metrics)
```

## Modules

| Module | Responsibility |
|---|---|
| `lang.py` | NFC normalisation, digit folding, joiner removal; script-based language detection (ne/en/hi/mixed); tokenizer that keeps Devanagari words whole; Nepali case-marker and plural stripping; light English stemmer; number extraction that matches १,२०० with 1200; sentence split on danda |
| `models.py` | Pydantic `Source`, `Article`, `Chunk`, `FeedItem`; canonical URLs (no tracking parameters) and stable article ids |
| `fetch.py` | `Fetcher`: robots.txt, per-host delay, SSRF guard on every redirect hop, retries with Retry-After, conditional GET, 5 MB cap, charset detection; pluggable transport for tests |
| `bsdate.py` | Bikram Sambat date detection and conversion (optional `nepali-datetime`) |
| `ingest/feeds.py` | RSS 2.0, RSS 1.0, Atom and JSON Feed parsing |
| `ingest/sitemap.py` | Sitemaps and sitemap indexes, Google News sitemap fields |
| `ingest/extract.py` | Page parsing (metadata, feeds, links, paragraphs) and article extraction |
| `ingest/discover.py` | Resolve any URL to feed, sitemap or link-crawl; list article URLs |
| `ingest/__init__.py` | `collect_source` (thread-safe fetch), `ingest_sources` (parallel across hosts, one index pass) |
| `ingest/boilerplate.py` | Per-site repeated-paragraph removal |
| `chunk.py` | Headline+lead chunk, then sentence-packed chunks with one sentence of overlap |
| `embed.py` | `HashingEmbedder` (offline), multilingual-e5 and bge-m3 via sentence-transformers |
| `xling.py` | Devanagari romaniser, phonetic keys, bilingual news glossary: the offline cross-language bridge |
| `retrieve.py` | BM25, cross-language BM25, dense cosine, RRF fusion, exclusions by article and outlet, related-coverage grouping |
| `store.py` | SQLite: sources, articles, chunks with vectors, seen URLs |
| `analysis/lexicons.py` | English and Nepali word lists (sensational, attribution, anonymous, loaded, hedges) |
| `analysis/signals.py` | Deterministic signals with evidence |
| `facts.py` | Quantified facts in both languages (digits, number words, lakh/crore), fact kinds, hedging; per-figure agreement across independent sources |
| `story.py` | Same-story test (event type, shared subject, time window), opinion detection, near-duplicate detection, outlet ownership groups |
| `analysis/corroboration.py` | Candidates from retrieval, filtered to the same story, collapsed to independent sources; figures confirmed elsewhere; relative tone |
| `analysis/llm_review.py` | Claim audit through `anchor.llm`, citations enforced |
| `report.py` | Weighted score, label, JSON and Markdown rendering, disclaimer in both languages |
| `evaluate.py` | Retrieval metrics through `anchor.retrieval_metrics`; writes Anchor records |
| `cli.py` | `spashta demo · sources · ingest · analyze · search · eval` |

## What comes from Anchor

[Anchor](https://github.com/arnabmanandhar/anchor) is a dependency, not a
copy:

- `anchor.llm`: provider-neutral model access (Gemini, Groq, OpenRouter,
  Hugging Face, Mistral, OpenAI, xAI, local servers), masked
  keys, structured JSON with schema negotiation and validation.
- `anchor.retrieval_metrics`: Recall@k, MRR, nDCG, MAP for `spashta eval`.
- The citation-audit pattern of `anchor.judge`, applied to news claims.
- Records written by `spashta eval --records` can be scored and browsed in
  Anchor's dashboard.

## Data model (SQLite)

- `sources(url, name, lang, kind, endpoint, enabled, last_polled, last_error)`
- `articles(id, url, source, title, body, lang, published, author, summary, fetched_at)`
- `chunks(id, article_id, idx, text, lang, embedder, vector)`
- `seen(url, article_id, seen_at)`: listed URLs already fetched
- `http_cache(url, etag, last_modified, body, content_type)`: conditional GET for feeds and sitemaps

## Report

Each signal is `{name, concern 0..1, summary, evidence[], details{}}`.
The score is `100 × (1 − weighted mean concern)`; the label is "Few",
"Some" or "Many warning signs". The report always carries the disclaimer
that it describes presentation and sourcing, not truth.

## Testing

`pytest -q` runs offline: a fake transport serves fixture feeds, sitemaps
and pages; the LLM review is tested with a stubbed `complete_json`; the
sample corpus is synthetic.
