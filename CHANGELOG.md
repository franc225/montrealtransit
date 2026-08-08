# Changelog

All notable changes to this project are documented in this file.

## [1.0.0] - 2026-08-08

### Static GTFS data quality

- Added validated STM GTFS download, local archival, raw DuckDB ingestion, and
  typed analytical modelling.
- Added ten traceable data-quality controls with explicit run identifiers and a
  generated GitHub Pages quality report.
- Added an automated refresh workflow that verifies report lineage against the
  quality run that produced it.

### GTFS-Realtime capture and processing

- Added validated nonsecret configuration and environment-only STM API-key
  handling.
- Added redirect-safe, bounded, streamed one-shot capture for Vehicle Positions
  and Trip Updates with incremental SHA-256 and atomic raw persistence.
- Added capture-integrity validation, Protocol Buffer parsing, normalized and
  versioned DuckDB persistence, and feed freshness/completeness controls.

### Matching and reliability

- Added deterministic scheduled-service matching using Montréal service dates,
  service calendars, static-snapshot lineage, and explicit ambiguity outcomes.
- Added versioned arrival/departure punctuality, delay, cancellation, route,
  stop, trip, and coverage indicators without conflating data absence with
  service failure.

### Reporting

- Added a self-contained interactive reliability dashboard that reads persisted
  classifications without recalculating them.
- Added bounded public presentation data, safe HTML serialization, GitHub Pages
  publication, and curated screenshots generated from the actual reports.

### Engineering and integrity

- Added isolated synthetic tests, temporary DuckDB warehouses, network
  tripwires, transactional persistence checks, and cross-platform CI on Python
  3.11.
- Preserved raw source values and capture bytes, explicit lineage identifiers,
  additive schema migrations, secret-safe output, and deterministic reporting.

### Demonstration scope

- This release proves the complete pipeline with controlled STM observations.
  It does not claim continuous collection, system-wide representativeness,
  official STM status, or production monitoring availability.
