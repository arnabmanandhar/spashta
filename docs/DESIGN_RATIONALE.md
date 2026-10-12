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

## 8. Same story and independent sources (round 3)

1. Corroboration means "someone else, who didn't copy this, saw the same
   thing". Retrieval similarity measures neither "same thing" nor
   "someone else".
2. "Same thing" in news is an event: a kind of happening plus a specific
   subject at a time. So the test is structural: shared event type, shared
   subject, close in time. A similarity threshold was tried first and
   failed: generic words (district, police, landslide) made two different
   landslides look more alike than a story and its translation.
3. Proper names are the strongest cross-language subject signal, but
   Devanagari has no capitalisation. English text does, so names are
   learned there and stored as phonetic keys that also match Devanagari.
4. "Someone else": syndicated copies and same-owner outlets are not
   independent, so they collapse; commentary is not a witness, so it is
   listed but not counted.
5. Each match carries its reason, so a reader can reject a wrong match.

## 9. Comparing facts (round 4)

1. Two outlets disagreeing on a death toll is the single most useful thing
   Spashta can show a reader, and it needs no language model.
2. A figure is only comparable with its meaning: 12 deaths and 12 houses
   are different facts. In both languages the counted thing sits next to
   the number (Nepali puts it after: १२ जनाको मृत्यु; English either side:
   "killed 12", "12 dead"), so the nearest fact word in the same clause is
   a reliable label, with generic words ("people", जना) losing to specific
   ones.
3. Number words matter because translations and English style guides
   spell out small numbers.
4. Agreement is decided by independent sources, by majority, so a figure
   repeated only by sites that copy each other or by one other
   sensational site does not become "confirmed".
5. Hedged figures are kept and labelled, because "50 feared dead" is a
   claim about uncertainty, and the reader should see that.

## 10. Confidence and weighted evidence (round 5)

1. A missing measurement is not a good measurement. Signals now say how
   much text they had, and the score trusts them accordingly.
2. Repetition is not confirmation. Borrowing from truth-discovery methods
   (where each source's reliability weights its votes), a source's vote on
   a figure is weighted by how measured its own writing is. This is
   deliberately simple and visible in the report; a learned reliability
   per outlet can replace it once there is reviewed data.
3. Some quantities have one true value per story (a death toll), others
   legitimately have several (prices of different fuels), so agreement is
   judged differently for each.
4. Every calibration claim is a test over labelled samples, so a lexicon
   or weight change that breaks the ordering fails CI.

## 11. Delivery: API first (round 8)

1. Readers in Nepal mostly meet news on phones, in Facebook and Viber.
   Every way to reach them (page, extension, bot) is a client of one HTTP
   API, so the API comes before any of them.
2. The first client is a plain page: one HTML file, no build step, Nepali
   first, so it can be hosted anywhere and audited easily.
3. An endpoint that fetches arbitrary URLs is an SSRF and abuse target.
   The fetcher already refuses private addresses on every hop; the API adds
   rate limits, size limits, and keeps write operations behind a token.

## 12. The model layer under attack (round 9)

1. Spashta's input is, by definition, content written to persuade, and
   some of it will be written to manipulate tools like Spashta. So the
   model must treat article text as data, structurally (tags it cannot
   forge) and by instruction, and its output is checked after the fact
   (citations must point at passages it was actually given).
2. A review must be repeatable to be trusted: same input, same answer.
   Caching by the exact prompt and model gives that, and makes repeat
   checks free.
3. Retrieval stays cheap and explainable by default; the cross-encoder is
   an optional precision layer over the top of the list, not a
   replacement for the explainable rankings.
