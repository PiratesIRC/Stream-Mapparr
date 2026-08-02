"""An alias entry works regardless of how its channel name is capitalised.

Reported by a user asking whether aliases could be made case insensitive. Half
of it already was, and half was not, so the report was correct.

Measured before this change:

  The alias VALUES, meaning the alternative names listed for a channel, were
  already compared against stream names case insensitively. An alias written
  "SS Main Event" matched a stream called "UK: SS MAIN EVENT HD" and one called
  "UK: ss main event".

  The alias KEY, meaning the channel name the entry is filed under, had to match
  the Dispatcharr channel name exactly, including capitalisation. An entry filed
  under "sky sports main event" was silently ignored for a channel named
  "Sky Sports Main Event", and the other way round.

Silently is the problem. A mistyped capital produced no error, no log line and
no match, which is indistinguishable from the alias simply not helping.

The lookup is now case insensitive on the key as well. Nothing else changes:
the stored map keeps its original keys so anything that reads it for display
still shows what the user typed.
"""


def _matcher(plugin_module):
    inst = plugin_module.Plugin.__new__(plugin_module.Plugin)
    inst.fuzzy_matcher = None
    inst._alias_map = None
    inst._initialize_fuzzy_matcher(85)
    return inst


CANDIDATES = ["UK: SS MAIN EVENT HD", "UK: ss main event", "Unrelated Channel"]
EXPECTED = ["UK: SS MAIN EVENT HD", "UK: ss main event"]


def test_a_key_typed_in_lower_case_still_matches_a_title_case_channel(plugin_module):
    """The reported case."""
    inst = _matcher(plugin_module)
    amap = inst._build_alias_map(
        {"custom_aliases": '{"sky sports main event": ["SS Main Event"]}'}, "UK")
    hits = inst.fuzzy_matcher.alias_lookup("Sky Sports Main Event", CANDIDATES, amap)
    assert sorted(hits) == sorted(EXPECTED)


def test_a_key_typed_in_title_case_still_matches_a_lower_case_channel(plugin_module):
    inst = _matcher(plugin_module)
    amap = inst._build_alias_map(
        {"custom_aliases": '{"Sky Sports Main Event": ["SS Main Event"]}'}, "UK")
    hits = inst.fuzzy_matcher.alias_lookup("sky sports main event", CANDIDATES, amap)
    assert sorted(hits) == sorted(EXPECTED)


def test_upper_case_on_either_side_works(plugin_module):
    inst = _matcher(plugin_module)
    amap = inst._build_alias_map(
        {"custom_aliases": '{"SKY SPORTS MAIN EVENT": ["SS Main Event"]}'}, "UK")
    hits = inst.fuzzy_matcher.alias_lookup("Sky Sports Main Event", CANDIDATES, amap)
    assert sorted(hits) == sorted(EXPECTED)


def test_surrounding_whitespace_on_the_key_does_not_break_it(plugin_module):
    """A user editing JSON by hand leaves stray spaces. Silently ignoring the
    entry is the same failure this change exists to remove."""
    inst = _matcher(plugin_module)
    amap = inst._build_alias_map(
        {"custom_aliases": '{"  Sky Sports Main Event  ": ["SS Main Event"]}'}, "UK")
    hits = inst.fuzzy_matcher.alias_lookup("Sky Sports Main Event", CANDIDATES, amap)
    assert sorted(hits) == sorted(EXPECTED)


# --------------------------------------------------------------------------- #
# What must not change
# --------------------------------------------------------------------------- #

def test_an_exact_match_still_works(plugin_module):
    """The existing behaviour is the common case and must be untouched."""
    inst = _matcher(plugin_module)
    amap = inst._build_alias_map(
        {"custom_aliases": '{"Sky Sports Main Event": ["SS Main Event"]}'}, "UK")
    hits = inst.fuzzy_matcher.alias_lookup("Sky Sports Main Event", CANDIDATES, amap)
    assert sorted(hits) == sorted(EXPECTED)


def test_a_channel_with_no_alias_entry_still_returns_nothing(plugin_module):
    inst = _matcher(plugin_module)
    amap = inst._build_alias_map(
        {"custom_aliases": '{"Sky Sports Main Event": ["SS Main Event"]}'}, "UK")
    assert inst.fuzzy_matcher.alias_lookup("BBC One", CANDIDATES, amap) == []


def test_an_empty_or_missing_map_is_still_handled(plugin_module):
    inst = _matcher(plugin_module)
    assert inst.fuzzy_matcher.alias_lookup("Anything", CANDIDATES, {}) == []
    assert inst.fuzzy_matcher.alias_lookup("Anything", CANDIDATES, None) == []


def test_no_candidates_is_still_handled(plugin_module):
    inst = _matcher(plugin_module)
    amap = inst._build_alias_map(
        {"custom_aliases": '{"Sky Sports Main Event": ["SS Main Event"]}'}, "UK")
    assert inst.fuzzy_matcher.alias_lookup("Sky Sports Main Event", [], amap) == []


def test_the_alias_values_are_still_matched_case_insensitively(plugin_module):
    """This half already worked. It is locked here so a change to the key
    lookup cannot quietly regress it."""
    inst = _matcher(plugin_module)
    amap = inst._build_alias_map(
        {"custom_aliases": '{"Sky Sports Main Event": ["ss MAIN event"]}'}, "UK")
    hits = inst.fuzzy_matcher.alias_lookup("Sky Sports Main Event", CANDIDATES, amap)
    assert sorted(hits) == sorted(EXPECTED)


def test_two_keys_differing_only_by_case_do_not_lose_each_other(plugin_module):
    """A user may file two entries whose names differ only in capitalisation.
    Neither may silently vanish, and the lookup must still return a result
    rather than raising."""
    inst = _matcher(plugin_module)
    amap = inst._build_alias_map(
        {"custom_aliases":
         '{"Sky Sports Main Event": ["SS Main Event"], '
         '"sky sports main event": ["SS Main Event"]}'}, "UK")
    hits = inst.fuzzy_matcher.alias_lookup("SKY SPORTS MAIN EVENT", CANDIDATES, amap)
    assert sorted(hits) == sorted(EXPECTED)
