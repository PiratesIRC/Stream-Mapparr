# Regex pre-processing

Some providers pad or decorate stream names in ways that defeat fuzzy matching:
decorative glyphs, invisible padding, bouquet prefixes, badges.

**Stream Name Regex Rules** is a general escape hatch. User-supplied regular
expression find and replace rules run on stream names before anything else
touches them, so novel provider junk does not need a plugin release to fix.

## Example

A provider padded stream names with an invisible zero-width space wrapped
around a decorative block glyph: `UK ▎BBC 1 FHD`, where `▎` (U+258E) is flanked
by invisible characters that no eyeball ever sees.

This rule collapses it back to `UK BBC 1 FHD`, which then normalises and matches
`BBC 1` normally:

```json
[["\\s*▎\\s*", " "]]
```

## Where the rules run

```
raw stream name -> [regex rules] -> normalisation / ignore tags -> aliases -> fuzzy match
```

Rules run before normalisation and ignore-tag stripping, and before Custom
Aliases. They apply in the order given in the JSON list, so a later rule sees an
earlier rule's output.

Python regular expression syntax is used. Prefix a pattern with `(?i)` to make
it case-insensitive.

## Two scope limits

**Rules affect matching only.** Stream names in Dispatcharr are never modified.

Quality sorting, zone routing, country restriction and duplicate detection all
read the original, untouched name. That split is deliberate. It means a rule
that strips a country prefix to help matching cannot also blind country
detection, and a rule that strips a tag cannot collapse two genuinely different
failover streams into one duplicate.

**Group labels are out of scope.** Selected Groups and country restriction read
group names literally. Rules only ever touch stream names, never group labels or
channel names.

## Seeing what a rule does

**Validate Settings** reports a one-line summary in the form `N ok, M rejected`,
with the full per-rule detail in the logs.

**Test Regex Rules** shows real before and after samples against every stream
currently loaded, without changing anything. Invisible characters are escaped in
that output so they are actually visible.

## Guardrails

A rule is rejected up front if it contains a nested unbounded quantifier or
alternation shape known to backtrack exponentially, such as `(a+)+`.

At run time, three further limits apply:

- Names over 500 characters skip regex pre-processing entirely.
- A rule chain that grows a name past four times its original length is reverted
  and stopped.
- An entire regex pass is capped at 5 seconds cumulative.

A bad rule therefore degrades gracefully, and is reported, rather than freezing a
worker.
