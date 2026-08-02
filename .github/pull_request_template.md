## What this changes

<!-- One or two sentences. What behaviour is different after this is merged? -->

## Why

<!-- What problem does it solve? If it fixes a reported issue, link it here. -->

Fixes #

## How it was verified

<!-- Say what you actually ran or observed, not what should happen in theory. -->

- [ ] `python -m pytest -q` passes
- [ ] A new test was written first and watched to fail before the code was written
- [ ] The new test was confirmed to fail when the code it covers is deliberately broken

<!--
If this is a documentation or tooling change with no behaviour change, say so
and tick only what applies.
-->

## Anything a reviewer should know

<!--
Trade-offs, anything deliberately left out, anything you were unsure about.
Uncertainty stated up front is more useful than a confident summary that turns
out to be wrong.
-->

---

Please confirm:

- [ ] No provider credentials, stream URLs, M3U account names or server
      addresses appear in the diff, the description, or any attached output.
      A stream URL usually carries a username and password in its path, and an
      M3U account name is often the provider's hostname.
- [ ] The version was bumped with `python3 Stream-Mapparr/bump_version.py`, or
      this change does not need a version bump.
