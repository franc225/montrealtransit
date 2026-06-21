# Data Model

## Purpose

The project transforms STM static GTFS files into a small analytical warehouse designed for quality validation and future service reliability analysis.

The model separates source preservation, analytical tables, and data quality execution history.

## Model layers

| Layer | Purpose | Examples |
|---|---|---|
| Raw | Preserve GTFS source files as loaded from the feed | `raw_routes`, `raw_stops`, `raw_trips`, `raw_stop_times` |
| Analytical | Provide typed and simplified tables for reporting and quality checks | `dim_route`, `dim_stop`, `dim_trip`, `fct_scheduled_stop_time` |
| Quality | Store rules, validation runs, and results | `dq_rule`, `dq_run`, `dq_result` |

## Raw layer

The raw layer loads GTFS text files into DuckDB with source values preserved as text.

| Table | Source file | Description |
|---|---|---|
| `raw_agency` | `agency.txt` | Transit agency information |
| `raw_calendar` | `calendar.txt` | Regular service calendars |
| `raw_calendar_dates` | `calendar_dates.txt` | Service exceptions |
| `raw_feed_info` | `feed_info.txt` | Feed metadata and validity dates |
| `raw_routes` | `routes.txt` | STM route definitions |
| `raw_shapes` | `shapes.txt` | Geographic route shapes |
| `raw_stop_times` | `stop_times.txt` | Planned arrival and departure times |
| `raw_stops` | `stops.txt` | Stop definitions and coordinates |
| `raw_trips` | `trips.txt` | Planned trips linked to routes and services |

## Analytical layer

### `dim_route`

Grain: one row per GTFS `route_id`.

| Column | Type | Description |
|---|---|---|
| `route_id` | VARCHAR | Unique GTFS route identifier |
| `route_short_name` | VARCHAR | Public-facing route number or short label |
| `route_long_name` | VARCHAR | Route description |
| `route_type` | INTEGER | GTFS transportation type |

### `dim_stop`

Grain: one row per GTFS `stop_id`.

| Column | Type | Description |
|---|---|---|
| `stop_id` | VARCHAR | Unique GTFS stop identifier |
| `stop_name` | VARCHAR | Public-facing stop name |
| `stop_lat` | DOUBLE | Stop latitude |
| `stop_lon` | DOUBLE | Stop longitude |

### `dim_service`

Grain: one row per GTFS `service_id`.

| Column | Type | Description |
|---|---|---|
| `service_id` | VARCHAR | Unique GTFS service identifier |
| `monday` to `sunday` | INTEGER | Service availability by weekday |
| `start_date` | DATE | Start date of the service period |
| `end_date` | DATE | End date of the service period |

### `dim_trip`

Grain: one row per GTFS `trip_id`.

| Column | Type | Description |
|---|---|---|
| `trip_id` | VARCHAR | Unique GTFS trip identifier |
| `route_id` | VARCHAR | Associated route identifier |
| `service_id` | VARCHAR | Associated service identifier |
| `trip_headsign` | VARCHAR | Public-facing destination or headsign |
| `direction_id` | INTEGER | Direction indicator |
| `shape_id` | VARCHAR | Associated route geometry identifier |

### `fct_scheduled_stop_time`

Grain: one row per planned stop sequence within a trip.

Business key: `trip_id` + `stop_sequence`.

| Column | Type | Description |
|---|---|---|
| `trip_id` | VARCHAR | Associated planned trip |
| `stop_id` | VARCHAR | Associated stop |
| `stop_sequence` | INTEGER | Order of the stop within the trip |
| `arrival_time` | VARCHAR | GTFS planned arrival time |
| `departure_time` | VARCHAR | GTFS planned departure time |
| `arrival_seconds` | INTEGER | Arrival time converted to seconds after service-day start |
| `departure_seconds` | INTEGER | Departure time converted to seconds after service-day start |

GTFS allows times greater than `23:59:59` for trips that continue after midnight. For example, `25:15:00` is valid and represents 1:15 AM on the following day.

### `meta_gtfs_feed`

Grain: one row per ingestion run.

This table stores GTFS feed metadata together with the ingestion run identifier and timestamp.

## Data quality layer

### `dq_rule`

Grain: one row per implemented quality rule.

| Column | Description |
|---|---|
| `rule_id` | Unique rule identifier |
| `rule_name` | Short business description |
| `severity` | Criticality classification |
| `table_name` | Main table evaluated by the rule |
| `description` | Detailed rule description |

### `dq_run`

Grain: one row per quality check execution.

| Column | Description |
|---|---|
| `run_id` | Unique execution identifier |
| `executed_at` | Timestamp of the execution |
| `database_path` | Local DuckDB warehouse path |

### `dq_result`

Grain: one row per rule per quality execution.

| Column | Description |
|---|---|
| `run_id` | Link to the executed validation run |
| `rule_id` | Link to the quality rule |
| `rule_name` | Rule name copied for reporting convenience |
| `severity` | Rule severity copied for reporting convenience |
| `table_name` | Main table evaluated |
| `status` | PASS or FAIL |
| `rows_checked` | Number of records evaluated |
| `rows_failed` | Number of records violating the rule |
| `failure_rate` | `rows_failed / rows_checked` |
| `executed_at` | Timestamp of the execution |

## Relationships

```text
dim_route (1)
    |
    | route_id
    |
    +----< dim_trip >----+ dim_service (1)
              |
              | trip_id
              |
              +----< fct_scheduled_stop_time >----+ dim_stop (1)
                            |
                            | stop_id
                            |

dq_rule (1)
    |
    | rule_id
    |
    +----< dq_result >----+ dq_run (1)
                            |
                            | run_id
                            |
```

## Refresh process

```text
GTFS ZIP archive
    |
    v
Python ingestion
    |
    v
Raw DuckDB tables
    |
    v
Analytical tables
    |
    v
Data quality checks
    |
    v
dq_rule / dq_run / dq_result
    |
    v
Static HTML report
```

### Current scope

The current model supports static GTFS quality validation.

The future real-time scope will add tables for:

- vehicle positions;
- trip updates;
- feed freshness;
- planned versus observed service performance;
- reliability indicators by route, period, and direction.