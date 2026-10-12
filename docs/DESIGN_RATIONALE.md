# Design rationale: the reasoning behind the chosen mechanism

This is the step-by-step reasoning for each major decision, written so a
reader can follow (and challenge) how the design was reached. The general
documentation is in [ARCHITECTURE.md](ARCHITECTURE.md) and the README; the
option survey is in [CASE_STUDIES.md](CASE_STUDIES.md). Decisions revised
in later review rounds are marked with the round that changed them.

---

## 1. What problem are we actually solving?

1. A reader in Nepal sees a headline on Facebook or a portal and wants to
   know: should I trust how this is being told?
2. "Is this true?" cannot be answered by software with any honesty for a
   breaking story. "Is this told responsibly, and does anyone else report
   it the same way?" can.
3. So the product question becomes: **measure presentation, sourcing and
   corroboration, show the evidence, and leave the verdict to the reader.**
   That reframing drives every later choice: anything that can't show its
   evidence is a weak fit.

## 2. Ingestion: why "any URL, resolved" instead of one method

1. Requirements: Nepali and English outlets; the long tail of portals and
   blogs; a "paste a link" flow; low cost; low legal risk.
2. RSS is the cheapest and most polite source, and most Nepali outlets run
   WordPress or similar CMSes that publish feeds. But not all do, and feed
   URLs change.
3. Sitemaps cover sites without feeds and give back-catalogue, but list URLs
   only.
4. News APIs (NewsAPI, GDELT) have thin or no Nepali-language coverage and
   would make a credibility tool depend on someone else's source list.
5. Per-site scrapers have the best text but break on every redesign and
   don't scale to blogs.
6. Every path except the API ends in "fetch a URL and get the article out",
   so a generic extractor is needed no matter what. Once it exists, the
   paths differ only in *how article URLs are listed*.
7. Therefore: one `Source` = any URL; resolve it to the cheapest listing
   method that works (feed → sitemap → link crawl); one extractor for all.
   Seed outlets are stored as home pages, so a changed feed URL is
   rediscovered rather than breaking.
8. Politeness and safety are part of the mechanism, not an afterthought:
   robots.txt, a named user agent, per-host delays, and an SSRF guard
   because a hosted instance will fetch URLs supplied by strangers.

## 3. Language: why build Nepali handling instead of translating

1. The tool judges *wording*. Translation changes wording, drops
   honorifics and flattens loaded terms, which is the signal we need.
2. Nepali is written in Devanagari with case markers glued to nouns
   (सरकारले, सरकारको). A whitespace tokenizer treats these as different
   words, so lexical search and overlap checks fail silently.
3. Digits come in two scripts and with lakh separators (१,२०,०००), and a
   number mismatch between headline and body is one of the strongest
   signals, so numbers must compare equal across forms.
4. Therefore: normalise (NFC, joiners, digits) once at the boundary; detect
   language by script (and tell Hindi apart, because Hindi pages exist on
   Nepali sites and Nepali lexicons would misfire); strip case markers with
   a minimum stem length so short words survive.

## 4. Retrieval: why hybrid BM25 + multilingual dense with RRF

1. What retrieval is for here: find *other outlets' coverage of the same
   event*, often in the other language, and passages that confirm or
   contradict specific facts.
2. Facts are names and numbers: lexical search is best at those and
   explains itself (matched terms).
3. Same-event coverage across languages and paraphrase needs semantic
   matching: a multilingual embedding model trained with Nepali
   (multilingual-e5, bge-m3).
4. Neither alone covers both needs; scores from the two are on different
   scales, and Reciprocal Rank Fusion combines rankings without
   calibration and is robust.
5. The corpus for one user or newsroom is thousands to tens of thousands of
   articles: SQLite + in-memory NumPy is enough and has zero operations
   cost; the search interface leaves room for sqlite-vec or pgvector.
6. Tests and CI must not download models, so a hashing embedder provides
   deterministic vectors offline; it handles morphology within a language
   but not cross-language, and the docs say so.
7. Retrieval quality is measured, not assumed: Anchor's metrics on a
   bilingual question set give a number to compare before and after any
   change.

## 5. Analysis: why layered signals and not "ask an LLM to rate it"

1. A credibility tool that can't show its work is itself not credible.
2. Many warning signs are lexical and countable: sensational words,
   exclamation marks, anonymous sourcing, loaded terms, numbers in the
   headline missing from the story. These are deterministic and
   explainable, so they always run.
3. Some judgments need the outside world (does anyone else report this
   figure?). Retrieval provides that evidence, and the comparison
   (figures, tone) stays deterministic.
4. Claim-level judgment benefits from a language model, but only under
   Anchor's discipline: the model lists claims and cites passages; scores
   are derived from the citations; citations of passages it wasn't given
   are rejected. It is optional, so the tool works with no key.
5. "No other coverage" is reported as *unverified*, never *false*: new,
   exclusive and local stories are legitimately uncorroborated.

## 6. Reporting: why a score at all

1. Readers want a quick answer; evidence lists alone are not read.
2. A single number invites over-trust, so it is labelled by warning signs
   ("Few/Some/Many"), comes with every signal's evidence, and carries a
   disclaimer in English and Nepali.
3. Weights are explicit constants that can be tuned against reviewed
   examples later.

## 7. Cross-language matching offline (round 2)

1. The multilingual embedder is the right tool for cross-language
   retrieval, but it is a large download and some deployments (CI,
   small servers, offline use) won't have it. Without it, a Nepali story
   could never find its English coverage, so corroboration would silently
   shrink to one language.
2. What actually crosses languages in news? Proper nouns (places, people,
   organisations), loanwords (petrol, cricket, bus) and a small, stable
   vocabulary of event words (landslide, killed, price, increase).
3. Proper nouns and loanwords are the same sounds in two scripts, so
   romanising Devanagari and comparing phonetic keys matches them. The
   same key handles romanised Nepali typed by readers.
4. The event vocabulary is small enough for a curated glossary; ambiguous
   words (rate, see) are kept out or accepted knowingly.
5. These are crude bridges, so they get their own ranking and RRF decides
   how much they count, rather than mixing them into the lexical index
   where they would distort term statistics.
6. A glossary tuned on a benchmark flatters that benchmark, so held-out
   questions are kept separately and reported on their own.
