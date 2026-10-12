# Review log

Each round: critique the current build, decide what to change and why,
change it, measure. Every round is one commit (`git log` shows them).

## v0.1.0: first working build

Ingestion (feed → sitemap → link crawl, generic extraction), Nepali and
English normalisation, hybrid BM25 + dense retrieval with RRF, four
deterministic signals, corroboration, optional LLM claim review, CLI,
retrieval evaluation through Anchor. 36 tests.
