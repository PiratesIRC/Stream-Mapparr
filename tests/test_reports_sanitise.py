"""The emailed artifacts are BUILT, not selected from what is on disk.

These tests pin the property that makes them safe to send verbatim. Newsflasharr
sends an attachment unredacted, so anything reaching this model can leave the box
by email.

Two measured facts drive the design, both taken from the live installation:

1. Every CSV export in /data/exports appends the M3U account name to each stream
   name, and on a real installation those account names are the provider's hostnames
   (placeholders below: provider.tv and three numbered variants).
2. Bracketed text is usually the market, not a source label. Measured on one
   real installation: 327 bracketed names and NOT ONE of them holds an account
   name. The brackets hold the market, which for an over-the-air station is its
   whole identity: "US: ABC 45 HD [WINSTON-SALEM]" against
   "US: ABC 33/40 HD [BIRMINGHAM]".

So stripping every bracketed group, which an earlier design did, would remove
nothing that leaks and would collapse dozens of distinct stations into one
indistinguishable label.
"""
import pytest

ACCOUNTS = ["custom", "provider.tv", "provider.tv-alt1", "provider.tv-alt2", "provider.tv-alt3"]


# --------------------------------------------------------------------------- #
# Stripping an account label, and only an account label
# --------------------------------------------------------------------------- #

def test_an_exact_account_label_is_stripped_from_a_stream_name():
    from reports import sanitise_stream_label
    assert sanitise_stream_label("SKY NEWS HD [provider.tv-alt1]", ACCOUNTS) == "SKY NEWS HD"


def test_a_bare_stream_name_is_unchanged():
    from reports import sanitise_stream_label
    assert sanitise_stream_label("SKY NEWS HD", ACCOUNTS) == "SKY NEWS HD"


def test_a_market_label_is_preserved():
    """The market is the identity of an over-the-air station. Removing it makes
    the report actively misleading about what matched what."""
    from reports import sanitise_stream_label
    assert sanitise_stream_label(
        "US: ABC 45 HD [WINSTON-SALEM]", ACCOUNTS) == "US: ABC 45 HD [WINSTON-SALEM]"
    assert sanitise_stream_label(
        "US: SPECTRUM SPORTSNET [LA DODGERS]", ACCOUNTS) == "US: SPECTRUM SPORTSNET [LA DODGERS]"


def test_a_market_label_survives_alongside_an_account_label():
    """The labelled form is "<name> [<market>] [<account>]". Only the account
    part may be removed."""
    from reports import sanitise_stream_label
    assert sanitise_stream_label(
        "US: ABC 45 HD [WINSTON-SALEM] [provider.tv-alt2]",
        ACCOUNTS) == "US: ABC 45 HD [WINSTON-SALEM]"


def test_the_longest_matching_account_name_is_stripped_first():
    """provider.tv is a prefix of provider.tv-alt1. Matching the short one first
    would leave a "-alt1]" fragment behind."""
    from reports import sanitise_stream_label
    assert sanitise_stream_label("SKY [provider.tv-alt1]", ACCOUNTS) == "SKY"


def test_an_empty_account_list_strips_nothing():
    """Degrade toward preserving content. The primary defence is that build_model
    is fed RAW stream names which never carried a label at all; this function is
    a backstop for a caller that passes the labelled form by mistake."""
    from reports import sanitise_stream_label
    assert sanitise_stream_label("SKY NEWS HD [provider.tv]", []) == "SKY NEWS HD [provider.tv]"


# --------------------------------------------------------------------------- #
# The model
# --------------------------------------------------------------------------- #

def test_the_model_never_contains_an_m3u_account_name():
    """Asserts on the account names specifically. An earlier draft asserted the
    substring ".tv" never appeared, which was a coincidence of its fixture: this
    installation has a real channel named Cruise1st.TV, so that assertion would
    fail on real data while proving nothing about the actual leak.
    """
    from reports import build_model
    model = build_model(
        [{"channel_name": "Sky News",
          "stream_names": ["SKY NEWS HD [provider.tv-alt1]",
                           "SKY NEWS FHD [provider.tv-alt2]"]}],
        ACCOUNTS, {}, 0)
    blob = repr(model)
    for account in ACCOUNTS:
        if account == "custom":
            continue  # too generic to assert on, and not a hostname
        assert account not in blob


def test_the_model_never_contains_an_ip_address():
    """The Sort export carries provider edge server addresses in its own column.
    This catches one that reaches a name field by any route."""
    from reports import build_model
    model = build_model(
        [{"channel_name": "Sky News 203.0.113.7",
          "stream_names": ["SKY NEWS HD 198.51.100.22"],
          "edge_ips": ["203.0.113.7"]}],
        ACCOUNTS, {}, 0)
    blob = repr(model)
    assert "203.0.113.7" not in blob
    assert "198.51.100.22" not in blob


def test_an_unexpected_extra_key_is_dropped_rather_than_carried_through():
    """Default closed. A key this builder does not know about may hold anything,
    so it is not copied into the model."""
    from reports import build_model
    model = build_model(
        [{"channel_name": "Sky News", "stream_names": [],
          "some_future_column": "http://user:pass@provider.tv/live/1"}],
        ACCOUNTS, {}, 0)
    assert "provider.tv" not in repr(model)


def test_a_joined_string_is_rejected_rather_than_iterated_per_character():
    """The CSV export stores stream_names as "a; b; c". Fed to a loop expecting a
    list, Python iterates the characters and the report renders as single
    letters. Fail loudly instead of producing a report of single letters."""
    from reports import build_model
    with pytest.raises(TypeError):
        build_model([{"channel_name": "Sky News",
                      "stream_names": "SKY NEWS HD; SKY NEWS FHD"}], ACCOUNTS, {}, 0)


