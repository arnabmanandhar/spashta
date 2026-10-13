# Spashta's scoring rules, in plain words

Every signal gives a **concern** from 0 (no warning signs) to 1 (strong
warning signs), a **confidence** (how much text it had), and the exact
words or numbers that triggered it. Code: `spashta/analysis/signals.py`,
word lists: `spashta/analysis/lexicons.py`, score: `spashta/report.py`.

The cut-offs where a signal's summary changes ("some" and "strong"
concern) live in `spashta/data/thresholds.json`, so they can be
calibrated on labelled articles (`spashta eval --gold`) without touching
code. The values below are the shipped defaults.

Several rules use a "saturating" curve: concern = 1 − e^(−raw/scale). It
starts at 0, reaches about 0.63 when raw equals the scale, and never quite
hits 1, so one extra hit matters less once an article is already flagged.

## 1. Headline consistency (weight 0.25) — `signals.py:180`

Does the story back up its headline?

| Check | Adds to concern |
|---|---|
| Share of headline words (stemmed, Nepali case markers stripped) missing from the body | up to 0.35 |
| Share of headline words missing from the first 3 sentences | up to 0.10 |
| A figure in the headline (e.g. "12 dead") that the body doesn't contain; other headline numbers missing from the body count half | up to 0.35 |
| Headline states a figure as fact that the body hedges ("may", "feared", सम्भवतः) | 0.25 |
| Headline uses sensational words the body never uses | 0.20 |

Capped at 1. Below 0.25: "supports its headline"; below 0.55: "goes partly beyond"; else "not supported".

## 2. Sensationalism (weight 0.20) — `signals.py:105`

raw = 2 × sensational words in the headline
    + sensational words per 100 words of body
    + exclamation marks in the headline (+ 0.25 each in the body, max 10)
    + 0.75 × ALL-CAPS words of 5+ letters in the headline (known acronyms like NEPSE excluded)
    + 1.5 × clickbait structures (curiosity gap "you won't believe", यस्तो छ; listicle "7 reasons", ७ कारण; "share this", सेयर गर्नुहोस्)
    + 0.75 if the headline is a question

concern = saturate(raw, 3). Below 0.25 "measured", below 0.6 "some emotive", else "heavily sensational".

Sensational words (sample): shocking, bombshell, slams, chaos, devastating,
went viral · सनसनी, भण्डाफोर, तहल्का, डरलाग्दो, भाइरल, ठूलो खुलासा.
Nepali words match by prefix, so inflected forms count too.

## 3. Sourcing (weight 0.20) — `signals.py:134`

- Counts named attribution (said, according to, told reporters ·
  बताए, जनाए, अनुसार, विज्ञप्ति, उनले भने) plus direct quotes of 10+ characters.
- Counts anonymous sourcing (sources said, reportedly, allegedly ·
  स्रोतका अनुसार, नाम नबताउने, भनिन्छ, हल्ला).
- concern = 0.6 × (shortfall below 3 attributions per 300 words)
          + 0.4 × (anonymous share of all attributions)

Short texts get low confidence (25% at 0 words, full at 150 words).

## 4. Framing (weight 0.15) — `signals.py:165`

raw = (loaded words + 0.5 × first-person opinion phrases) per 100 words
concern = saturate(raw, 1)

Loaded words (sample): regime, thug, puppet, traitor, so-called, obviously ·
भ्रष्ट, कठपुतली, देशद्रोही, गद्दार, तथाकथित, तानाशाह.
First person: "I think", "we must", मलाई लाग्छ, मेरो विचारमा.

## 5. Corroboration (weight 0.20) — `analysis/corroboration.py`

Needs other articles in the index.
- Same story = shared event type + shared subject + within 3 days.
- Wire copies and same-owner outlets count as one source; opinion pieces don't count.
- If there is no independent coverage, concern is 0.5 and the report says "unverified, not disproven".
- Otherwise concern = 0.4 × (1 − sources/3, floor 0) + 0.6 × share of figures that conflict
  + 0.3 if any conflict + 0.3 × how much more sensational this article is than the median of the others.

## 6. Claims (weight 0.25, optional LLM) — `analysis/llm_review.py`

The model lists factual claims and cites another outlet's passage for each.
concern = 0.8 × contradicted share + 0.3 × unverified share + 0.3 if the headline is unfair.
A citation of a passage the model wasn't given is downgraded to "unverified".

## Overall score — `report.py:89`

Each signal's weight is multiplied by its confidence, the weights are
normalised to sum to 1, and

score = 100 − Σ (normalised weight × concern × 100)

75+ "Few warning signs", 50–74 "Some warning signs", below 50 "Many warning signs".
The report lists the points each signal took off.
