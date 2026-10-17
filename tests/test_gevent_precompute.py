"""Pre-normalizing the stream or EPG name list hands the worker back while it runs.

Under uWSGI's `gevent = 400` with early monkey-patching a plugin thread is a
greenlet in the worker that serves requests and streams, so a loop that never
yields stops everything else in that worker until it finishes. Every matching
action calls precompute_normalizations once over the whole name list first.
Measured 2026-10-17 at this installation's name counts: 7,261 stream names took 0.5 s with no
yield.

The loop now calls time.sleep(0) every PRECOMPUTE_YIELD_EVERY names, which is
gevent.sleep(0) once Dispatcharr has monkey-patched and does nothing anywhere
else. These tests point time.sleep at gevent.sleep for their own duration and
run the pass inside the hub_hold guard (tests/conftest.py).
"""
import random

import pytest
from conftest import load_fuzzy_matcher

fm = load_fuzzy_matcher()


def _names(n=30000):
    rng = random.Random(1)
    words = ["News", "Sports", "Movies", "Kids", "Comedy", "Drama", "Music",
             "Travel", "Food", "History", "Science", "Nature"]
    return [f"{rng.choice(['US', 'UK', 'CA'])}: {rng.choice(words)} {rng.choice(words)} {i} HD"
            for i in range(n)]


@pytest.fixture
def gevent_sleep(monkeypatch):
    """time.sleep as gevent.sleep for this test only, as monkey-patching makes it."""
    import gevent
    monkeypatch.setattr(fm.time, "sleep", gevent.sleep)


def test_precompute_never_holds_the_worker_long(gevent_sleep, hub_hold):
    matcher = fm.FuzzyMatcher(match_threshold=80)
    with hub_hold(max_ms=100):
        matcher.precompute_normalizations(_names())
    assert len(matcher._norm_cache) > 0, "nothing was normalized, so nothing was timed"


def test_precompute_without_the_patch_is_caught(hub_hold):
    """Positive control: unpatched, time.sleep(0) switches nothing and the pass
    holds the worker for its whole run, which the guard must report."""
    matcher = fm.FuzzyMatcher(match_threshold=80)
    with pytest.raises(AssertionError):
        with hub_hold(max_ms=100):
            matcher.precompute_normalizations(_names())


def test_precompute_results_do_not_depend_on_the_yield(gevent_sleep, monkeypatch):
    names = _names(2000)
    patched = fm.FuzzyMatcher(match_threshold=80)
    patched.precompute_normalizations(names)
    monkeypatch.setattr(fm.time, "sleep", lambda _seconds: None)
    plain = fm.FuzzyMatcher(match_threshold=80)
    plain.precompute_normalizations(names)
    assert patched._norm_cache == plain._norm_cache
    assert patched._processed_cache == plain._processed_cache
