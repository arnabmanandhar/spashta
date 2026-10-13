# Annotation guide (v2)

Every accuracy number Spashta reports should come from real articles
labelled by people. This guide defines the labels, gives anchored
examples, and fixes the procedure, so a second annotator can reproduce
them and the agreement scores mean something.

**Version log.** v2 (this version): five signal labels, a pilot round,
and a double-annotated subset for agreement. After the pilot, record
every change below under "Revisions" with the date and the reason.

## Procedure

1. **Snapshot.** Freeze the corpus (`spashta dataset freeze`). Labels
   refer to the snapshot text by character offsets, so never label text
   from the live site.
2. **Sample.** `spashta dataset sample` draws ~400 articles across
   language, topic and outlet, with stories covered by several outlets
   drawn more often. It marks 30 pilot articles and 100 more for the
   second annotator.
3. **Pilot.** Label the 30 pilot articles yourself
   (`spashta dataset labeltool SNAP sample.jsonl --batch pilot`). Write in
   the notes box every time the guide didn't tell you what to do. Then
   revise this guide (add an example or a rule for each ambiguity) and
   record the revision. Don't reuse pilot labels as gold unless you
   re-label them with the revised guide.
4. **Main round.** Label all remaining articles with the revised guide.
5. **Second annotator.** Give a second person (a classmate, a journalism
   student) this guide, a 20-minute walkthrough on 5 pilot articles, and
   the page built with `--double`. They label independently: no
   discussion of individual articles until both are done.
6. **Agreement.** `spashta dataset agreement SNAP yours.jsonl theirs.jsonl`
   reports Cohen's kappa per signal with a 95% interval. Report every
   signal's kappa, including the low ones: a low kappa means the signal
   is subjective as defined, which is a finding, not a failure.
7. **Adjudicate.** For articles where you disagree, decide together (or
   with a third person) and write only the decided fields to an
   adjudication file. `spashta dataset gold` merges it over your labels.
8. **Pairs and queries.** Label same-event pairs
   (`dataset labeltool --pairs`) and judge pooled retrieval results
   (`qrels pool`, then `dataset labeltool --pool`) the same way; the
   second annotator does at least 100 pairs and 200 judgments.

Label one article in one sitting, headline last: read the story first,
then decide whether the headline is fair to it.

## Article labels

### Headline consistency: consistent / partly / inconsistent

Does the story support what the headline says?

| Label | Rule | Example |
|---|---|---|
| consistent | Every claim in the headline is stated in the story, with the same certainty. | "Landslide kills 12 in Sindhupalchok" over a story reporting 12 deaths there. |
| partly | The headline overstates or simplifies: drops a hedge the story keeps ("may", "आशंका"), rounds a figure up, picks the most dramatic of several figures, or adds a detail the story only implies. | "पहिरोमा २० जनाको मृत्यु" when the story says "२० जनाको मृत्युको आशंका" (feared dead). |
| inconsistent | The headline claims something the story doesn't support or contradicts, or its main claim isn't in the story at all. | "Minister resigns" over a story saying opposition parties demanded the minister resign. |

Rules: judge against the story, not against what you know from
elsewhere. A question headline ("Is petrol getting dearer?") is
consistent if the story answers it as the headline implies.

### Sensationalism: 0 / 1 / 2

How much is the wording built to provoke rather than inform? Judge the
wording, not the topic: a disaster story told plainly is 0.

| Level | Rule | English anchor | Nepali anchor |
|---|---|---|---|
| 0 measured | Plain, informational. Strong words only when quoting someone or when they are literally accurate ("deadly" for a flood that killed people). | "Petrol price up Rs 5 a litre from Sunday, NOC says" | "आइतबारदेखि पेट्रोलको मूल्य लिटरमा ५ रुपैयाँ बढ्ने" |
| 1 some | Facts lead, but there is some emotive or clickbait wording: one or two charged adjectives, a teasing or question headline, an exclamation mark. | "Shocking hike: petrol now Rs 5 dearer" | "पेट्रोलमा फेरि ठूलो झट्का, लिटरमा ५ रुपैयाँ बढ्यो" |
| 2 heavy | The wording is built to provoke: shock words throughout, the headline withholds the news ("you won't believe…", "यस्तो छ…"), calls to share, ALL CAPS, emotion before facts. | "YOU WON'T BELIEVE what NOC just did to your wallet!!" | "हेर्नुहोस्, यस्तो भयो पेट्रोलको मूल्य! सबैलाई सेयर गर्नुहोस्" |

### Sourcing: mark each key claim

Select each **key factual claim** (who did what, figures, dates, causes,
accusations: the claims a reader would need to check) and mark how it
is attributed:

- **named**: attributed to a named person, a named organisation or a
  document ("NOC said", "प्रहरीका अनुसार", "according to the CBS report").
  "Police" and "the ministry" count as named organisations.
- **anonymous**: attributed to someone unnamed ("sources said", "a senior
  official who asked not to be named", "स्रोतका अनुसार", "एक अधिकारीले").
- **unattributed**: stated as fact with no source, where the reporter
  could not have witnessed it.

Mark the clause carrying the claim, not the whole paragraph. Skip
background everyone accepts ("Kathmandu is the capital"), the reporter's
direct observation ("the road was blocked when we arrived"), and opinion.
Aim for the 3–8 claims that carry the story.

### Framing: mark loaded language

Select words or short phrases that carry a judgement the article presents
as fact: charged labels ("regime", "puppet", "देशद्रोही", "तथाकथित"),
loaded verbs ("admitted" for "said", "slammed"), and first-person opinion
in a news story ("मलाई लाग्छ"). Don't mark words inside quotation marks
attributed to a source; that is the source's language, not the outlet's.

### Topic, opinion, skip

Correct the topic if it is wrong (politics, economy, disaster, crime,
sports, other). Tick **opinion** for columns, editorials and blogs.
**Skip** when the page is not a news article, the text is broken or cut
off, or it duplicates another article.

## Same-event pairs

Do articles A and B report **the same event**: the same incident,
decision or announcement? Same topic is not enough ("a landslide in
Sindhupalchok" and "a landslide in Myagdi" on the same day are
different events). A follow-up reporting a new development of the same
incident counts as the same event. Answer yes / no / unsure.

If yes, list **conflicting figures**: anything both report with
different values (deaths 12 vs 15, Rs 5 vs Rs 7, Sunday vs Monday), as
what / value in A / value in B. Rounding ("about 300" vs "312") is not a
conflict.

## Retrieval relevance (pooled judgments)

For a query and one retrieved article: **2** the article is about what
the query asks; **1** partly (mentions it, or reports a closely related
event); **0** not relevant. Judge the article whatever its language: a
Nepali article answers an English query if it is about the same thing.
You don't see which method retrieved it.

## Format

Labels are JSONL, one record per article, pair or judgment; see
`spashta/dataset/labels.py` for the fields. Offsets are code-point
offsets into the snapshot body, end exclusive. The labelling page writes
this format; `spashta dataset check SNAP labels.jsonl` validates it.

## Revisions

| Date | Change | Why (pilot article or note) |
|---|---|---|
| | | |
