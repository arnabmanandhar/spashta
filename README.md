# Spashta (स्पष्ट)

**News integrity reports for Nepali and English.**

Spashta helps readers check how a news story is told: whether the
headline matches the story, how sensational the wording is, whether
claims are attributed to named sources, whether the language is loaded,
and whether other outlets report the same facts. Every finding comes with
the words, numbers or passages that produced it.

It does not decide whether a story is true. It shows the evidence so the
reader can.

## What it does

1. **Ingests news from anywhere.** Add any news site, blog or feed by URL.
   Spashta finds the site's RSS/Atom/JSON feed, or its sitemap, or falls
   back to collecting article links from the page, then extracts the full
   article text. Single articles can be checked by URL, saved HTML or
   pasted text.
2. **Understands Nepali and English.** Unicode and digit normalisation,
   language detection, a Nepali tokenizer that strips case markers
   (सरकारले → सरकार), and numbers that match across scripts (१,२०० = 1200).
3. **Retrieves related coverage (RAG).** Hybrid search: BM25 for names
   and numbers plus multilingual embeddings (multilingual-e5 or bge-m3) for
   meaning and cross-language matches, fused with Reciprocal Rank Fusion.
4. **Reports integrity signals:** headline consistency, sensationalism,
   sourcing, framing, corroboration across outlets, and optionally a
   claim-by-claim audit by any LLM (through
   [Anchor](https://github.com/arnabmanandhar/anchor)), where every claim
   must cite the passage that supports or contradicts it.

## Quickstart

```bash
pip install -e ".[extract]"     # trafilatura for best text extraction
spashta demo                    # synthetic sample articles, no network, no key
spashta eval                    # retrieval metrics on the bilingual sample questions
```

Check real news:

```bash
spashta sources seed                         # major Nepali and English outlets
spashta sources add https://some-blog.example --resolve
spashta ingest --limit 20                    # poll sources, index new articles
spashta analyze https://example.com/news/some-article
spashta search "पेट्रोलको मूल्य"
```

Better cross-language retrieval (downloads a model once):

```bash
pip install -e ".[ml]"
export SPASHTA_EMBEDDER=e5-small             # or e5-base, bge-m3
```

Claim-level review with a language model (any provider Anchor supports;
Gemini and Groq have free tiers):

```bash
export GEMINI_API_KEY=...
spashta analyze https://example.com/news/some-article --llm
```

## Web API and page

```bash
pip install -e ".[api]"
export SPASHTA_ADMIN_TOKEN=change-me        # enables adding sources and ingesting over HTTP
uvicorn spashta.api:app --port 8000
```

Open http://localhost:8000 for a bilingual page (paste a link or the text
of a story), or call the JSON API: `POST /api/analyze`, `GET /api/search`,
`GET|POST /api/sources`, `POST /api/ingest`. Requests are rate limited per
client, and user-supplied URLs can never reach private or local addresses.

## Documentation

- [docs/CASE_STUDIES.md](docs/CASE_STUDIES.md): every ingestion, retrieval
  and analysis option considered, scored and compared
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md): how the system is built
- [docs/DESIGN_RATIONALE.md](docs/DESIGN_RATIONALE.md): the reasoning
  behind each chosen mechanism
- [docs/REVIEW_LOG.md](docs/REVIEW_LOG.md): each critique-and-improve round

## Tests

```bash
pip install -e ".[dev]"
pytest -q
ruff check .
```

Everything runs offline: fixtures for feeds, sitemaps and pages, a
synthetic sample corpus, and a stubbed model for the LLM review.

## Responsible use

Spashta identifies itself as `SpashtaBot`, honours robots.txt, waits
between requests to a site, and stores only what analysis needs with a
link back to the source. The sample articles are fictional.
