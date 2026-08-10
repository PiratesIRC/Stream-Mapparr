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

One report sends ONE email, carrying one attachment. That is not a choice the
plugin makes: a notification carries a single attachment, so both files on one
message is not something it can express. Choosing both writes both files and
emails the HTML page, which holds everything the CSV does and reads on a phone.
Choosing CSV emails the CSV instead.

Earlier versions emitted one notification per file, so choosing both produced
two separate emails per run.

Both files are written to `/data/stream_mapparr_reports` either way, and the
email names the one it did not attach so it is not mistaken for missing.

## The Report a Bug button can email too

When notifications are enabled, pressing **Report a Bug or Request a Feature**
also sends the report, respecting **Email A Report After** so never means never.

The report text rides in the message body rather than as an attachment, because
only `.html`, `.htm` and `.csv` files can be attached and a bug report is a text
file. The single attachment slot carries a freshly sanitised copy of your newest
CSV export.

It is never the export itself. Every file in `/data/exports` labels each stream
with its M3U source, and a source name is commonly your provider's hostname. If
the account names cannot be read, no CSV is attached at all rather than an
unsanitised one being sent.

The written file masks your M3U source list for the same reason, because that
file exists to be pasted into a public issue.

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
