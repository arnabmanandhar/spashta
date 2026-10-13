---
# Hugging Face dataset card. `spashta dataset card` fills the double-brace fields
# from the release; edit everything marked TODO by hand before publishing.
pretty_name: Spashta Nepali–English News Integrity
language:
  - ne
  - en
license: cc-by-4.0  # applies to the labels, queries and judgments; article text is not included
task_categories:
  - text-classification
  - text-retrieval
  - token-classification
size_categories:
  - n<1K
tags:
  - nepali
  - low-resource
  - news
  - media-literacy
  - cross-lingual-retrieval
configs:
  - config_name: articles
    data_files: articles.jsonl
  - config_name: labels
    data_files: labels.jsonl
  - config_name: pairs
    data_files: pairs.jsonl
  - config_name: queries
    data_files: queries.jsonl
---

# Spashta: Nepali–English news integrity labels

Labels for how Nepali and English news from Nepal is told and sourced
(headline consistency, sensationalism, claim sourcing, loaded language),
same-event pairs across outlets, and bilingual retrieval queries with
pooled relevance judgments. Built to measure
[Spashta](https://github.com/arnabmanandhar/spashta) honestly.

**No article text is included.** Each article is a URL with SHA-256
hashes of its title and body; labels point into the text by character
offsets. `spashta dataset rehydrate` re-fetches the text and checks the
hashes (see "Getting the text").

## Contents

| File | Rows | What |
|---|---|---|
| `articles.jsonl` | {{n_articles}} | URL, outlet, owner group, language, publication date, hashes, length, topic |
| `labels.jsonl` | {{n_labels}} | per-article labels (below) |
| `pairs.jsonl` | {{n_pairs}} | article pairs: same event or not, conflicting figures |
| `queries.jsonl` | {{n_queries}} | queries in Nepali, English and romanised Nepali, with graded judgments |

Snapshot id `{{snapshot_id}}` (a hash over every article id and content
hash: a rehydrated copy that verifies is the same corpus).

## Sources and collection

- **Collection period:** {{window}}.
- **Outlets:** {{n_outlets}} outlets in {{n_groups}} ownership groups.
  Outlets with a common owner count as one source when judging
  corroboration.

{{outlets_table}}

- **Languages:** {{langs}}.
- **How:** Spashta's ingester polled each outlet's feed or sitemap and
  extracted the article text ({{software}}). Pages under {{min_chars}}
  characters were dropped. TODO: note anything that broke during
  collection (an outlet down for a week, a paywall).

## Sampling for labels

{{n_labels}} articles drawn by `spashta dataset sample`: stratified by
language and topic (politics, economy, disaster, crime, sports, other),
round-robin across outlets, with stories covered by several independent
outlets drawn three times as often (so this is **not** a random sample
of Nepali news: don't read label frequencies as prevalence).

## Labels

Guidelines: `docs/ANNOTATION.md` (v2), with anchored examples for every
level. Summary:

- **headline**: consistent / partly / inconsistent with the story.
- **sensationalism**: 0 measured / 1 some / 2 heavy.
- **claims**: key factual claims as character spans, each marked
  named source / anonymous source / unattributed.
- **loaded**: spans of loaded or opinionated language in news text.
- **topic**, **opinion** (commentary rather than news).
- **pairs**: same event yes / no / unsure; conflicting figures.
- **queries**: relevance 0 / 1 / 2, judged on pools of the top 10 results
  from BM25, dense, cross-language and hybrid retrieval (TREC-style
  pooling: no method is favoured; unjudged articles count as not
  relevant).

Annotators: TODO (the primary annotator, the second annotator's
background, e.g. "a journalism student fluent in Nepali and English";
whether they were paid).

## Agreement

Two annotators labelled {{n_double}} articles independently.
Disagreements were adjudicated by discussion. Cohen's kappa, 95%
bootstrap intervals:

{{agreement_table}}

TODO: one paragraph on what the low-kappa signals mean (a low kappa means
the signal, as defined, is subjective; say which and why).

## Getting the text

```bash
pip install "spashta @ git+https://github.com/arnabmanandhar/spashta"
spashta dataset rehydrate . ./full
```

This fetches each URL politely (robots.txt, one request per second per
site), extracts the article with the same extractor, and reports how many
match their hash exactly. Labelled spans are re-anchored by their own
hashes when an outlet has edited a page; spans that can't be found are
reported, not guessed. Pages that have been removed can't be recovered.

## Limitations

- Single country, {{n_outlets}} outlets, {{window}}: not representative of
  all Nepali media, and topic mix depends on that period's news.
- The sample over-represents multi-outlet stories (by design).
- Labels describe presentation and sourcing, not truth: an article can
  be measured, well sourced and wrong.
- The primary annotator also built the system being evaluated. TODO: how
  this was mitigated (the second annotator, labelling before looking at
  Spashta's scores).
- Romanised Nepali queries were written by TODO, and romanisation habits
  vary by writer.
- Links rot: some articles will become unavailable over time.

## License and use

Labels, queries and judgments: CC BY 4.0. Article text and headlines
belong to their publishers and are not redistributed here. Intended for
research and teaching on news presentation and cross-lingual retrieval;
not for ranking or blacklisting outlets.

## Citation

TODO: add a BibTeX entry once there is a write-up.
