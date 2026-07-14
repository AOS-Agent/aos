"""Tests for canonical_name normalization.

All test inputs are FABRICATED. Do not put any real operator canonical_name
values into this file — those live only in the operator's own people.db.
"""
from core.engine.people.intel.normalize import (
    NormalizedName,
    component_variants,
    normalize_canonical_name,
)


# ── component_variants ───────────────────────────────────────────────


def test_component_variants_empty():
    assert component_variants("") == []


def test_component_variants_both_qualify():
    assert component_variants("Alex Kumar") == ["alex", "kumar"]


def test_component_variants_both_too_short():
    # Both tokens are < 4 chars → empty list.
    assert component_variants("Bo Sam") == []


def test_component_variants_three_tokens():
    assert component_variants("Alice Jane Kumar") == ["alice", "jane", "kumar"]


def test_component_variants_dedup_preserves_order():
    # Duplicate token dropped, order preserved.
    assert component_variants("John John Smith") == ["john", "smith"]


def test_component_variants_lowercased():
    out = component_variants("ALICE Kumar")
    assert out == ["alice", "kumar"]


def test_component_variants_custom_min_length():
    # min_length=3 should accept 3-char tokens like "Bob".
    assert component_variants("Bob Smith", min_length=3) == ["bob", "smith"]


# ── Primitives ────────────────────────────────────────────────────────


def test_empty_input():
    r = normalize_canonical_name("")
    assert r.variants == []
    assert r.primary == ""
    assert r.raw == ""


def test_none_input():
    r = normalize_canonical_name(None)
    assert r.variants == []


def test_whitespace_only():
    r = normalize_canonical_name("   \t\n")
    assert r.variants == []


# ── Well-formed names should pass through ────────────────────────────


def test_already_spaced_two_word_name():
    r = normalize_canonical_name("Alex Kumar")
    assert "alex kumar" in r.variants
    assert r.primary == "Alex Kumar"
    assert not r.is_non_latin


def test_already_spaced_three_word_name():
    r = normalize_canonical_name("Alex Jordan Kumar")
    assert "alex jordan kumar" in r.variants
    # Three-word names should also get a first+last variant
    assert "alex kumar" in r.variants
    assert r.primary == "Alex Jordan Kumar"


def test_single_word_name():
    r = normalize_canonical_name("Xavier")
    assert "xavier" in r.variants
    assert r.primary == "Xavier"


def test_single_word_too_short():
    r = normalize_canonical_name("Bo")
    # 2-char names are below the 3-char floor
    assert r.variants == []


# ── CamelCase splitting ──────────────────────────────────────────────


def test_camelcase_two_word():
    r = normalize_canonical_name("FabricatedExample")
    assert "fabricated example" in r.variants
    assert "fabricatedexample" in r.variants
    assert r.primary == "Fabricated Example"


def test_camelcase_three_word():
    r = normalize_canonical_name("OneTwoThree")
    assert "one two three" in r.variants
    assert "onetwothree" in r.variants
    # Three-token names → first+last variant
    assert "one three" in r.variants


def test_camelcase_with_digits_in_middle():
    # "Word3MoreWord" → "Word 3 More Word" → drop the digit → "Word More Word"
    r = normalize_canonical_name("Word3MoreWord")
    assert "word more word" in r.variants


def test_camelcase_acronym_aware():
    r = normalize_canonical_name("XMLParser")
    # "XMLParser" → "XML Parser" (acronym preserved as a unit)
    assert "xml parser" in r.variants


# ── Compound records split on '/' ────────────────────────────────────


def test_slash_compound_two_people():
    r = normalize_canonical_name("NameOne/NameTwo")
    # Both people should produce variants
    assert "name one" in r.variants
    assert "name two" in r.variants


def test_slash_compound_person_plus_org():
    r = normalize_canonical_name("PersonName/OrgName")
    # Both halves normalize independently
    assert "person name" in r.variants
    assert "org name" in r.variants


def test_slash_with_trailing_year():
    r = normalize_canonical_name("NameOne/NameTwoTrip2025")
    assert "name one" in r.variants
    # 'Trip' is a noise word, '2025' is a trailing year — both stripped
    assert "name two" in r.variants
    for v in r.variants:
        assert "2025" not in v
        assert "trip" not in v


# ── Title stripping ──────────────────────────────────────────────────


def test_strip_leading_title_dr():
    r = normalize_canonical_name("Dr Example")
    assert "example" in r.variants
    # "dr example" should NOT be in variants (title stripped)
    assert "dr example" not in r.variants


def test_strip_leading_title_dr_with_period():
    r = normalize_canonical_name("Dr. Example")
    assert "example" in r.variants


def test_strip_leading_title_camelcase():
    # "QariExampleName" → camel-split → "Qari Example Name" → strip "Qari" → "Example Name"
    r = normalize_canonical_name("QariExampleName")
    assert "example name" in r.variants
    # Title alone should not be a variant
    assert "qari" not in r.variants
    # The concatenated form WITHOUT the title
    assert "examplename" in r.variants


def test_strip_multiple_titles():
    r = normalize_canonical_name("Dr Prof Example")
    assert "example" in r.variants


