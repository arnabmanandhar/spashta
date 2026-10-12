# Case studies: how Spashta could be built

Spashta has to do three things well:

1. **Get the news in**, from Nepali and English outlets, and from any
   website or blog a user points it at.
2. **Retrieve related coverage** for any article (RAG), in Nepali, English
   or a mix, so a claim can be checked against what other outlets said.
3. **Turn that into an integrity report**: headline/body consistency,
   sensationalism, sourcing, bias signals and cross-source corroboration,
   each with the evidence behind it.

This document lays out the realistic options for each part, scores them,
and records the choice. [ARCHITECTURE.md](ARCHITECTURE.md) describes the
system built from those choices, [DESIGN_RATIONALE.md](DESIGN_RATIONALE.md)
gives the reasoning step by step, and [REVIEW_LOG.md](REVIEW_LOG.md)
records how ten rounds of critique refined the first build (notably a
third, cross-language ranking in retrieval, same-story and source
independence checks, and fact-level comparison across outlets).

Scores below are 1 (poor) to 5 (good). "Coverage" means how many Nepali and
English outlets the option actually reaches today.

---

## Part 1. Ingestion

### Option A. RSS / Atom feeds

Most Nepali outlets run WordPress or a CMS with a feed (Onlinekhabar,
Setopati, Ratopati, Nagarik, Kathmandu Post, Himalayan Times, Republica,
Rising Nepal and many regional portals). A feed gives title, link, publish
time, author and usually a summary.

- **Pros:** cheap, polite (one request per poll), structured, timestamps
  included, standard formats (RSS 2.0, Atom, JSON Feed). Encoding is UTF-8,
  so Devanagari arrives intact.
- **Cons:** only the latest 10-50 items; summaries are often truncated so
  the full article still has to be fetched; some sites have no feed or a
  broken one.
- Coverage 4 · Cost 5 · Reliability 4 · Legal/ethical risk 5 (feeds are
  published to be consumed) · Effort 5

### Option B. News sitemaps (`sitemap.xml`, Google News sitemaps)

Sites that care about search traffic publish sitemaps, usually listed in
`robots.txt`. Google News sitemaps (`<news:news>`) carry title, language
and publication date for the last 48 hours.

- **Pros:** complete list of article URLs, including back-catalogue
  (useful for building the retrieval corpus); declared in `robots.txt`.
- **Cons:** URLs only (plus lastmod), so every article is a full fetch;
  sitemap indexes can be huge; some Nepali sites don't keep them current.
- Coverage 3 · Cost 3 · Reliability 4 · Risk 5 · Effort 4

### Option C. Third-party news APIs (NewsAPI, GDELT, Mediastack, Event Registry)

- **Pros:** one integration covers thousands of sources, with de-duplication
  and metadata; GDELT is free and multilingual.
- **Cons:** Nepali-language coverage is thin (GDELT translates rather than
  stores Nepali text; NewsAPI has no Nepali sources); commercial APIs cost
  money and limit full text; you inherit someone else's source list and
  biases, which is awkward for a credibility tool.
- Coverage of Nepali 1 · English 4 · Cost 2 · Reliability 4 · Risk 4 · Effort 4

### Option D. Site-specific scrapers

A hand-written parser per outlet (CSS selectors for headline, body, author).

- **Pros:** the cleanest text for the sites covered; can pick up fields
  like category or reporter.
- **Cons:** breaks every time a site redesigns; one parser per outlet does
  not scale; higher legal and politeness risk; nothing for the long tail of
  blogs.
- Coverage 2 · Cost 4 · Reliability 2 · Risk 2 · Effort 1

### Option E. Generic article extraction from any URL ("link feed")

Given any URL, fetch it and extract the main text with a boilerplate
remover (trafilatura, readability), using page metadata (OpenGraph,
JSON-LD `NewsArticle`, `<html lang>`) for headline, date and author. Given
a site's home page, *discover* its feed (`<link rel="alternate">`) or its
sitemap (`robots.txt`), and fall back to collecting same-site article
links from the page.

- **Pros:** works on any news site or blog with no per-site code; the same
  extractor serves the "paste a link" user flow and full-text fetching for
  A and B; trafilatura handles Devanagari well and is maintained.
- **Cons:** extraction quality varies on unusual layouts; link-crawling
  fallback needs heuristics to tell articles from section pages.
- Coverage 5 · Cost 4 · Reliability 3 · Risk 4 · Effort 3

### Option F. User submission (paste text, upload, browser extension)

- **Pros:** zero crawling; covers paywalled or social posts the user can
  see; the natural way to ask "is *this* credible?".
