# GTFS-Realtime Service Reliability Indicators

## Scope and prerequisites

This stage calculates transparent punctuality and coverage indicators from one
completed scheduled-service matching run. It requires matching algorithm 1.0,
complete realtime persistence schema 2, one static snapshot, and the matching
layer's preserved scheduled and realtime comparison facts.

The results describe only the observations contained in the selected matching
run. A manual or sparse capture is not comprehensive STM service reliability,
provider availability, or production monitoring.

## Performance and coverage are separate

Service-performance metrics use eligible scheduled events with comparable
delays. Coverage metrics retain unmatched, ambiguous, conflicting,
unsupported, and unavailable observations. Poor coverage is never interpreted
as poor transit service. Collector coverage cannot be inferred from a one-shot
capture and is therefore documented as incomplete rather than converted to a
service metric.

## Configuration and thresholds

`config/gtfs_realtime_reliability.json` is versioned, nonsecret project policy.
Its thresholds are not official STM or GTFS-Realtime standards:

| Classification | Boundary |
|---|---|
| `EARLY` | selected delta below -60 seconds |
| `ON_TIME` | -60 through 300 seconds, inclusive |
| `LATE` | above 300 through 600 seconds, inclusive |
| `VERY_LATE` | above 600 seconds |
| `UNCLASSIFIED` | no eligible selected delta |

Arrivals and departures are always separate observations and denominators.
Punctuality metrics are informational; the implementation does not fail a run
solely because an on-time ratio is low.

## Eligibility and delta selection

An event requires complete lineage, a matched parent trip and stop, a supported
relationship, a resolved service date and scheduled UTC time, static coverage,
and a comparable value. Exclusion reasons explicitly distinguish unmatched,
ambiguous, conflicting, incomplete, unsupported, deleted, frequency-based,
no-coverage, unresolved-time, and unavailable-comparison cases.

The selected delta is the calculated absolute-event-time delta when available,
then the reported GTFS-Realtime delay. Both original values and their
consistency difference remain stored. Differences exceeding 30 seconds create
`RTCONS001_DELAY_SOURCE_DISAGREEMENT`; the calculated value remains selected
and no third value is invented.

## Canonical observations and duplicates

The canonical key contains provider, static snapshot, Montréal service date,
static trip, stop sequence, and event type. Unresolved records use source
lineage so unrelated exclusions are not merged. The default policy selects the
latest eligible observation, then capture UUID, entity index, and update index
as deterministic tie-breakers. An ineligible later observation does not
replace an eligible one.

Candidate count, first observation time, selected observation time, and whether
the delta changed are retained. The current CLI analyzes one match run; this
still detects duplicate event keys within that run. Different snapshots and
trip instances are never combined.

## Indicators and aggregation

Stable indicator families are:

- `RTP001` event classification and `RTP002` on-time ratio;
- `RTD001` median and `RTD002` p95 selected delay;
- `RTR001` explicitly reported cancellation count;
- `RTCV001` trip matching, `RTCV002` stop matching, and `RTCV003` comparison availability;
- `RTCONS001` reported/calculated delay disagreement.

Trip summaries retain separate arrival/departure counts, ratios, distribution,
first/last events, start/end delay, delay change, maximum lateness, and any
very-late event. No hidden trip-level punctuality label is assigned.

Aggregates are produced separately by event type for service date, route,
route-direction, stop, stop-route-direction, and selected capture scope. They
include counts, ratios, minimum, maximum, mean, median, p90, and p95. Percentiles
use deterministic linear interpolation between sorted observations. Results
below the configured denominator are `LOW_SAMPLE`; empty denominators are null
and `NOT_APPLICABLE`.

Stop results are not passenger-experience measures because passenger volumes
are unavailable. Route results below the sample threshold must not be
presented as representative.

## Cancellation interpretation

Only an explicitly observed `CANCELED` schedule relationship is a reported
cancellation. Scheduled, canceled, added, unscheduled, duplicated, absent, and
unsupported relationships are counted separately. Feed absence and unmatched
entities are never inferred to be cancellations. The ratio denominator is
explicitly the observed scheduled-family relationships; it is not a complete
system cancellation rate.

## Coverage

Stored coverage includes entity and trip outcomes, StopTimeUpdate outcomes,
comparable and classified events, canonical and candidate observations, trip
and stop matching ratios, comparison availability, classification coverage,
and observations per selected event. A zero denominator remains null.

## DuckDB persistence and lineage

Reliability-only additive tables are:

- `gtfs_realtime_reliability_run`;
- `gtfs_realtime_reliability_event`;
- `gtfs_realtime_reliability_trip`;
- `gtfs_realtime_reliability_aggregate`;
- `gtfs_realtime_reliability_finding`.

Rows retain reliability run, match run, capture/entity/update, static snapshot,
matching and persistence versions, reliability algorithm/configuration, and
threshold provenance. One transaction stores the entire assessment. Any
failure rolls back all new reliability rows and preserves prior runs and every
upstream table.

Identity consists of match run, optional route filter, reliability algorithm,
and configuration version. Repetition returns the existing completed run;
incompatible source lineage is rejected and results are never replaced.

## CLI

```powershell
python .\src\calculate_gtfs_realtime_reliability.py `
    --match-run-id <MATCH_RUN_ID>
```

Use `--route-id` for one route, `--warehouse` for an alternate DuckDB file, and
`--no-persist` for calculation-only output. There is no ambiguous “latest”
shortcut. The command makes no network request and prints no raw records,
vehicle identifiers, credentials, environment values, or absolute paths.

## Limitations and future work

Recurring controlled capture, capture-availability measurement, headway
adherence, excess wait time, bunching, travel-time reliability, passenger
weighting, production monitoring, and public reliability reporting remain
future work. Headway metrics require a documented recurring observation
cadence and cannot be derived responsibly from manual captures.
