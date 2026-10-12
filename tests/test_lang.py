from spashta import lang as L


def test_normalize_folds_digits_and_joiners():
    assert L.normalize("२०८३ साल") == "2083 साल"
    assert "‍" not in L.normalize("पुर्‍याइएको")


def test_detect_language():
    assert L.detect_language("सिन्धुपाल्चोकमा पहिरो गएको छ र प्रहरीले उद्धार गरेको छ।") == "ne"
    assert L.detect_language("A landslide killed twelve people, police said.") == "en"
    assert L.detect_language("NOC ले petrol को मूल्य बढायो and people are angry about it") == "mixed"
    assert L.detect_language("यह एक परीक्षण है और इसमें कुछ नहीं है। वह भी यहाँ है।") == "hi"
    assert L.detect_language("12") == "unknown"


def test_nepali_case_markers_are_stripped():
    assert L.stem("सरकारले") == L.stem("सरकारको") == L.stem("सरकारलाई") == "सरकार"
    assert L.stem("नेताहरूले") == "नेता"
    assert L.stem("आमा") == "आमा"  # too short to strip मा


def test_english_stemmer():
    assert L.stem("prices") == L.stem("price") == L.stem("priced")
    assert L.stem("raised") == L.stem("raising") == L.stem("raise")
    assert L.stem("taxes") == L.stem("tax")
    assert L.stem("bus") == "bus"


def test_numbers_match_across_scripts_and_separators():
    assert L.numbers("१,२०० जना") == {"1200"}
    assert L.numbers("1,20,000 rupees") == {"120000"}
    assert "2.5" in L.numbers("2.5 percent")


def test_sentences_split_on_danda():
    assert len(L.sentences("पहिलो वाक्य। दोस्रो वाक्य। Third one.")) == 3


def test_content_terms_drop_stopwords():
    terms = L.content_terms("सरकारले पनि यो निर्णय गरेको छ and the government said")
    assert "पनि" not in terms and "the" not in terms
    assert "सरकार" in terms and "government" in terms
