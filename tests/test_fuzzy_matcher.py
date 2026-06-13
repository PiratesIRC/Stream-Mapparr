"""Regression tests for fuzzy_matcher.py.

Most cases are derived directly from documented bugs in .wolf/buglog.json and the
BUG_REPORT_*.md files — each one locks in a fix that was shipped at least once.
fuzzy_matcher has no Django dependency, so these run with just pytest (+ rapidfuzz
to exercise the fast path).
"""

import importlib

import pytest


# --------------------------------------------------------------------------- #
# normalize_name — tag stripping
# --------------------------------------------------------------------------- #

def test_quality_tag_stripped(matcher):
    m = matcher()
    assert m.normalize_name("CNN HD").upper().strip() == "CNN"
    assert "4K" not in m.normalize_name("CNN 4K").upper()
    assert "FHD" not in m.normalize_name("ESPN [FHD]").upper()


def test_itv1_and_itv_space_1_normalize_identically(matcher):
    """'ITV1' and 'ITV 1' must collapse to the same form (digit-spacing fix)."""
    m = matcher()
    assert m.normalize_name("ITV1") == m.normalize_name("ITV 1")


def test_regional_west_stripped_when_enabled(matcher):
    """BUGFIX_Regional_Tags_West_Missing: 'West' must be removed under strip_all."""
    m = matcher()
    assert "WEST" not in m.normalize_name("FX West", ignore_regional=True).upper()


def test_regional_west_kept_when_disabled(matcher):
    """Keep-Regional handling must preserve 'West' so East/West stay distinct."""
    m = matcher()
    assert "WEST" in m.normalize_name("FX West", ignore_regional=False).upper()


def test_unicode_user_ignore_tag_does_not_crash(matcher):
    """BUG_REPORT_Custom_Ignore_Tags_Unicode: box-drawing tags broke \\b word boundaries."""
    m = matcher()
    out = m.normalize_name("Sport ┃NLZIET┃", user_ignored_tags=["┃NLZIET┃"])
    assert "NLZIET" not in out
    assert "Sport" in out


def test_word_boundary_tag_does_not_strip_substring(matcher):
    """'East' as an ignore tag must not nuke the 'east' inside 'Feast'."""
    m = matcher()
    out = m.normalize_name("Feast", user_ignored_tags=["East"], ignore_regional=False)
    assert "Feast" in out


# --------------------------------------------------------------------------- #
# calculate_similarity
# --------------------------------------------------------------------------- #

def test_similarity_identical_and_empty(matcher):
    m = matcher()
    assert m.calculate_similarity("cnn", "cnn") == 1.0
    assert m.calculate_similarity("", "cnn") == 0.0
    assert m.calculate_similarity("cnn", "") == 0.0


def test_similarity_threshold_early_out(matcher):
    """When lengths differ too much to ever meet the threshold, return 0.0."""
    m = matcher()
    assert m.calculate_similarity("a", "abcdefghij", threshold=0.9) == 0.0


def test_rapidfuzz_and_pure_python_agree(fuzzy_module, matcher):
    """The optional C path and the pure-Python fallback must return the same score.

    bug-026 fix: both now use 1 - distance / max(len). A divergence here would
    make results depend on whether rapidfuzz is installed. Skips if rapidfuzz
    isn't present (nothing to compare against).
    """
    if not fuzzy_module._USE_RAPIDFUZZ:
        pytest.skip("rapidfuzz not installed; only one path available")

    pairs = [("fox sports 1", "fox sports 2"),
             ("cnn", "cnn hd"),
             ("discovery channel", "discovery"),
             ("bbc one", "bbc two")]
    m = matcher()

    fast = [m.calculate_similarity(a, b) for a, b in pairs]
    fuzzy_module._USE_RAPIDFUZZ = False
    try:
        slow = [m.calculate_similarity(a, b) for a, b in pairs]
    finally:
        fuzzy_module._USE_RAPIDFUZZ = True

    for (a, b), f, s in zip(pairs, fast, slow):
        assert f == pytest.approx(s, abs=1e-9), f"path divergence on {a!r} vs {b!r}: {f} != {s}"


# --------------------------------------------------------------------------- #
# process_string_for_matching
# --------------------------------------------------------------------------- #

def test_token_sort_is_order_insensitive(matcher):
    m = matcher()
    assert m.process_string_for_matching("Sports Fox") == m.process_string_for_matching("Fox Sports")


def test_accents_folded(matcher):
    m = matcher()
    assert m.process_string_for_matching("Canalé") == m.process_string_for_matching("Canale")


# --------------------------------------------------------------------------- #
# extract_callsign
# --------------------------------------------------------------------------- #

def test_callsign_from_parenthetical_suffix(matcher):
    """Priority-1 regex captures the base callsign stem; the loader also stores the
    '-TV' form, so resolving by either key works. Base form is what's returned."""
    m = matcher()
    assert m.extract_callsign("WABC New York (WABC-TV)") == "WABC"


