# Data Quality Rules

## Purpose

The project validates STM static GTFS data before it is used for reporting or future service reliability analysis.

Each rule produces a PASS or FAIL result and stores the number of rows checked, failed rows, failure rate, execution timestamp, and severity.

## Severity definitions

| Severity | Meaning |
|---|---|
| `CRITICAL` | A failure indicates that core identifiers, relationships, or structural assumptions are invalid. Downstream reporting should not be considered trustworthy until the issue is reviewed. |
| `WARNING` | A failure indicates a potential business or operational concern. Reporting can continue, but the issue should be investigated and documented. |
| `INFO` | Informational monitoring rule. No current rules use this severity. |

## Status definitions

| Status | Meaning |
|---|---|
| `PASS` | No row violated the rule during the validation run. |
| `FAIL` | At least one row violated the rule during the validation run. |

## Rule catalogue

| Rule | Severity | Table | Validation logic |
|---|---|---|---|
| `DQ001` | CRITICAL | `fct_scheduled_stop_time` | `trip_id`, `stop_id`, and `stop_sequence` must be populated. |
| `DQ002` | CRITICAL | `fct_scheduled_stop_time` | A trip cannot contain duplicate `stop_sequence` values. |
| `DQ003` | CRITICAL | `fct_scheduled_stop_time` | Every `trip_id` must exist in `dim_trip`. |
| `DQ004` | CRITICAL | `fct_scheduled_stop_time` | Every `stop_id` must exist in `dim_stop`. |
| `DQ005` | CRITICAL | `fct_scheduled_stop_time` | Arrival and departure values must be valid GTFS time values in `HH:MM:SS` format. |
| `DQ006` | WARNING | `fct_scheduled_stop_time` | When both values exist, departure time cannot be earlier than arrival time at the same stop. |
| `DQ007` | CRITICAL | `dim_trip` | Every planned trip must have at least one scheduled stop. |
| `DQ008` | WARNING | `dim_route` | Every route should have at least one planned trip in the current feed. |
| `DQ009` | WARNING | `dim_stop` | Stop coordinates must exist and fall within a broad Montréal-area geographic envelope. |
| `DQ010` | CRITICAL | `fct_scheduled_stop_time` | `stop_sequence` must be a positive integer. |

## Quality metrics

The validation process calculates the following metrics for each rule:

```text
rows_checked = number of rows evaluated by the rule

rows_failed = number of rows that violate the rule

failure_rate = rows_failed / rows_checked
```

The report also calculates an overall failure rate across all implemented rules:

```text
overall_failure_rate =
    total_rows_failed / total_rows_checked
```

## GTFS time handling

GTFS time values may exceed 23:59:59.

This is valid when service continues after midnight.

Examples:

23:45:00 = 11:45 PM on the service day
24:15:00 = 12:15 AM on the following calendar day
25:30:00 = 1:30 AM on the following calendar day

For this reason, the project validates the general GTFS time pattern instead of restricting hours to a maximum of 23.

## Current validation result

The initial static GTFS snapshot passed all 10 implemented data quality controls.

This confirms that the loaded snapshot passed the current checks for:

- completeness;
- referential integrity;
- trip sequence uniqueness;
- time consistency;
- GTFS time formatting;
- positive stop sequences;
- route and trip coverage;
- coordinate plausibility.

## Limitations

The current rules do not yet evaluate:

- GTFS-Realtime feed availability;
- real-time feed freshness;
- vehicle position completeness;
- planned versus observed arrival deviation;
- operational punctuality;
- service cancellations;
- passenger occupancy data;
- geographic accuracy beyond a broad coordinate envelope.