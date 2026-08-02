# GTFS-Realtime Feed Quality

## Scope

The feed-quality stage validates a captured protobuf, calculates freshness and
completeness, and optionally persists normalized records and quality results in
DuckDB. It makes no network request and does not perform static GTFS matching,
scheduled-delay calculations, punctuality, or reliability analysis.

## Time semantics

- `captured_at_utc` is the observation time recorded by the collector.
- `FeedHeader.timestamp` describes the provider feed snapshot.
- entity timestamps describe individual Vehicle Positions or Trip Updates.
- the analysis timestamp records when DuckDB persistence occurred.

Historical age is always `captured_at_utc - provider timestamp`, never the
current wall clock minus an archived timestamp. Original Unix integers and
derived timezone-aware UTC values are stored separately.

The GTFS-Realtime best-practices reference a 30-second feed refresh interval
and recommend Vehicle Position and Trip Update data no older than 90 seconds.
This project uses those documented values and a project-specific five-second
future-clock-skew tolerance. Negative ages within that tolerance are retained;
larger future skew fails `RTF003`.

Sequence comparisons use the previous ingested capture for the same provider
and feed type. A local capture interval measures collector operation only. A
long manual interval is not evidence that STM data was unavailable or stale.

## Freshness metrics

The stage calculates feed-header age; per-entity age; minimum, maximum, mean,
median, and nearest-rank p95 entity age; timestamped count and ratio; future
timestamp count and maximum skew; feed-timestamp delta/repetition; payload-hash
repetition; and local capture interval. Sequence results are
`NOT_APPLICABLE` for the first comparable capture.

## Completeness

Feed counts separate total, deleted, nondeleted, expected, unexpected,
Vehicle Position, Trip Update, and unsupported entities. Deleted entities are
excluded from all business-field denominators.

Vehicle Position metrics cover identifiers, trip and vehicle descriptors,
position and valid coordinates, bearing, speed, stop reference, timestamp,
status, and occupancy fields. Trip Update metrics cover trip/service
identifiers, vehicle, timestamp, delay, and StopTimeUpdate availability.
StopTimeUpdate metrics cover stop references, events, delay/time values, and
schedule relationship.

Every ratio preserves numerator and denominator. A zero denominator produces a
null ratio and `NOT_APPLICABLE`, never zero or 100 percent. Optional protobuf
fields remain informational because no project completeness threshold is
enabled by default.

## Statuses and rules

- `PASS`: an enabled requirement is satisfied.
- `WARN`: a recommendation or repeat condition needs review.
- `FAIL`: an enabled integrity, time-skew, or monotonicity rule fails.
- `INFO`: a measured dimension has no enabled pass/fail threshold.
- `NOT_APPLICABLE`: no valid comparison or denominator exists.

The overall status is the most severe enabled, noninformational result.
Individual dimensions are never collapsed into a single completeness score.
Stable families are `RTF` (freshness), `RTC` (completeness), and `RTS`
(sequence/collector comparison).

## DuckDB model

The existing `data/warehouse/montreal_transit.duckdb` warehouse is used with
logically separate `gtfs_realtime_` tables:

- `gtfs_realtime_capture`
- `gtfs_realtime_entity`
- `gtfs_realtime_vehicle_position`
- `gtfs_realtime_trip_update`
- `gtfs_realtime_stop_time_update`
- `gtfs_realtime_quality_run`
- `gtfs_realtime_quality_result`

FeedEntity and StopTimeUpdate indexes preserve source order. Stored paths are
project-relative. One explicit transaction persists a capture, normalized
facts, quality run, and results. Any failure rolls back the entire unit.

Re-ingesting the same capture UUID and SHA-256 returns the prior ingestion
without inserting rows. The same UUID with a different hash is rejected as an
integrity conflict. Raw payloads and sidecars are never modified.

## CLI

Analyze and persist:

```powershell
python .\src\ingest_gtfs_realtime.py `
    --payload <PATH_TO_CAPTURE.pb>
```

Calculate without opening or creating a warehouse:

```powershell
python .\src\ingest_gtfs_realtime.py `
    --payload <PATH_TO_CAPTURE.pb> `
    --no-persist
```

`--metadata` overrides sidecar discovery and `--warehouse` selects a warehouse.
Output is concise and contains no raw messages, metadata dumps, credentials, or
absolute project path.

## Limitations and next step

This stage does not establish provider availability, schedule captures, match
static identifiers, compare planned and observed service, or calculate delays,
punctuality, and reliability. The next increment is static GTFS matching.
