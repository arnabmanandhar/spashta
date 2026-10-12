from spashta import xling
from spashta.samples import sample_articles


def test_romanize_common_news_words():
    assert xling.romanize("सिन्धुपाल्चोक") == "sindhupalchok"
    assert xling.romanize("पेट्रोल") == "petrol"
    assert xling.romanize("मूल्य") == "mulya"  # conjunct keeps its final vowel
    assert xling.romanize("घर") == "ghar"  # final schwa dropped
    assert xling.romanize("म्याग्दी") == "myagdi"


def test_phonetic_key_meets_romanised_spellings():
    k = xling.phonetic_key
    assert k(xling.romanize("बढ्यो")) == k("badhyo") == k("badyo")
    assert k(xling.romanize("क्रिकेट")) == k("cricket")
    assert k("Kathmaandu") == k("kathmandu")


def test_concepts_bridge_languages():
    assert set(xling.concepts("पहिरोमा १२ जनाको मृत्यु")) == set(xling.concepts("Landslide kills 12 people"))
    assert "price" in xling.concepts("पेट्रोलको मूल्य") and "petrol" in xling.concepts("petrol price")


def test_cross_language_search_finds_other_language(sample_index):
    nepali_story = next(a for a in sample_articles() if a.url.endswith("101210"))  # cricket, Nepali
    hits = sample_index.search("Nepal cricket win against UAE", k=5, mode="xling")
    assert nepali_story.id in {h.chunk.article_id for h in hits}


def test_romanised_query_finds_devanagari(sample_index):
    hits = sample_index.search("petrol ko mulya badhyo", k=3, mode="xling")
    assert any(h.chunk.article_id == next(a.id for a in sample_articles() if a.url.endswith("101180")) for h in hits)
