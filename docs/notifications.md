# Notifications and emailed reports

Stream-Mapparr sends notifications through **Newsflasharr**, the central
notification plugin, rather than posting to a webhook of its own. Newsflasharr
owns the email settings and decides what goes where, so one place configures
notifications for every plugin that uses it.

## Turning it on

1. Enable **Send notifications to Newsflasharr**.
2. Choose when a report is emailed with **Email A Report After**: never,
   scheduled runs only, or every run that produces one.

**Email Report Now** builds and queues one on demand.

## Which files are sent

**Email Report Format** chooses between the HTML page, the CSV, or both.

A notification carries one attachment, so choosing both sends two separate
emails per run.

Both files are written to `/data/stream_mapparr_reports` either way. The setting
only decides which of them are emailed.

## These are not the CSV exports

The emailed files are built specifically for sending. They are not the CSV
exports in `/data/exports`.

That distinction matters. The exports label every stream with its M3U source
name, which on a real installation is your provider's hostname, and an
attachment is sent verbatim and unredacted. The emailed files never contain a
source name, a stream URL or a server address.

## If Newsflasharr is missing

Newsflasharr does not have to be installed. With it absent or disabled, nothing
is sent and nothing fails.

If it is installed but cannot actually deliver, for example because no routing
rule sends this plugin's reports to email, then **Validate Settings** says so and
**Email Report Now** refuses rather than building a report nobody receives.