def test_regional_word_not_mistaken_for_callsign(matcher):
    """'(WEST)' looks like a K/W callsign but must be rejected as a false positive."""
    m = matcher()
    assert m.extract_callsign("Some Channel (WEST)") != "WEST"


# --------------------------------------------------------------------------- #
# Numeric-sibling guard (bug-021: Fox Sports 1 vs Fox Sports 2)
# --------------------------------------------------------------------------- #

def test_numeric_siblings_do_not_false_match(matcher):
    """At threshold 95, 'Fox Sports 2' must NOT match candidate 'Fox Sports 1'."""
    m = matcher(threshold=95)
    name, score, mtype = m.fuzzy_match("Fox Sports 2", ["Fox Sports 1"])
    assert name is None and score == 0


def test_numeric_sibling_correct_match_still_works(matcher):
    """The guard must not block the correct same-number match."""
    m = matcher(threshold=95)
    name, score, _ = m.fuzzy_match("Fox Sports 1", ["Fox Sports 1", "Fox Sports 2"])
    assert name == "Fox Sports 1"
    assert score == 100


def test_find_best_match_respects_numeric_guard(matcher):
    m = matcher(threshold=95)
    name, score = m.find_best_match("ESPN 2", ["ESPN 3"])
    assert name is None and score == 0


# --------------------------------------------------------------------------- #
# _expand_zones
# --------------------------------------------------------------------------- #

def test_expand_zones_emits_variants_not_base(matcher):
    """A zoned premium channel yields 'FX East'/'FX West' but NOT bare 'FX'."""
    m = matcher()
    names = [c["channel_name"] for c in m._expand_zones(
        {"channel_name": "FX", "type": "premium/cable/national", "zones": ["East", "West"]})]
    assert names == ["FX East", "FX West"]
    assert "FX" not in names


def test_expand_zones_passthrough_without_zones(matcher):
    m = matcher()
    out = list(m._expand_zones({"channel_name": "CNN", "type": "premium/cable/national"}))
    assert len(out) == 1 and out[0]["channel_name"] == "CNN"


def test_expand_zones_malformed_zones_treated_as_unzoned(matcher):
    """A non-list 'zones' must degrade gracefully, not raise."""
    m = matcher()
    out = list(m._expand_zones({"channel_name": "TNT", "type": "premium", "zones": "East"}))
    assert len(out) == 1 and out[0]["channel_name"] == "TNT"
    assert "zones" not in out[0]


# --------------------------------------------------------------------------- #
# Stylized-Unicode decoration stripping (WeatherNation fix).
# Markers documented by code point so they stay unambiguous:
#   RAW = superscript R A W (U+1D3F U+1D2C U+1D42)
#   HD  = superscript H D   (U+1D34 U+1D30)
#   FHD = small-cap  F H D  (U+A730 U+029C U+1D05)
#   FISH = FISHEYE bullet   (U+25C9)
#   FPS60 = superscript "60fps" (U+2076 U+2070 U+1DA0 U+1D56 U+02E2)
#   ARABIC/CYRILLIC = real non-Latin words (must be preserved)
# --------------------------------------------------------------------------- #
RAW = "ᴿᴬᵂ"
HD = "ᴴᴰ"
FHD = "ꜰʜᴅ"
FISH = "◉"
FPS60 = "⁶⁰ᶠᵖˢ"
ARABIC = "الم"
CYRILLIC = "Россия"


def test_is_decorative_char_classifies_markers(fuzzy_module):
    f = fuzzy_module._is_decorative_char
    assert f("ᴿ") is True   # superscript modifier-letter R
    assert f("ꜰ") is True   # Latin small capital F
    assert f("◉") is True   # FISHEYE bullet (curated)
    assert f("⁶") is True   # superscript six


def test_is_decorative_char_keeps_real_letters(fuzzy_module):
    f = fuzzy_module._is_decorative_char
    assert f("G") is False
    assert f("4") is False
    assert f("я") is False   # Cyrillic small ya
    assert f("ا") is False   # Arabic alef


def test_strip_stylized_tokens_drops_superscript_token(fuzzy_module):
    assert fuzzy_module._strip_stylized_tokens("WEATHERNATION " + RAW) == "WEATHERNATION"


def test_strip_stylized_tokens_drops_punct_glued_ornament(fuzzy_module):
    assert fuzzy_module._strip_stylized_tokens(FISH + ": CNN") == "CNN"


def test_strip_stylized_tokens_keeps_ascii_tier_word(fuzzy_module):
    assert fuzzy_module._strip_stylized_tokens("Gold " + RAW) == "Gold"


def test_strip_stylized_tokens_is_non_latin_safe(fuzzy_module):
    # glued Arabic + superscript HD: Arabic token KEPT; NFKD folds the superscript HD to "HD".
    assert fuzzy_module._strip_stylized_tokens(ARABIC + HD) == ARABIC + "HD"


def test_strip_stylized_tokens_ascii_fast_path(fuzzy_module):
    assert fuzzy_module._strip_stylized_tokens("Fox Sports 1") == "Fox Sports 1"


