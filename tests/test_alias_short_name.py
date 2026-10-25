"""A hand-written alias matches a channel whose name cleans down to one character.

Reported on issue #68: a New Zealand channel named "Three" matched nothing, and
a Custom Aliases entry {"Three": ["Three"]} did not help either.

Measured before this change: normalize_name turns "Three" into "3". Fuzzy
matching skips any name shorter than two characters, which is right for a
similarity score (a one-character name is similar to everything). The alias
lookup accepted the alias "3", but it cleaned each stream name through the same
two-character minimum, so no stream named "Three" could ever equal it.

An alias match is exact equality of the cleaned names, not similarity, so the
minimum is not needed there. It is dropped for alias candidates only; fuzzy
matching keeps it, so a channel with no alias behaves exactly as before.
"""


def _matcher(plugin_module):
    inst = plugin_module.Plugin.__new__(plugin_module.Plugin)
    inst.fuzzy_matcher = None
    inst._alias_map = None
    inst._initialize_fuzzy_matcher(80)
    return inst


CANDIDATES = ["Three", "NZ: Three", "Three HD", "BBC Three", "UK: BBC Three",
              "Three +1", "ThreeNow"]


def _alias_map(inst):
    return inst._build_alias_map({"custom_aliases": '{"Three": ["Three"]}'}, None)


def test_an_alias_matches_streams_whose_name_cleans_to_one_character(plugin_module):
    """The reported case."""
    inst = _matcher(plugin_module)
    hits = inst.fuzzy_matcher.alias_lookup("Three", CANDIDATES, _alias_map(inst))
    assert sorted(hits) == sorted(["Three", "NZ: Three", "Three HD"])


def test_the_short_alias_still_needs_the_whole_cleaned_name_to_be_equal(plugin_module):
    """BBC Three and Three +1 contain the alias but are different channels."""
    inst = _matcher(plugin_module)
    hits = inst.fuzzy_matcher.alias_lookup("Three", CANDIDATES, _alias_map(inst))
    for wrong in ("BBC Three", "UK: BBC Three", "Three +1", "ThreeNow"):
        assert wrong not in hits


def test_the_same_holds_after_the_stream_names_are_precomputed(plugin_module):
    """A real run fills the normalization cache first; the cache must not hide them."""
    inst = _matcher(plugin_module)
    inst.fuzzy_matcher.precompute_normalizations(CANDIDATES)
    hits = inst.fuzzy_matcher.alias_lookup("Three", CANDIDATES, _alias_map(inst))
    assert sorted(hits) == sorted(["Three", "NZ: Three", "Three HD"])


def test_without_an_alias_a_one_character_name_still_matches_nothing(plugin_module):
    """Fuzzy matching keeps its two-character minimum."""
    inst = _matcher(plugin_module)
    inst.fuzzy_matcher.precompute_normalizations(CANDIDATES)
    assert inst.fuzzy_matcher.alias_lookup("Three", CANDIDATES, {}) == []
    assert inst.fuzzy_matcher.fuzzy_match("Three", CANDIDATES)[0] is None
