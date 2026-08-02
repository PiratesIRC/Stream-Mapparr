# Security

## Reporting a vulnerability

Please report security issues privately using GitHub's
[private vulnerability reporting](https://github.com/PiratesIRC/Stream-Mapparr/security/advisories/new)
rather than opening a public issue.

Include what you observed, the plugin version from the settings page, and the
steps to reproduce it. **Do not include your provider credentials, stream URLs,
or M3U account names**: a stream URL carries your username and password in its
path, and an M3U account name is often your provider's hostname.

## What this plugin has access to

Stream-Mapparr runs inside Dispatcharr's Django backend, so it can read and
write Dispatcharr's database directly. In particular it reads every channel,
stream and M3U account, and it creates and deletes channel-to-stream
assignments. It does not make outbound network requests except when the
throughput probe is used, which downloads briefly from a stream URL you have
already configured.

## Things worth knowing before you report

- **Reports emailed through Newsflasharr are built specifically for sending.**
  They never contain M3U source names, stream URLs or server addresses. The CSV
  exports in `/data/exports` do contain source names, and are not emailed.
- **Match & Assign replaces a channel's whole stream list.** Run Preview Changes
  first; it writes a report and changes nothing.
- **A bug report written by the Report a Bug action masks known secret settings**
  before writing the file, because that file is meant to be pasted publicly.

## Supported versions

The latest release is the supported one. Fixes are made on `main` and shipped in
the next release rather than backported.