def test_strip_stylized_tokens_empty_and_all_decoration(fuzzy_module):
    # Contract: empty input and an all-decoration name both collapse to "" cleanly
    # (normalize_name tolerates an empty intermediate result).
    assert fuzzy_module._strip_stylized_tokens("") == ""
    assert fuzzy_module._strip_stylized_tokens(RAW) == ""


def test_normalize_recovers_weathernation(matcher):
    assert matcher().normalize_name("WEATHERNATION " + RAW) == "WEATHERNATION"


def test_normalize_strips_superscript_hd(matcher):
    assert matcher().normalize_name("C-SPAN2 " + HD) == "C SPAN 2"


def test_normalize_strips_small_caps(matcher):
    assert matcher().normalize_name("ESPN " + FHD) == "ESPN"


def test_normalize_strips_punct_glued_bullet(matcher):
    assert matcher().normalize_name(FISH + ": CNN") == "CNN"


def test_normalize_strips_glued_and_standalone_markers(matcher):
    name = "ENTERTAINMENT " + HD + "/" + RAW + " " + FPS60
    assert matcher().normalize_name(name) == "ENTERTAINMENT"


def test_normalize_collision_guard_keeps_ascii_tier_words(matcher):
    m = matcher()
    assert m.normalize_name("Gold") == "Gold"
    assert m.normalize_name("VIP") == "VIP"


def test_normalize_preserves_non_latin(matcher):
    assert matcher().normalize_name(CYRILLIC) == CYRILLIC


def test_normalize_no_regression_plain_ascii(matcher):
    m = matcher()
    assert m.normalize_name("Fox Sports 1") == "Fox Sports 1"
    assert m.normalize_name("Fox Sports 2") == "Fox Sports 2"
    assert m.normalize_name("CNN HD") == "CNN"
    assert m.normalize_name("ITV1") == "ITV 1"


# --------------------------------------------------------------------------- #
# Emoji-as-letter normalization (beIN SP⚽RTS = SPORTS) + emoji decoration.
#   BALL = ⚽ SOCCER BALL (U+26BD) used as the letter 'o' mid-word
#   VS16 = U+FE0F zero-width variation selector (invisible noise)
# --------------------------------------------------------------------------- #
BALL = "⚽"
VS16 = "️"  # U+FE0F


def test_normalize_emoji_ball_midword_to_o(fuzzy_module):
    f = fuzzy_module._normalize_emoji
    assert f("SP" + BALL + "RTS") == "SPoRTS"
    assert f("Sp" + BALL + "rts") == "Sports"


def test_normalize_emoji_edge_ball_stripped_not_mapped(fuzzy_module):
    assert fuzzy_module._normalize_emoji("BE" + BALL) == "BE"


def test_normalize_emoji_strips_zero_width(fuzzy_module):
    assert fuzzy_module._normalize_emoji(VS16 + "HULU") == "HULU"


def test_normalize_emoji_ascii_fast_path(fuzzy_module):
    assert fuzzy_module._normalize_emoji("Fox Sports") == "Fox Sports"


def test_normalize_emoji_leaves_non_emoji_nonascii(fuzzy_module):
    assert fuzzy_module._normalize_emoji("Россия") == "Россия"


def test_normalize_emoji_leading_and_adjacent_balls(fuzzy_module):
    f = fuzzy_module._normalize_emoji
    # leading ball: no ASCII letter before it -> not mapped, stripped as ornament
    assert f(BALL + "SPORTS") == "SPORTS"
    # adjacent balls mid-word: neither is flanked by two ASCII letters -> both stripped
    assert f("A" + BALL + BALL + "B") == "AB"


def test_normalize_name_ball_to_sports(matcher):
    m = matcher()
    assert m.normalize_name("SP" + BALL + "RTS") == "SPoRTS"
    assert m.normalize_name("Sp" + BALL + "rts") == "Sports"


def test_normalize_name_strips_standalone_ball(matcher):
    assert matcher().normalize_name("UEFA CHAMPIONS LEAGUE " + BALL) == "UEFA CHAMPIONS LEAGUE"


def test_normalize_name_strips_music_notes(matcher):
    assert matcher().normalize_name("♬♬♬ MUSIC TV ♬♬♬") == "MUSIC"


def test_normalize_name_emoji_collision_and_non_latin_guards(matcher):
    m = matcher()
    assert m.normalize_name("Gold") == "Gold"
    assert m.normalize_name("Россия") == "Россия"


def test_normalize_name_emoji_no_regression(matcher):
    m = matcher()
    assert m.normalize_name("Fox Sports 1") == "Fox Sports 1"
    assert m.normalize_name("CNN HD") == "CNN"


def test_bein_sports_matches_after_emoji_fix(matcher):
    m = matcher(95)
    match, score = m.find_best_match("beIN Sports", ["### beIN " + "SP" + BALL + "RTS" + " ###"])
    assert match is not None
    assert score == 100