def test_title_only_input():
    r = normalize_canonical_name("Dr")
    assert r.variants == []


# ── Relation suffix stripping ────────────────────────────────────────


def test_strip_trailing_uncle():
    r = normalize_canonical_name("ExampleUncle")
    assert "example" in r.variants
    assert "uncle" not in r.variants


def test_strip_trailing_father():
    r = normalize_canonical_name("ExampleFather")
    assert "example" in r.variants


def test_strip_romanized_urdu_kinship_bhai():
    r = normalize_canonical_name("ExampleBhai")
    assert "example" in r.variants


def test_strip_romanized_urdu_kinship_mamu():
    r = normalize_canonical_name("ExampleMamu")
    assert "example" in r.variants


def test_strip_compound_with_relation_suffixes():
    r = normalize_canonical_name("NameOneUncle/NameTwoFather")
    assert "name one" in r.variants
    assert "name two" in r.variants
    assert "uncle" not in r.variants
    assert "father" not in r.variants


def test_relation_suffix_only():
    r = normalize_canonical_name("Uncle")
    # After stripping, nothing remains → no variants
    assert r.variants == []


# ── Noise word stripping ─────────────────────────────────────────────


def test_strip_event_tag():
    r = normalize_canonical_name("ExampleTrip")
    assert "example" in r.variants
    assert "trip" not in r.variants


def test_strip_year_suffix():
    r = normalize_canonical_name("ExampleName2025")
    assert "example name" in r.variants
    for v in r.variants:
        assert "2025" not in v


def test_strip_year_and_event():
    r = normalize_canonical_name("ExampleTrip2025")
    assert "example" in r.variants
    for v in r.variants:
        assert "2025" not in v
        assert "trip" not in v


# ── Non-Latin scripts (pass through) ──────────────────────────────────


def test_arabic_pass_through():
    # Fabricated Arabic: "example name" (transliterated conceptually)
    arabic = "مثالاسم"
    r = normalize_canonical_name(arabic)
    assert r.is_non_latin is True
    assert r.variants == [arabic.lower()]


def test_arabic_with_trailing_year_strips_year():
    arabic_with_year = "مثالاسم 2025"
    r = normalize_canonical_name(arabic_with_year)
    assert r.is_non_latin is True
    # Year should be stripped even on non-Latin path
    assert all("2025" not in v for v in r.variants)


def test_cjk_pass_through():
    r = normalize_canonical_name("例の名前")
    assert r.is_non_latin is True
    assert len(r.variants) == 1


# ── Variant dedup + 3-char floor ─────────────────────────────────────


def test_variants_deduped():
    r = normalize_canonical_name("Example")
    # "example" should appear only once
    assert r.variants.count("example") == 1


def test_short_variants_dropped():
    r = normalize_canonical_name("Ab")
    assert r.variants == []


def test_first_word_variant_requires_four_chars():
    # "JoBob" → camel-split → "Jo Bob" → primary variants include "jo bob"
    # The first-token-only variant requires length >= 4, so "jo" is not added
    r = normalize_canonical_name("JoBob")
    # Two-word form should be present
    assert "jo bob" in r.variants
    # "jo" (2 chars) should NOT be in variants
    assert "jo" not in r.variants


# ── Realistic compound patterns from plan ────────────────────────────


def test_compound_multi_part_camelcase():
    # e.g., a 4-word camelCase record
    r = normalize_canonical_name("AlphaBetaGammaDelta")
    assert "alpha beta gamma delta" in r.variants
    # Four-word → first+last variant
    assert "alpha delta" in r.variants


def test_title_plus_camelcase_plus_descriptor():
    # "Dr ExampleNameCity" — title stripped, City is NOT in the noise list
    # so it stays — this is intentional, we don't curate real place names
    r = normalize_canonical_name("DrExampleNameCity")
    # Title stripped, the rest comes through
    assert any("example" in v for v in r.variants)


def test_many_relation_suffixes_on_compound():
    r = normalize_canonical_name("OneUncle/TwoAuntie/ThreeBhai")
    # All three names should survive, all relation suffixes stripped
    for name in ("one", "two", "three"):
        assert name in r.variants
    for sfx in ("uncle", "auntie", "bhai"):
        assert sfx not in r.variants


# ── Primary display form ─────────────────────────────────────────────


def test_primary_is_title_cased():
    # CamelCase input → primary is the spaced, title-cased form
    r = normalize_canonical_name("FabricatedExample")
    assert r.primary == "Fabricated Example"


def test_all_lowercase_input_not_split():
    # We cannot know where the word boundary is in all-lowercase input
    # like "fabricatedexample" — the normalizer preserves it as a single
    # token and still title-cases the primary for display.
    r = normalize_canonical_name("fabricatedexample")
    assert "fabricatedexample" in r.variants
    assert r.primary == "Fabricatedexample"


def test_primary_falls_back_to_raw_when_all_noise():
    r = normalize_canonical_name("Uncle")
    # Everything stripped → primary should still be something (raw)
    assert r.primary != ""


def test_primary_for_non_latin():
    r = normalize_canonical_name("مثالاسم")
    # Primary is title-cased first variant (non-Latin: same as input)
    assert r.primary  # non-empty
