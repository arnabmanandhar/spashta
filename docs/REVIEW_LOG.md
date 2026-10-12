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

## Round 2 (v0.3.0): cross-language and romanised Nepali without a model

**Critique.** Round 1 showed cross-language retrieval was the weakest
slice, and the offline (hashing) embedder cannot match across scripts at
all. Many readers type Nepali in Latin script ("petrol ko mulya badhyo").
Relying only on a downloaded multilingual model means CI, low-resource
servers and offline users get no cross-language matching.

**Change.**
- `xling.py`: a Devanagari romaniser with word-final schwa deletion
  (सिन्धुपाल्चोक → sindhupalchok, पेट्रोल → petrol), a phonetic key that
  collapses spelling variation (aa/a, ee/i, v/w/b, aspirates, sh/s), and a
  bilingual glossary of ~90 news concepts (`data/glossary.json`).
- A third ranking, `xling`, indexes phonetic keys and concept ids in its own
  BM25 and is fused into hybrid search with RRF.
- Contextual chunk embedding: chunks after the first are embedded with the
  headline prepended, so a passage about "the toll" still carries its event
  (a cheap variant of contextual retrieval).
- Five `heldout` questions written after the glossary, using words it does
  not contain, to measure how much of the gain is glossary fitting.

**Result (21 questions, k=5, nDCG@5).**

| Mode | all | xl | rom | heldout |
|---|---|---|---|---|
| lexical | 0.633 | 0.572 | 0.425 | 0.412 |
| dense (hashing) | 0.735 | 0.691 | 0.701 | 0.620 |
| xling | 0.896 | 0.878 | 0.678 | 0.714 |
| hybrid (all three) | 0.847 | 0.821 | 0.819 | **0.769** |

Hybrid vs lexical +0.214 (p < 0.001), hybrid vs dense +0.111 (p = 0.011),
both significant. xling alone scores highest overall, but it was built
knowing these questions; on the held-out questions hybrid is best, which
is the argument for fusing rankings rather than trusting any single one.
The glossary should grow from real query logs, not from this benchmark.

## Round 3 (v0.4.0): counting independent sources, not search hits

**Critique.** Corroboration counted whatever retrieval returned.
Three failure modes in the v0.3 demo: a wire story copied word for word
by a second site counted as a second confirmation; an opinion blog about
landslides counted as coverage of the event; and nothing stopped last
year's landslide in another district (or an LPG story next to a petrol
story) from "corroborating" this one. Meanwhile the Nepali landslide story
did not find its English coverage at all, because the old relatedness
filter used only same-script terms. Credibility research is clear that
*independent* confirmation is what matters; 20 sites echoing one press
release is one source.

**Change.**
- `story.py`: `same_story` requires the same kind of event (from glossary
  event concepts: landslide, increase, exam result...), a shared specific
  subject (a proper name or a named concept such as petrol or UAE), and
  publication within 3 days. Proper names are learned from capitalisation
  in English text and stored as phonetic keys, so the same name written in
  Devanagari is recognised. Every match records its reason.
- `genre`: first-person argument or an opinion/blog URL section marks
  commentary, which is listed but not counted.
- `near_duplicate`: 5-word shingle Jaccard; copies collapse into one source.
- `data/outlet_groups.json`: outlets with one owner (Kantipur Media's
  ekantipur and Kathmandu Post, Nagarik Network, Gorkhapatra Corporation,
  Onlinekhabar's two sites) count once.
- Corroboration now labels each related article independent, copy,
  same-owner or commentary, with the reason it matched.

**Result.** On all 136 article pairs of the sample corpus, same-story
matching agrees with the hand-labelled events: 0 false matches, 0 missed
(opinion pieces excluded). The Nepali landslide report now finds the
English coverage, counts the wire copy once and sets the blog aside:
"Reported by 2 independent sources" instead of the earlier misleading
count.

**Still wrong (next round).** "5" and "8" in the Nepali story are reported
as not found elsewhere, but the English coverage says "five" and "eight".
