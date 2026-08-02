# GTFS-Realtime Scheduled-Service Matching

## Scope and prerequisites

This stage matches persisted Vehicle Position and Trip Update entities to one
specific static GTFS snapshot. It requires complete realtime persistence schema
version 2. Older or missing persistence versions are rejected before entity
matching begins.

The stage produces deterministic lineage and raw schedule-comparison facts. It
does not assign early, on-time, or late categories and does not calculate
punctuality, headway adherence, route aggregates, or reliability indicators.

## Configuration

`config/gtfs_realtime_matching.json` is nonsecret and versioned. It records the
algorithm version, Montréal timezone, required persistence version, bounded
service-date lookback, enabled exact/composite strategies, descriptor
consistency policies, and unsupported frequency policy.

## Static snapshot and service dates

The most recent row in `meta_gtfs_feed` supplies the static ingestion run, feed
version, and service-period boundaries. Their combination forms the snapshot
identifier persisted on every run and result. A requested service date outside
the feed period produces `NO_STATIC_COVERAGE`, not a missing-trip claim.

`TripDescriptor.start_date` is preferred and interpreted as a Montréal service
date. Without it, matching considers only the capture’s Montréal local date and
the configured preceding dates. A date is active only after combining weekday
calendar flags, calendar range, and `calendar_dates` additions/removals.
Exception-only services are supported. Zero active dates are unmatched and
multiple active instances are ambiguous.

## Trip matching

Exact `trip_id` matching is primary. The matched trip must be active and any
provided route, direction, and start time must agree with static values.

When trip ID is absent, the optional fallback requires route, direction, and
start time and succeeds only for one active candidate. Zero and multiple
candidates remain explicit; the implementation never selects the first row or
uses vehicle ID, entity order, fuzzy text, or geographic proximity.

Deleted entities are `NOT_APPLICABLE`. Canceled scheduled trips can retain a
valid static match. Added and unscheduled trips are `NOT_APPLICABLE` by design.
Unknown relationships are `UNSUPPORTED`.

The current static ingestion does not persist `frequencies.txt`. If a
`raw_frequencies` table is present, its trip templates are detected and
classified `FREQUENCY_UNSUPPORTED`; no template is guessed as a unique trip
instance.

## StopTimeUpdate matching

Stops are attempted only after a valid parent trip match. `stop_sequence` is
primary and a simultaneous differing stop ID is a `CONFLICT`. Without sequence,
a stop ID matches only if it occurs once; repeated IDs are `AMBIGUOUS`.
Unknown or missing references are `UNMATCHED`. When the parent is unresolved,
updates remain in source order as `NOT_APPLICABLE`.

## Scheduled time and DST

GTFS times retain their original strings and are parsed to integer seconds from
the service-day origin. `24:00:00`, `25:10:00`, and multi-day offsets remain
valid. Malformed minute/second components fail explicitly.

Scheduled local datetimes use `America/Montreal` through `pytz`. Ambiguous and
nonexistent DST wall times retain service date, original string, seconds, and
offset but receive `DST_AMBIGUOUS` or `DST_NONEXISTENT`; local and UTC values
remain null rather than silently selecting an offset.

## Comparison facts

Stop results retain reported delays, absolute realtime UTC event times,
scheduled local/UTC times, calculated absolute-time deltas, explicit delta
sources, and the difference between reported and calculated delay. Both values
remain available when they disagree. No punctuality label is produced.

## Statuses, methods, and persistence

Statuses include `MATCHED`, `UNMATCHED`, `AMBIGUOUS`, `CONFLICT`,
`NOT_APPLICABLE`, `UNSUPPORTED`, `NO_STATIC_COVERAGE`, and
`INCOMPLETE_LINEAGE`. Methods include exact trip ID, exact ID/date, unique
composite, stop sequence, unique stop ID, no/multiple candidates, added trip,
frequency unsupported, and incomplete persistence.

DuckDB tables are:

- `gtfs_realtime_match_run`;
- `gtfs_realtime_trip_match`;
- `gtfs_realtime_stop_time_match`;
- `gtfs_realtime_match_finding`.

One transaction stores an entire run. Failures roll back all new matching rows
without changing static, realtime, quality, or raw data. Identity is capture,
static snapshot, algorithm version, and configuration version. Repetition
returns the completed run; incompatible source lineage is rejected.

## CLI

```powershell
python .\src\match_gtfs_realtime.py `
    --capture-uuid <CAPTURE_UUID>
```

Use `--warehouse` for an alternate DuckDB file and `--no-persist` for a
calculation-only summary. The command makes no network request and prints no
raw records, credentials, environment values, or absolute local paths.

## Limitations and next step

Frequency instances, recurring capture, production monitoring, public realtime
reporting, punctuality classifications, headway adherence, and reliability
aggregates remain future work. The next increment is reliability indicators
built from these preserved comparison facts.
