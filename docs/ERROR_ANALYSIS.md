# Error analysis

Real failures, what caused them, and what would fix them. Fill this in
from `error_candidates.md` (`spashta dataset errors`), which lists the
most confident false alarms and misses per signal and the worst
retrieval queries on the labelled snapshot. Aim for 15–20 cases across
all signals and retrieval, including ones that are embarrassing: they
are the most useful part of the evaluation.

Snapshot: `TODO snapshot id` · labels: `TODO gold file` · Spashta version: TODO

## Summary

| Cause | Cases | Signals affected | Fix | Effort |
|---|---|---|---|---|
| TODO e.g. Nepali honorific verb forms missing from the attribution list | | sourcing | add जनाउनुभयो, बताउनुभयो… with a test | small |
| | | | | |

## Cases

Use one block per case. Quote at most a sentence of the article (the
article text is the publisher's); link the URL.

### 1. TODO short name of the failure

- **Signal / task:** e.g. sourcing, false alarm (concern 0.72, label: well sourced)
- **Article:** [headline](url), outlet, language
- **What Spashta did:** the evidence lines it showed
- **What the label says, and why:**
- **Cause:** e.g. the attribution verb was in an honorific form the word list doesn't have
- **Fix:** e.g. add the forms to `lexicons.ATTRIBUTION` with a test; or "none: the label is debatable"
- **Category:** lexicon gap / extraction (boilerplate, cut text) / language detection / tokenisation or stemming /
  romanisation or phonetic key / glossary gap / same-story matcher / fact extraction / label ambiguity / other

## Patterns

TODO: two or three paragraphs. Which causes recur, which signals fail
in which language, what a better method (a trained classifier, a larger
glossary, an LLM) would need to beat, and what you would not fix because
the label itself is subjective (see the agreement table).
