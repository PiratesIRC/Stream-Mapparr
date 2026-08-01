"""The emailed artifacts are BUILT, not selected from what is on disk.

These tests pin the property that makes them safe to send verbatim. Newsflasharr
sends an attachment unredacted, so anything reaching this model can leave the box
by email.

Two measured facts drive the design, both taken from the live installation:

1. Every CSV export in /data/exports appends the M3U account name to each stream
   name, and on this box those account names are the provider's hostnames
   (streamq.tv, streamq.tv-bk15, streamq.tv-bk26, streamq.tv-bk29).
2. 327 stream names contain square brackets and NOT ONE of them holds an account
   name. The brackets hold the market, which for an over-the-air station is its
   whole identity: "US: ABC 45 HD [WINSTON-SALEM]" against
   "US: ABC 33/40 HD [BIRMINGHAM]".

So stripping every bracketed group, which an earlier design did, would remove
nothing that leaks and would collapse dozens of distinct stations into one
indistinguishable label.
"""
import pytest

ACCOUNTS = ["custom", "streamq.tv", "streamq.tv-bk15", "streamq.tv-bk26", "streamq.tv-bk29"]


# --------------------------------------------------------------------------- #
# Stripping an account label, and only an account label
# --------------------------------------------------------------------------- #

def test_an_exact_account_label_is_stripped_from_a_stream_name():
    from reports import sanitise_stream_label
    assert sanitise_stream_label("SKY NEWS HD [streamq.tv-bk15]", ACCOUNTS) == "SKY NEWS HD"


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
        "US: ABC 45 HD [WINSTON-SALEM] [streamq.tv-bk26]",
        ACCOUNTS) == "US: ABC 45 HD [WINSTON-SALEM]"


def test_the_longest_matching_account_name_is_stripped_first():
    """streamq.tv is a prefix of streamq.tv-bk15. Matching the short one first
    would leave a "-bk15]" fragment behind."""
    from reports import sanitise_stream_label
    assert sanitise_stream_label("SKY [streamq.tv-bk15]", ACCOUNTS) == "SKY"


def test_an_empty_account_list_strips_nothing():
    """Degrade toward preserving content. The primary defence is that build_model
    is fed RAW stream names which never carried a label at all; this function is
    a backstop for a caller that passes the labelled form by mistake."""
    from reports import sanitise_stream_label
    assert sanitise_stream_label("SKY NEWS HD [streamq.tv]", []) == "SKY NEWS HD [streamq.tv]"


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
          "stream_names": ["SKY NEWS HD [streamq.tv-bk15]",
                           "SKY NEWS FHD [streamq.tv-bk26]"]}],
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
