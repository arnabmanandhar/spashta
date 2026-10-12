# Review log

Each round: critique the current build, decide what to change and why,
change it, measure. Every round is one commit (`git log` shows them).

## v0.1.0: first working build

Ingestion (feed → sitemap → link crawl, generic extraction), Nepali and
English normalisation, hybrid BM25 + dense retrieval with RRF, four
deterministic signals, corroboration, optional LLM claim review, CLI,
retrieval evaluation through Anchor. 36 tests.

## Round 1 (v0.2.0): a benchmark that can actually fail

**Critique.** The v0.1 evaluation scored a perfect 1.0 on every metric.
With 8 articles on 4 unrelated topics, any retriever wins, so the number
said nothing and no later change could be measured against it. There was
also no way to tell whether the hybrid design beat its own halves.

**Change.**
- Sample corpus grew from 8 to 17 synthetic articles with deliberate
  distractors: a different landslide a year earlier (Myagdi), a different
  fuel story (LPG), a road story sharing the place name, a near-duplicate
  wire copy, an opinion blog, and Nepali/English pairs for every event.
- Question set grew from 8 to 16 queries, each tagged `ne`, `en`, `xl`
  (relevant articles in both languages), `para` (paraphrase) or `rom`
  (romanised Nepali), with every same-event article marked relevant.
- `Index.search(mode=...)` and `spashta eval --compare`: lexical-only,
  dense-only and hybrid on the same questions, with Anchor's paired
  significance test between them. Results are broken down by tag.

**Result (hashing embedder, k=5).**

| Mode | Recall@5 | nDCG@5 | MAP |
|---|---|---|---|
| lexical | 0.615 | 0.702 | 0.599 |
| dense (hashing) | 0.724 | 0.772 | 0.681 |
| hybrid | 0.724 | 0.773 | 0.683 |

Cross-language (`xl`) nDCG is the weakest slice at 0.72, and romanised
queries reach 0.71 only because place names are spelled the same in
English. Those are the targets for the next rounds. Hybrid vs lexical:
+0.071 nDCG, 95% CI [+0.015, +0.142], p = 0.12 on 16 questions (not
significant yet: the question set is still small).
