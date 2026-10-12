"""Every labelled-bad sample must score worse than every labelled-good one,
signal by signal. Small, synthetic and strict: a regression in a lexicon
or a weight shows up here first."""

import json

import pytest

from spashta.analysis.signals import framing, headline_consistency, sensationalism, sourcing
from spashta.report import analyze
from spashta.samples import DATA, sample_articles

LABELS = {a["url"]: a["labels"] for a in json.loads((DATA / "sample_articles.json").read_text(encoding="utf-8"))["articles"]}
ARTICLES = sample_articles()


@pytest.mark.parametrize("signal,label", [(sensationalism, "sensational"), (framing, "loaded"), (sourcing, "poorly_sourced")])
def test_labelled_samples_separate(signal, label):
    pos = [signal(a).concern for a in ARTICLES if LABELS[a.url][label]]
    neg = [signal(a).concern for a in ARTICLES if not LABELS[a.url][label]]
    assert min(pos) > max(neg), (label, sorted(pos), sorted(neg))


def test_no_acronym_shouting_false_positives():
    for a in ARTICLES:
        if not LABELS[a.url]["sensational"]:
            assert not any(e.startswith("capitals") for e in sensationalism(a).evidence), a.title


def test_short_text_lowers_confidence_not_concern():
    short = next(a for a in ARTICLES if a.url.endswith("50-janako-mrityu"))
    s = sourcing(short)
    assert s.confidence < 1 and s.concern > 0.5


def test_headline_dropping_uncertainty_is_flagged():
    from spashta.models import Article
    a = Article(url="text:", title="50 killed in landslide", lang="en",
                body="Officials fear 50 people may have died in the landslide, though only 12 deaths are confirmed so far.")
    s = headline_consistency(a)
    assert s.details["dropped_hedge"] and s.concern >= 0.25


def test_scores_order(sample_index):
    good = [analyze(a, sample_index).score for a in ARTICLES if not any(LABELS[a.url].values())]
    bad = [analyze(a, sample_index).score for a in ARTICLES if LABELS[a.url]["sensational"]]
    assert min(good) > max(bad)
