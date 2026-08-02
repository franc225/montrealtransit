# GTFS-Realtime Protocol Buffer Parsing

## Purpose and scope

`src/parse_gtfs_realtime.py` validates and decodes one locally captured STM
GTFS-Realtime payload. It reads the untouched `.pb` payload and its JSON
metadata sidecar, verifies capture integrity, and returns immutable normalized
Python objects.

It supports Vehicle Positions, Trip Updates, deleted entities, and mixed
feeds. Service Alerts and newer entity types are preserved as unsupported
entity counts for later increments.

The parser does not make network requests, modify raw files, persist decoded
data, write DuckDB tables, schedule captures, or calculate operational
metrics.

## Official binding dependency

The project uses the official MobilityData Python bindings:

```text
gtfs-realtime-bindings==2.1.0
```

They expose `google.transit.gtfs_realtime_pb2`. Runtime parsing does not
require `protoc`, a protobuf compiler, or vendored generated source files.

## Integrity validation

Metadata is validated before protobuf decoding. The parser requires:

- regular payload and metadata files under the configured raw storage root;
- metadata schema version 1, provider `stm`, and a supported feed type;
- project-relative payload and metadata paths matching the selected files;
- an HTTP 200 protobuf response;
- exact payload size and SHA-256 agreement;
- valid capture UUID and UTC capture timestamp;
- filename timestamp and UUID agreement with metadata.

Failures reject parsing. Metadata and payload files are never repaired or
modified automatically.

## Normalized object model

The immutable model includes:

- `ParsedFeed` and `ParsedFeedHeader`;
- `ParsedEntity`;
- `ParsedTripDescriptor` and `ParsedVehicleDescriptor`;
- `ParsedVehiclePosition` and `ParsedPosition`;
- `ParsedTripUpdate`, `ParsedStopTimeUpdate`, and `ParsedStopTimeEvent`;
- `ValidationFinding` and `ParseSummary`.

Entity and Stop Time Update order is preserved. Missing optional protobuf
fields remain `None`; the parser does not invent default business values.

## Timestamps and enums

Original Unix timestamp integers are preserved. Explicit `*_utc` properties
contain timezone-aware UTC `datetime` values. The normalized parser does not
convert real-time timestamps to Montréal local time.

Trip `start_date` remains its original `YYYYMMDD` service-date string.

Enums preserve both their numeric value and a readable binding name. An
unknown numeric value is labelled safely when the protobuf runtime exposes
it.

## Structural errors and findings

Fatal errors include malformed or truncated protobufs, unsupported header
versions, missing required protobuf fields, missing or duplicate entity IDs,
invalid timestamps, and capture-integrity failures.

Entity-level findings include invalid coordinates, invalid `start_date`,
invalid stop sequences, missing trip identifiers, and metadata feed-type
mismatches. Unexpected supported entities remain normalized and counted;
they are not relabelled or silently discarded.

## CLI usage

The default output is a concise summary. The matching `.json` sidecar is
located automatically:

```powershell
python .\src\parse_gtfs_realtime.py `
    --payload data\raw\gtfs_realtime\stm\vehicle_positions\YYYY\MM\DD\CAPTURE.pb
```

Specify metadata explicitly when needed:

```powershell
python .\src\parse_gtfs_realtime.py `
    --payload data\raw\gtfs_realtime\stm\trip_updates\YYYY\MM\DD\CAPTURE.pb `
    --metadata data\raw\gtfs_realtime\stm\trip_updates\YYYY\MM\DD\CAPTURE.json `
    --summary
```

Summary output includes capture identity, header information, entity counts,
unsupported and deleted counts, validation findings, payload size, and
SHA-256 verification. It does not dump raw bytes or full protobuf/entity
representations.

## Security and testing

- No API key is needed to parse a local capture.
- Output uses project-relative paths when available.
- Errors omit request headers, environment values, raw bytes, and protobuf
  representations.
- Tests construct synthetic protobuf messages in memory under isolated
  temporary project roots and block network transports.
- Raw captures remain excluded from Git.

## Current limitations and next step

The parser does not support normalized Service Alert models, persistence,
freshness/completeness KPIs, static-to-real-time trip matching, delay or
punctuality calculations, reliability indicators, or live reporting.

Parsed captures can now proceed to the separate
[GTFS-Realtime Feed Quality](gtfs_realtime_feed_quality.md) stage for freshness,
completeness, and transactional DuckDB persistence.

The complete parser-field mapping and schema lineage are documented in
[GTFS-Realtime Normalized Persistence](gtfs_realtime_persistence.md).