- **Cons:** no corpus building on its own; text quality depends on the user.
- Always needed as an entry point, never sufficient alone.

### Option G. Social platforms (Facebook pages, X, YouTube)

A large share of Nepali news is consumed on Facebook. Platform APIs are
restricted, scraping them violates terms, and the content is mostly links
back to articles anyway. Out of scope for now; F covers the case where a
user pastes a post.

### Ingestion comparison

| Option | Nepali coverage | English coverage | Cost | Reliability | Risk | Effort | Total |
|---|---|---|---|---|---|---|---|
| A. RSS/Atom | 4 | 4 | 5 | 4 | 5 | 5 | **27** |
| B. Sitemaps | 3 | 4 | 3 | 4 | 5 | 4 | 23 |
| C. News APIs | 1 | 4 | 2 | 4 | 4 | 4 | 19 |
| D. Per-site scrapers | 3 | 2 | 4 | 2 | 2 | 1 | 14 |
| E. Generic link feed | 5 | 5 | 4 | 3 | 4 | 3 | **24** |
| F. User submission | n/a | n/a | 5 | 3 | 5 | 5 | entry point |

### Ingestion decision

**A + B + E behind one `Source` abstraction, with F as the user entry
point. C is an optional adapter later; D only as a per-site override when
generic extraction fails on an important outlet.**

A user (or the seed list) adds a source as *any URL*. Spashta resolves it
in order:

1. The URL is itself a feed → poll it (A).
2. The page advertises a feed → poll that (A).
3. `robots.txt` lists a sitemap, or `/sitemap.xml` exists → read it (B).
4. Otherwise → collect same-site links that look like articles (E).

Every discovered article URL then goes through the same extractor (E) for
full text, because feed summaries are not enough to judge an article. A
single pasted link (F) skips discovery and goes straight to extraction.

This is the cheapest option that reaches the long tail of Nepali portals
and blogs, and it degrades gracefully: a site with a feed costs one request
per poll, a site without one still works.

Ground rules for every fetch: identify as `SpashtaBot` with a contact URL,
honour `robots.txt`, rate-limit per host, cache by URL and `ETag`, and
store only what the analysis needs (text, metadata, a link back), never
republish full articles.

---

## Part 2. Retrieval (RAG) for Nepali and English

What retrieval is *for* here matters: given an article, find other
coverage of the same event (to compare facts and framing) and background
passages (to check a claim). Queries are whole headlines or claims, not
short keyword searches, and the matching article is often in the other
language (a Kantipur story and a Kathmandu Post story about the same
event).

Nepali-specific problems any option has to handle:

- **Script and normalisation.** Devanagari text arrives in mixed Unicode
  forms (precomposed vs. combining nukta, ZWJ/ZWNJ, stray danda variants).
  It has to be NFC-normalised and cleaned before anything else.
- **Digits.** Both ०-९ and 0-9 are used; numbers must compare equal.
- **Morphology.** Nepali attaches case markers to the noun: `सरकारले`,
  `सरकारको`, `सरकारलाई` are all "government". Without stripping these,
  lexical search misses most matches.
- **Dates.** Bikram Sambat (2083) and Gregorian (2026) dates both appear.
- **Romanised Nepali** in blogs and comments ("sarkar le bhanyo").
- **Few labelled datasets**, so evaluation has to build its own questions.

### Option R1. Lexical only (BM25)

- **Pros:** exact, cheap, explainable, great on names and numbers.
- **Cons:** no cross-language matching; needs a Nepali tokenizer and
  suffix stripper to be any good on Nepali.
- Nepali 3 (with stemming) · Cross-language 1 · Cost 5 · Explainability 5

### Option R2. Dense multilingual embeddings

Candidates that include Nepali in training: `BAAI/bge-m3`,
`intfloat/multilingual-e5-base/small/large`,
`sentence-transformers/paraphrase-multilingual-mpnet-base-v2` (weaker on
Nepali), LaBSE (good for cross-lingual sentence matching, weaker for
passage retrieval), and hosted APIs (Cohere embed-multilingual, Gemini
embeddings, OpenAI text-embedding-3).

- **Pros:** semantic and cross-language matching (Nepali query finds the
  English article); handles paraphrase and romanisation better.
- **Cons:** misses exact names and numbers more often; a model download or
  API cost; harder to explain why something matched.
- Nepali 4 (bge-m3, e5) · Cross-language 4 · Cost 3 · Explainability 2

### Option R3. Hybrid: BM25 + dense, fused with Reciprocal Rank Fusion

- **Pros:** each half covers the other's blind spot (names/numbers vs.
  meaning/language); RRF needs no score calibration; the lexical half keeps
  results explainable.
