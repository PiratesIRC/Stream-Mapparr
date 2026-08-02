# Troubleshooting

## An operation seems stuck

Check the Docker logs first. If another operation is running, wait for it to
finish. The operation lock auto-expires after 10 minutes, or you can clear it
with **Clear Operation Lock**.

## No matches found

- Lower **Match Sensitivity** from Strict to Normal or Relaxed.
- For US broadcast channels, use **Match US OTA Only** rather than fuzzy
  matching.
- Check that the correct **Channel Database** is selected.

## A channel is playing a still image or a slate

Some providers serve placeholder streams that report a high resolution while
carrying almost no picture. **Demote Placeholder Streams** ranks these last.
Streams treated this way appear as `placeholder` in the CSV export's `tiers`
column.

If a real stream is being demoted, raise **Placeholder Bitrate Floor (kbps)**.
If a placeholder is getting through, lower it.

## The system is slow while scanning

Set **Rate Limiting** to Medium or High.

## Useful commands

```bash
# Monitor plugin activity
docker logs -f dispatcharr | grep Stream-Mapparr

# Check CSV exports
docker exec dispatcharr ls -lh /data/exports/

# Check plugin files
docker exec dispatcharr ls -la /data/plugins/stream-mapparr/
```

## Reporting a problem

The **Report a Bug or Request a Feature** action writes a ready-to-paste report
to `/config/stream-mapparr/report-a-bug.txt`. It contains the plugin version,
your settings with secrets masked, and the paths of your three most recent CSV
exports.

Please do not paste provider credentials, stream URLs or M3U account names into
a public issue.
