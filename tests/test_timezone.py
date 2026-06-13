"""Timezone is sourced from Dispatcharr's global setting, not a plugin field.

`coerce_timezone` is a pure helper (pytz-validated, UTC fallback).
`_dispatcharr_timezone` reads core.models.CoreSettings; outside Dispatcharr
(core.models not importable in tests) it falls back to UTC.
`_get_system_timezone` must use that source and ignore any stale
settings['timezone'] (the plugin field was removed).
"""


def _bare_plugin(plugin_module):
    return plugin_module.Plugin.__new__(plugin_module.Plugin)


# --------------------------------------------------------------------------- #
# coerce_timezone — validate IANA name, UTC fallback
# --------------------------------------------------------------------------- #

def test_coerce_timezone_valid(plugin_module):
    assert plugin_module.coerce_timezone("US/Eastern") == "US/Eastern"
    assert plugin_module.coerce_timezone("Europe/Oslo") == "Europe/Oslo"


def test_coerce_timezone_strips_whitespace(plugin_module):
    assert plugin_module.coerce_timezone("  UTC  ") == "UTC"


def test_coerce_timezone_blank_none_nonstring(plugin_module):
    assert plugin_module.coerce_timezone("") == "UTC"
    assert plugin_module.coerce_timezone("   ") == "UTC"
    assert plugin_module.coerce_timezone(None) == "UTC"
    assert plugin_module.coerce_timezone(123) == "UTC"


def test_coerce_timezone_invalid_name(plugin_module):
    assert plugin_module.coerce_timezone("Not/AZone") == "UTC"
    assert plugin_module.coerce_timezone("US/Central/Bogus") == "UTC"


# --------------------------------------------------------------------------- #
# _dispatcharr_timezone / _get_system_timezone
# --------------------------------------------------------------------------- #

def test_dispatcharr_timezone_falls_back_to_utc_without_dispatcharr(plugin_module):
    p = _bare_plugin(plugin_module)
    assert p._dispatcharr_timezone() == "UTC"


def test_get_system_timezone_ignores_stale_setting_uses_dispatcharr(plugin_module):
    p = _bare_plugin(plugin_module)
    # A stale plugin-side timezone must NOT override Dispatcharr's source.
    assert p._get_system_timezone({"timezone": "US/Pacific"}) == "UTC"