- **Cons:** two indexes to maintain.
- Nepali 5 · Cross-language 4 · Cost 3 · Explainability 4

### Option R4. Translate everything to English, then retrieve

- **Pros:** one English index; English tooling is best.
- **Cons:** translation cost on every article; machine translation of
  Nepali loses names, honorifics and framing, which is exactly what a bias
  tool must preserve; errors compound.
- Rejected as the primary path. Translation stays useful for *display*
  (showing a Nepali user what the English coverage said).

### Option R5. Hybrid plus a cross-encoder reranker (`bge-reranker-v2-m3`)

- **Pros:** best precision at the top of the list.
- **Cons:** slow on CPU; only worth it once the corpus is large.
- Kept as a later upgrade behind the same interface.

### Vector store options

| Store | Fit |
|---|---|
| SQLite + in-process NumPy | Zero ops, fine to ~100k chunks. **Start here.** |
| SQLite + `sqlite-vec` | Same file, real ANN index; next step. |
| PostgreSQL + `pgvector` | When there is a server and multiple writers. |
| Qdrant / Weaviate / Milvus | Only at millions of chunks or for hybrid search built in. |

### Retrieval decision

**R3: hybrid BM25 + multilingual dense retrieval, fused with RRF, over
language-aware chunks, stored in SQLite.**

- Lexical half: a Nepali-aware tokenizer (NFC, digit folding, danda and
  punctuation handling, stopwords, case-marker stripping) and a light
  English stemmer, with BM25 per language.
- Dense half: `intfloat/multilingual-e5-small` by default (small, CPU
  friendly, covers Nepali and English in one space), `BAAI/bge-m3` as the
  quality option. A dependency-free hashing embedder keeps everything
  working offline and in CI.
- Retrieval is evaluated with Anchor's metrics (Recall@k, MRR, nDCG) on a
  bilingual question set, so a model or tokenizer change can be judged
  with numbers instead of impressions.

---

## Part 3. Credibility analysis

### Option Q1. LLM-only ("rate this article 1-10")

Fast to build and fluent, but unexplainable, inconsistent between runs,
and easy to steer. A credibility tool that can't show its working is
itself not credible. Rejected as the primary method.

### Option Q2. Rules and lexicons only

Deterministic and explainable: clickbait phrases, superlatives,
exclamation marks, all-caps, unattributed claims, loaded words, opinion
markers, in both languages. Misses subtler framing and can't judge claims.

### Option Q3. Supervised classifiers (fake-news / clickbait models)

Public datasets are almost all English, and labels like "fake" don't
capture framing or bias. A Nepali clickbait classifier would need a
labelled dataset first. Worth doing later, once Spashta has collected
reviewed examples.

### Option Q4. Layered: deterministic signals, then retrieval-grounded checks, then an optional audited LLM review

The Anchor approach applied to news:

1. **Deterministic signals** (Q2) always run and are always explainable.
2. **Corroboration via RAG**: retrieve coverage of the same story from
   other outlets, then compare key facts (numbers, named entities) and
   framing intensity across them.
3. **LLM review (optional, any provider via Anchor)**: the model must
   break the article into claims and, for each, cite the retrieved passage
   that supports or contradicts it, or mark it unverified. Scores are
   derived from that audit trail, never asked for directly, and citations
   of passages that weren't retrieved are rejected.

### Analysis decision

**Q4.** Every score in a report comes with the phrases, numbers or
passages that produced it. The tool works with no API key; an LLM makes
the claim check richer but never replaces the evidence.

---

## Part 4. Delivery

| Option | Notes |
|---|---|
| CLI + Python library | First. Lets the pipeline be tested end to end. |
| HTTP API (FastAPI) | Next. Needed by every UI. |
| Web app (Streamlit, like Anchor's dashboard) | Quickest UI for "paste a link, get a report". |
| Browser extension | Best user experience (report on the page being read); after the API. |
| Messenger/Viber bot | High reach in Nepal; after the API. |

---

## Summary of the chosen design

| Part | Choice |
|---|---|
| Ingestion | Any URL resolved to feed → sitemap → link crawl; full text by generic extraction; paste/URL for single articles; APIs later |
| Language | Script-based detection; Nepali normalisation, tokenizer and case-marker stripping |
| Retrieval | Hybrid BM25 + multilingual-e5 (or bge-m3), RRF, SQLite |
| Analysis | Deterministic signals + RAG corroboration + optional citation-audited LLM review |
| Evaluation | Anchor retrieval metrics on a bilingual question set |
| Delivery | CLI/library now; API, web app, extension next |