def test_the_model_counts_channels_and_carries_the_timestamp():
    from reports import build_model
    model = build_model(
        [{"channel_name": "A", "stream_names": ["x"]},
         {"channel_name": "B", "stream_names": ["y", "z"]}],
        ACCOUNTS, {}, 1785237435.0)
    assert model["channel_count"] == 2
    assert model["generated_ts"] == 1785237435.0
    assert model["entries"][1]["matched"] == 2


def test_an_empty_input_produces_an_empty_model():
    from reports import build_model
    model = build_model([], ACCOUNTS, {}, 0)
    assert model["channel_count"] == 0
    assert model["entries"] == []


# --------------------------------------------------------------------------- #
# Gaps found by a security review of the written code, 2026-08-01
# --------------------------------------------------------------------------- #

def test_a_BARE_account_name_is_stripped_not_only_the_bracketed_form():
    """The account names are literal provider hostnames, so an occurrence
    outside brackets leaks exactly as much as one inside them. An operator
    naming a channel "ESPN backup provider.tv" is enough."""
    from reports import sanitise_stream_label
    assert "provider.tv" not in sanitise_stream_label("ESPN backup provider.tv", ACCOUNTS)
    assert "provider.tv" not in sanitise_stream_label("ESPN (provider.tv)", ACCOUNTS)


def test_account_name_matching_ignores_case():
    """[PROVIDER.TV] is the same hostname and the same leak."""
    from reports import sanitise_stream_label
    assert "PROVIDER" not in sanitise_stream_label("SKY [PROVIDER.TV]", ACCOUNTS).upper()
    assert "PROVIDER" not in sanitise_stream_label("SKY Provider.TV feed", ACCOUNTS).upper()


def test_stripping_a_bare_account_name_leaves_readable_text():
    from reports import sanitise_stream_label
    assert sanitise_stream_label("ESPN backup provider.tv HD", ACCOUNTS) == "ESPN backup HD"


def test_an_ipv6_address_is_scrubbed():
    """The IPv4 pattern does not match an IPv6 address, so an edge server
    address in that form would have shipped unredacted."""
    from reports import build_model
    model = build_model(
        [{"channel_name": "Feed 2001:db8::1",
          "stream_names": ["Backup fe80::1ff:fe23:4567:890a"]}],
        ACCOUNTS, {}, 0)
    blob = repr(model)
    assert "2001:db8::1" not in blob
    assert "fe80::1ff:fe23:4567:890a" not in blob


def test_scrubbing_an_address_does_not_leave_a_double_space():
    from reports import build_model
    model = build_model(
        [{"channel_name": "Backup 203.0.113.5 Feed", "stream_names": []}],
        ACCOUNTS, {}, 0)
    assert model["entries"][0]["channel_name"] == "Backup Feed"


def test_a_channel_name_that_merely_looks_like_a_version_is_untouched():
    """Do not over-scrub. 1.2.3.4 is an address shape, but 4.5 or v1.2 is not."""
    from reports import build_model
    model = build_model(
        [{"channel_name": "Channel 4.5 HD", "stream_names": ["Sky v1.2"]}],
        ACCOUNTS, {}, 0)
    assert model["entries"][0]["channel_name"] == "Channel 4.5 HD"
    assert model["entries"][0]["stream_names"] == ["Sky v1.2"]


def test_an_uppercase_bracketed_account_leaves_no_empty_brackets():
    """Found by mutation testing: with case-sensitive bracket matching, the
    bare-name fallback still removes the hostname but leaves "[]" behind, so the
    leak is closed and the output is ugly. Both matter."""
    from reports import sanitise_stream_label
    assert sanitise_stream_label("SKY [PROVIDER.TV]", ACCOUNTS) == "SKY"
    assert sanitise_stream_label("SKY (Provider.TV)", ACCOUNTS) == "SKY"


def test_repeated_names_are_collapsed_with_a_count():
    """Found on the first real report. The same stream name legitimately exists
    in several M3U accounts, and all of them are matched, so removing the source
    label leaves the report showing what looks like the same entry three times
    with nothing to tell them apart.

    Collapsing to one line with a count keeps the information the label carried,
    which is HOW MANY sources, without naming them.
    """
    from reports import build_model
    model = build_model(
        [{"channel_name": "CBS", "stream_names": [
            "US: CBS 4 HD", "CITY: CBS COLUMBUS",
            "US: CBS 4 HD", "CITY: CBS COLUMBUS",
            "US: CBS 4 HD", "CITY: CBS COLUMBUS"]}],
        ACCOUNTS, {}, 0)
    names = model["entries"][0]["stream_names"]
    assert names == ["US: CBS 4 HD (x3)", "CITY: CBS COLUMBUS (x3)"]


def test_the_matched_count_still_reports_every_stream():
    """The count is what was actually assigned, not the number of distinct
    names, so collapsing the display must not change it."""
    from reports import build_model
    model = build_model(
        [{"channel_name": "CBS", "stream_names": ["A", "B", "A", "B", "A", "B"]}],
        ACCOUNTS, {}, 0)
    assert model["entries"][0]["matched"] == 6


def test_a_single_occurrence_carries_no_count():
    from reports import build_model
    model = build_model(
        [{"channel_name": "CBS", "stream_names": ["A", "B"]}], ACCOUNTS, {}, 0)
    assert model["entries"][0]["stream_names"] == ["A", "B"]


def test_first_appearance_order_is_kept():
    from reports import build_model
    model = build_model(
        [{"channel_name": "CBS", "stream_names": ["Z", "A", "Z"]}], ACCOUNTS, {}, 0)
    assert model["entries"][0]["stream_names"] == ["Z (x2)", "A"]
