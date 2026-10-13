# From demo to evidence: the runbook

The commands for every step of the plan, in order, for PowerShell on
Windows (they work the same in bash). Run them from the repository
folder (`D:\spashta`) after `pip install -e ".[dev,ml]"`.

Folders used below (all git-ignored except `release\`):

| Path | What | Publish? |
|---|---|---|
| `data\live.db` | the live store you ingest into | no |
| `snapshots\v1\` | the frozen corpus with full text | **no** (publishers' text) |
| `data\eval.db` | an index built from the snapshot only | no |
| `labels\` | sample, label files, agreement, gold | the gold labels go into the release |
| `results\` | evaluation JSON and RESULTS.md | yes |
| `release\` | the public dataset: no article text | yes (Hugging Face) |

Set the embedder once per shell, and use the same one for pooling and
evaluation:

```powershell
$env:SPASHTA_EMBEDDER = "e5-small"    # multilingual-e5-small; e5-base is better and slower
mkdir data, labels, results -Force
```

## Phase 1: build the dataset

### 1. Choose outlets and collect

Pick 8–12 outlets: Nepali and English, broadsheets and online portals,
and owner groups with more than one outlet so the "same owner counts
once" rule is tested. The bundled list has four such groups, each with a
Nepali and an English outlet. A suggested set of 11:

| Group | Outlets |
|---|---|
| Kantipur Media | ekantipur.com (ne), kathmandupost.com (en) |
| Nagarik Network | nagariknews.nagariknetwork.com (ne), myrepublica.nagariknetwork.com (en) |
| Onlinekhabar | onlinekhabar.com (ne), english.onlinekhabar.com (en) |
| Gorkhapatra Corporation | gorkhapatraonline.com (ne), risingnepaldaily.com (en) |
| independent | setopati.com (ne), ratopati.com (ne), thehimalayantimes.com (en) |

Add one or two smaller or more sensational-leaning sites of your own
choosing (`spashta sources add URL --lang ne`), so the sensationalism
labels have positives.

```powershell
spashta --db data\live.db sources seed
spashta --db data\live.db sources list
# Backfill the last four weeks from the sites' sitemaps (slow: one request per second per site)
spashta --db data\live.db ingest --since 2026-09-15 --workers 6
spashta --db data\live.db sources list          # check every outlet has articles; fix or drop the ones that don't
```

Then keep collecting for 3–4 weeks: some sites have no dated sitemaps,
and polling catches what backfill misses. Schedule it every 3 hours:

```powershell
schtasks /Create /SC HOURLY /MO 3 /TN SpashtaIngest /TR "cmd /c cd /d D:\spashta && spashta --db data\live.db ingest >> data\ingest.log 2>&1"
```

(On the VPS, `deploy/` has a systemd timer that does the same.)

### 2. Freeze the snapshot

```powershell
spashta --db data\live.db dataset freeze snapshots\v1 --since 2026-09-15 --until 2026-10-12 --note "11 outlets, backfill + 3h polling"
spashta dataset verify snapshots\v1
```

Write the snapshot id down: every number you report refers to it. Back
the folder up privately (it is not in git).

### 3. Sample for labelling

```powershell
spashta dataset sample snapshots\v1 -o labels\sample.jsonl --n 400
spashta dataset pairs snapshots\v1 labels\sample.jsonl -o labels\pairs_todo.jsonl
```

The summary shows the spread by language, topic and outlet, and how
many sampled stories several outlets covered.

### 4. Pilot the guide

```powershell
spashta dataset labeltool snapshots\v1 labels\sample.jsonl --batch pilot -o labels\pilot.html
start labels\pilot.html
```

Type your initials in the Annotator box (AM below; the second annotator
uses theirs, XY below). Label the 30 pilot articles (the short guide is at the top of the page;
the full one is `docs/ANNOTATION.md`). Use the notes box whenever the
guide doesn't tell you what to do. Export, then revise
`docs/ANNOTATION.md` and log each change under "Revisions". Re-label the
pilot articles with the revised guide before using them as gold.

### 5. Label

```powershell
spashta dataset labeltool snapshots\v1 labels\sample.jsonl --batch main -o labels\main.html
spashta dataset labeltool snapshots\v1 --pairs labels\pairs_todo.jsonl -o labels\pairs.html
# after exporting from each page:
spashta dataset check snapshots\v1 labels\labels-article-main-AM.jsonl
```

The page keeps your progress in the browser; export often, and Import
restores it on another computer.

### 6. Second annotator and agreement

```powershell
spashta dataset labeltool snapshots\v1 labels\sample.jsonl --double -o labels\second_annotator.html
```

Send them `second_annotator.html` and `docs/ANNOTATION.md` (and the
pairs page). The page contains article text: share it privately, not
publicly. When both are done:

```powershell
spashta dataset agreement snapshots\v1 --a labels\labels-article-pilot-AM.jsonl labels\labels-article-main-AM.jsonl labels\labels-pairs-AM.jsonl labels\labels-qrels-AM.jsonl --b labels\labels-article-double-XY.jsonl labels\labels-pairs-XY.jsonl labels\labels-qrels-XY.jsonl --out results\agreement.json --markdown
```

Decide disagreements together and write only the decided fields, one
line per article, to `labels\adjudicated.jsonl`, e.g.
`{"task": "article", "article_id": "…", "headline": "partly"}`. Then:

```powershell
spashta dataset gold labels\labels-article-pilot-AM.jsonl labels\labels-article-main-AM.jsonl --adjudicated labels\adjudicated.jsonl -o labels\gold.jsonl
spashta dataset gold labels\labels-pairs-AM.jsonl -o labels\gold_pairs.jsonl
```

### 7. Retrieval test set

Write 150–200 queries in `labels\queries.jsonl`, one per line, a third
each in Nepali, English and romanised Nepali, about events in the
snapshot window, **before** looking at search results:

```json
{"qid": "q001", "query": "सिन्धुपाल्चोक पहिरोमा कति जनाको मृत्यु भयो?", "lang": "ne"}
{"qid": "q002", "query": "petrol ko mulya kati badhyo", "lang": "rom"}
{"qid": "q003", "query": "Who won the by-election in Ilam?", "lang": "en"}
```

```powershell
spashta --db data\eval.db dataset index snapshots\v1
spashta --db data\eval.db qrels pool labels\queries.jsonl -o labels\pool.jsonl --depth 10
spashta dataset labeltool snapshots\v1 --pool labels\pool.jsonl -o labels\judge.html
# judge, export, then (the second annotator judges at least 200 of the same pool):
spashta qrels build labels\queries.jsonl labels\labels-qrels-AM.jsonl --pool labels\pool.jsonl -o labels\questions.json
```

`qrels build` prints how many relevant articles each method found alone:
if one method found most of them alone, say so in the write-up.

### 8. Release on Hugging Face

```powershell
spashta dataset release snapshots\v1 release --labels labels\gold.jsonl --pairs labels\gold_pairs.jsonl --queries labels\questions.json --sample labels\sample.jsonl
spashta dataset card release --agreement results\agreement.json -o release\README.md
# fill the TODO markers in release\README.md, then:
huggingface-cli upload arnabmanandhar/spashta-news-integrity release . --repo-type dataset
```

`release` refuses to write anything that contains article text. Test the
round trip before publishing: `spashta dataset rehydrate release
rehydrated --limit 50`.

## Phase 2: measure

```powershell
# 9. Retrieval comparison, by query language, with intervals and significance
spashta --db data\eval.db eval labels\questions.json --compare -k 10 --out results\compare.json
spashta --db data\eval.db eval labels\questions.json -k 10 --out results\hybrid.json

# 10. Cross-language ablation
spashta --db data\eval.db eval labels\questions.json --ablate-xling -k 10 --out results\ablation.json

# 11. Signals against the labels; suggested thresholds
spashta eval --gold labels\gold.jsonl --pairs labels\gold_pairs.jsonl --snapshot snapshots\v1 --out results\signals.json --write-thresholds results\thresholds.json
```

If you adopt the suggested thresholds, copy them into
`spashta\data\thresholds.json`, re-run step 11, and report the numbers
at the thresholds you ship. Be clear that thresholds tuned on the same
labels are optimistic; the cross-validated F1 is the honest estimate.

```powershell
# 12. Failure cases for docs/ERROR_ANALYSIS.md
spashta dataset errors snapshots\v1 labels\gold.jsonl --retrieval results\hybrid.json -o results\error_candidates.md

# 13. LLM review, two providers, ~50 articles (keys in the environment, never in a file you commit)
$env:GEMINI_API_KEY = "…"; $env:GROQ_API_KEY = "…"
spashta --db data\eval.db dataset llm-compare snapshots\v1 labels\gold.jsonl --providers gemini:gemini-2.5-flash groq:openai/gpt-oss-120b --n 50 --price "gemini:gemini-2.5-flash=0.30/2.50" --out results\llm.json

# 14. Speed and memory at real size
python benchmarks\bench_retrieval.py --snapshot snapshots\v1 --queries labels\questions.json --embedder e5-small --json > results\bench.json

# All tables, and the resume line with your numbers
spashta results --manifest snapshots\v1\manifest.json --compare results\compare.json --ablation results\ablation.json --signals results\signals.json --agreement results\agreement.json --llm results\llm.json --bench results\bench.json -o results\RESULTS.md
```

Free-tier prices are 0 to you; `--price` reports what the same calls
would cost on the paid tier (check the provider's current price page).

## Phase 3: ship

See `docs/DEPLOY.md` (Hugging Face Space, free) and `docs/DEPLOY_VPS.md`
(EC2 or any VPS: Docker Compose with HTTPS, a systemd ingest timer and
nightly database backups). Reports get permanent links at `/r/<id>`.

For user feedback, keep `docs/FEEDBACK_LOG.md`: who tried it (role, not
name, unless they agree), on which stories, what they found useful, what
was wrong, and what you changed.

## Phase 4: tell the story

`results\RESULTS.md` has every table for the README, the dataset card
and a blog post, and ends with the resume line filled in with your
numbers. Report the low-kappa signals and the failure analysis alongside
the wins.
