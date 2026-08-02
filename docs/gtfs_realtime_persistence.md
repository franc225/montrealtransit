# GTFS-Realtime Normalized Persistence

## Purpose

The persistence layer maps validated immutable parser models to normalized
DuckDB columns. It retains the lineage and optional-field distinctions needed
by scheduled-service matching without storing protobuf representations or
modifying raw captures.

## Model-to-table mapping

| Parser model | DuckDB table | Persisted content |
|---|---|---|
| `ParsedFeed` / header / summary | `gtfs_realtime_capture` | Capture identity, protocol/header values, relative paths, hash, counts, and schema versions |
| `ParsedEntity` | `gtfs_realtime_entity` | Source index/order, ID/type/deleted flags, common identifiers, and versions |
| `ParsedVehiclePosition` | `gtfs_realtime_vehicle_position` | Complete trip/vehicle descriptors, position, statuses, occupancy, and timestamp |
| `ParsedTripUpdate` | `gtfs_realtime_trip_update` | Complete trip/vehicle descriptors, timestamp, delay, and update count |
| `ParsedStopTimeUpdate` | `gtfs_realtime_stop_time_update` | Source order, stop reference, relationship, and complete arrival/departure events |
| `ValidationFinding` | `gtfs_realtime_parser_finding` | Ordered finding code, message, and optional entity index |

Every enum has separate nullable numeric and readable-name columns. Every
timestamp keeps the original Unix integer and a separately derived
timezone-aware UTC value. Optional fields remain null; an explicitly present
numeric zero remains zero.

## Lineage and versions

Primary lineage keys are capture UUID, entity index, and StopTimeUpdate index.
They preserve FeedEntity and update order and must not be replaced by entity
ID alone.

The versions have distinct meanings:

- `gtfs_realtime_version`: provider protocol version from `FeedHeader`;
- `parser_model_schema_version`: normalized Python model contract, currently 1;
- `persistence_schema_version`: normalized DuckDB fact contract, currently 2;
- legacy `parser_schema_version`: retained for compatibility with v1 rows;
- quality configuration version: retained independently on quality runs.

## Additive migration

Schema initialization creates missing tables and applies nullable columns with
idempotent `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` statements in a dedicated
transaction. It does not drop, rename, replace, or rewrite existing realtime,
quality, or static tables.

Rows created under persistence v1 retain null `persistence_schema_version` and
remain distinguishable from complete v2 rows. They are not silently marked
complete or reconstructed. Reingesting the same UUID/hash for such a row is
rejected with an explicit older-schema message. A future opt-in rebuild can be
designed separately when its raw pair remains available.

## Idempotency and transactions

The same UUID/hash already stored at version 2 returns `already ingested`
without duplicate facts. The same UUID with a different hash is an integrity
conflict.

For a new capture, inventory, entity facts, Vehicle Positions, Trip Updates,
StopTimeUpdates, parser findings, quality run, and quality results are inserted
in one explicit transaction. Any failure rolls back the entire new capture and
preserves earlier rows and raw files.

## CLI

The existing command uses the complete schema automatically:

```powershell
python .\src\ingest_gtfs_realtime.py --payload <CAPTURE.pb>
```

`--metadata`, `--warehouse`, and `--no-persist` retain their documented
behavior. No credential or network request is required.

## Frequencies and limitations

The current static ingestion does not persist `frequencies.txt`. This does not
block normalized realtime persistence. Frequency-instance matching remains
explicitly unsupported until a dedicated static persistence policy is added.

Complete schema-v2 captures can proceed to
[GTFS-Realtime Scheduled-Service Matching](gtfs_realtime_schedule_matching.md).
Older incomplete captures remain ineligible.
