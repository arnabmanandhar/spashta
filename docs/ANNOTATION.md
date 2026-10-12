# Building a real evaluation set

Every number Spashta reports about its own accuracy so far comes from a
small synthetic corpus written for testing. Real accuracy needs real
articles labelled by people. This is the plan and the format, so the
existing commands (`spashta eval`, `spashta eval --signals`) run on it
unchanged.

## What to collect

- 300+ articles: half Nepali, half English; at least 15 outlets including
  national dailies, online portals, regional sites and blogs; a spread of
  topics (disasters, politics, economy, sport, education).
- Group them into events: for each event, all coverage you can find within
  a week, in both languages.
- Include known problem cases: headlines later corrected, viral rumours,
  sponsored content, copied wire text.

## Labels per article

Two annotators per article, independently; disagreements go to a third.

| Label | Question the annotator answers |
|---|---|
| `event` | Which event does this article report? (free id, shared across articles) |
| `sensational` | Does the wording aim to provoke emotion beyond what the facts warrant? |
| `loaded` | Does it use charged or opinionated words while presenting itself as news? |
| `poorly_sourced` | Are most factual claims missing a named source? |
| `headline_unsupported` | Does the headline claim something the body doesn't support? |
| `opinion` | Is it commentary rather than a news report? |

Report Cohen's kappa for each label before using it; a label annotators
can't agree on (kappa < 0.4) is not one Spashta should be scored against.

## Format

Same shape as `spashta/data/sample_articles.json`:

```json
{"articles": [{"url": "...", "title": "...", "body": "...", "published": "2026-10-10T06:30:00+05:45",
               "event": "sindhupalchok-landslide-2026-10",
               "labels": {"sensational": false, "loaded": false, "poorly_sourced": false}}]}
```

Retrieval questions use `spashta/data/sample_questions.json`'s shape:
`{"query", "relevant_urls", "tags"}`.

## Commands

```bash
spashta eval --signals my_articles.json     # AUC per signal and overall
spashta eval my_questions.json --compare    # retrieval ablation with significance (after ingesting the articles)
```

Store the article text only as long as the licence allows; keep URLs and
labels permanently.
