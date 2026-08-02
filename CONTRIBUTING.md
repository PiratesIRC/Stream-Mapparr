# Contributing

Thank you for considering a contribution. Bug reports, feature requests and pull
requests are all welcome.

## Before you open an issue

Please do not include provider credentials, stream URLs, M3U account names or
server addresses. A stream URL usually carries a username and password in its
path, and an M3U account name is often the provider's hostname. Both identify
your account to anyone reading the issue.

The **Report a Bug or Request a Feature** action inside the plugin writes a
ready-to-paste report to `/config/stream-mapparr/report-a-bug.txt`. It contains
the plugin version and your settings with secrets masked, which is usually
everything needed to diagnose a problem.

For a security vulnerability, use private reporting instead. See
[SECURITY.md](SECURITY.md).

## Setting up

The plugin runs inside Dispatcharr's Django backend, so there is no standalone
way to run it. Tests are the safety net, and they stub Django so the plugin
imports in isolation.

```bash
pip install -r requirements-dev.txt
python -m pytest -q
```

The suite takes a few seconds. Everything must pass before a pull request is
reviewed.

## Making a change

**Write the test first.** Watch it fail, then make it pass. A test written after
the code passes immediately, which proves it runs but not that it can catch the
bug. Most of the tests in `tests/` exist because something broke once, and each
records what and why.

**Deliberately break a new guard before trusting it.** A test that has never
failed may not be testing anything. If you cannot make it fail by breaking the
code it covers, it is not yet a test.

**Keep the change scoped.** One change per pull request, without unrelated
reformatting mixed in, so a reviewer can see what actually changed.

## Style

- No em dashes in anything a user reads: settings help text, report output,
  rendered pages.
- No contractions in code, comments, docstrings or test names. Write "does not",
  not "doesn't". Possessives are fine.
- Comments should say why, not what. The code already says what.
- Match the surrounding code. Naming, comment density and structure vary by
  file, and consistency inside a file matters more than a global preference.

## Versioning

This plugin uses calver: `1.MAJOR.DDDHHMM`, the UTC day of year plus the UTC
time. Bump it with the script, which keeps `plugin.json` and `plugin.py` in
step:

```bash
python3 Stream-Mapparr/bump_version.py
```

Do not edit either version by hand. A mismatch fails the build.

## What runs on your pull request

- Byte compilation of the plugin source
- A check that `plugin.json` and `plugin.py` agree on the version
- Channel database validation
- The full test suite
- Release zip validation, which checks for path separators that break
  installation on Linux
- A publish audit, which fails if the tree contains a provider hostname, an M3U
  account suffix, a LAN address or a personal path

## Adding a channel database

A channel database is a JSON file named `<CC>_channels.json` in the plugin
directory. It supplies expected channel names for a country:

```json
{
  "country_code": "XX",
  "country_name": "Country Name",
  "version": "1.0",
  "channels": [
    {"channel_name": "Example One", "type": "national", "category": "News"}
  ]
}
```

Only a `type` containing `broadcast` is treated specially: it marks an
over-the-air channel and requires a `callsign`. Every other `type` value is
free-form and matched by name. Run `python scripts/validate_databases.py` before
committing.

A database is selected by the operator in the settings, never by the country
prefix a provider puts on a stream name, so one file per country serves every
provider and none needs duplicating.

## Questions

Ask in [Discussions](https://github.com/PiratesIRC/Stream-Mapparr/issues) or on
[Discord](https://discord.gg/Sp45V5BcxU).
