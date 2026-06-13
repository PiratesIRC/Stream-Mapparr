"""Audit the alias table for the bare-short-token guard (Lineuparr lesson).

A bare short token as an alias VALUE (e.g. "UP", "GET", "GAC", "great")
normalizes to a 2-3 char string that exact-matches unrelated short streams.
Such tokens are forbidden as values; the canonical name carries the alias.
"""
import importlib.util
import sys
from pathlib import Path

SM = Path(__file__).resolve().parents[1] / "Stream-Mapparr"


def _load_aliases():
    spec = importlib.util.spec_from_file_location("sm_aliases", SM / "aliases.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["sm_aliases"] = mod
    spec.loader.exec_module(mod)
    return mod


FORBIDDEN_BARE = {"up", "get", "gac", "great"}


def test_no_forbidden_bare_token_values():
    mod = _load_aliases()
    offenders = []
    for key, variants in mod.CHANNEL_ALIASES.items():
        for v in variants:
            norm = "".join(ch for ch in v.lower() if ch.isalnum())
            if norm in FORBIDDEN_BARE:
                offenders.append((key, v))
    assert offenders == [], f"forbidden bare-token alias values: {offenders}"


def test_tables_are_dicts_of_string_lists():
    mod = _load_aliases()
    assert isinstance(mod.CHANNEL_ALIASES, dict)
    assert isinstance(mod.COUNTRY_ALIASES, dict)
    for key, variants in mod.CHANNEL_ALIASES.items():
        assert isinstance(key, str) and key
        assert isinstance(variants, list) and all(isinstance(v, str) for v in variants)
