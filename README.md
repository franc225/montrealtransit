# Montréal Transit Reliability & Data Quality

![Project status](https://img.shields.io/badge/status-V1%20complete-2E8B57?style=flat-square)
![Python](https://img.shields.io/badge/Python-3.x-3776AB?style=flat-square&logo=python&logoColor=white)
![DuckDB](https://img.shields.io/badge/DuckDB-analytics-FCC624?style=flat-square&logo=duckdb&logoColor=black)
![GTFS](https://img.shields.io/badge/data-GTFS-0085CA?style=flat-square)
![Report](https://img.shields.io/badge/report-HTML%20static-5B5FC7?style=flat-square)
[![Live report](https://img.shields.io/badge/live%20report-GitHub%20Pages-2E8B57?style=flat-square&logo=githubpages&logoColor=white)](https://franc225.github.io/montrealtransit/)

A data quality and operational analytics project built from Montréal STM GTFS data.

[View the live Data Quality Overview](https://franc225.github.io/montrealtransit/)

## Objective

Transform static STM GTFS data into a reliable analytical dataset, validate its quality through repeatable controls, and publish a static HTML data quality report.

This project demonstrates practical skills in:

- Python data ingestion
- DuckDB data warehousing
- Operational data modelling
- SQL-based data quality checks
- Static HTML reporting with Matplotlib
- Data documentation and governance
- Reproducible analytics workflows

## Current progress

### Completed

- Downloaded and extracted the STM static GTFS feed.
- Loaded GTFS source files into DuckDB raw tables.
- Built analytical tables for routes, stops, services, trips, and scheduled stop times.
- Implemented 10 data quality rules.
- Stored data quality runs and results in DuckDB.
- Validated the first GTFS snapshot successfully.
- Generated a static HTML **Data Quality Overview** report.
- Created data model and data quality rule documentation.

### Planned

- Capture GTFS-Realtime data over several days.
- Compare scheduled and real-time service performance.
- Measure feed freshness and completeness.
- Add service reliability metrics and dashboards.
- Add delay prediction or anomaly detection only after the reliability layer is complete.

## Initial data profile

| Dataset | Rows |
|---|---:|
| Routes | 231 |
| Stops | 9,188 |
| Trips | 203,056 |
| Scheduled stop times | 7,151,705 |
| Shapes | 211,100 |

## Architecture

```text
STM static GTFS ZIP
        |
        v
Python ingestion
        |
        v
DuckDB raw tables
        |
        v
Analytical model
(dim_route, dim_stop, dim_service, dim_trip, fct_scheduled_stop_time)
        |
        v
Data quality checks
        |
        v
dq_rule / dq_run / dq_result
        |
        v
Static HTML Data Quality Overview
```

## Data Quality Overview

The project generates a static HTML report at:

```text
docs/index.html
```

The report includes:

- overall readiness assessment;
- number of implemented, passed, and failed rules;
- total rows checked and overall failure rate;
- latest validation run metadata;
- charts for rule status and severity;
- detailed results for every quality rule;
- dataset profile and row counts.

To generate the report locally:

```powershell
python .\src\generate_quality_report.py
Start-Process .\docs\index.html
```

## Report preview

### Data Quality Overview

![Data Quality Overview](docs/assets/screenshots/data-quality-overview.png)

### Data Quality Rule Results

![Data Quality Rule Results](docs/assets/screenshots/data-quality-rule-results.png)

## Data model

The project separates the data warehouse into three layers.

| Layer | Purpose | Examples |
|---|---|---|
| Raw | Preserve GTFS source files as loaded | `raw_routes`, `raw_stops`, `raw_trips`, `raw_stop_times` |
| Analytical | Provide typed tables for reporting and validation | `dim_route`, `dim_stop`, `dim_trip`, `fct_scheduled_stop_time` |
| Quality | Store rules, runs, and validation results | `dq_rule`, `dq_run`, `dq_result` |

Detailed documentation is available in [data_quality_rules.md](docs/data_quality_rules.md).

Detailed documentation is available in [data_model.md](docs/data_model.md)

### Raw tables

The ingestion process loads GTFS text files into raw DuckDB tables, including:

- `raw_agency`
- `raw_calendar`
- `raw_calendar_dates`
- `raw_feed_info`
- `raw_routes`
- `raw_shapes`
- `raw_stop_times`
- `raw_stops`
- `raw_trips`

### Analytical tables

| Table | Description |
|---|---|
| `dim_route` | STM routes and route attributes |
| `dim_stop` | Stops and geographic coordinates |
| `dim_service` | GTFS service calendars |
| `dim_trip` | Planned trips by route and service |
| `fct_scheduled_stop_time` | Scheduled arrival and departure times by stop |
| `meta_gtfs_feed` | GTFS feed metadata and ingestion information |

### Data quality tables

| Table | Description |
|---|---|
| `dq_rule` | Quality rule catalogue |
| `dq_run` | Quality check execution history |
| `dq_result` | Quality rule results by execution |

## Implemented data quality controls

| Rule | Severity | Control |
|---|---|---|
| DQ001 | CRITICAL | Required fields are populated in scheduled stop times |
| DQ002 | CRITICAL | Trip stop sequences are unique |
| DQ003 | CRITICAL | Scheduled stop times reference valid trips |
| DQ004 | CRITICAL | Scheduled stop times reference valid stops |
| DQ005 | CRITICAL | Scheduled times use a valid GTFS format |
| DQ006 | WARNING | Departure is not earlier than arrival |
| DQ007 | CRITICAL | Trips have at least one scheduled stop |
| DQ008 | WARNING | Routes have at least one planned trip |
| DQ009 | WARNING | Stop coordinates are plausible for the STM service area |
| DQ010 | CRITICAL | Stop sequence is positive |

Detailed documentation is available in:

```text
docs/data_quality_rules.md
```

## Initial quality results

The first executed GTFS snapshot passed all 10 implemented data quality controls.

The controls cover:

- completeness;
- duplicate prevention;
- referential integrity;
- temporal consistency;
- sequence validity;
- geographic plausibility;
- structural validity of the GTFS feed.

A successful validation means that no exception was detected by the current rules. It does not certify real-time data availability, punctuality, service reliability, or operational performance.

## Project structure

```text
montrealtransit/
├── data/
│   ├── raw/                         # Ignored: extracted GTFS files
│   └── warehouse/                   # Ignored: local DuckDB database
├── docs/
│   ├── assets/
│   │   ├── rules_by_severity.png
│   │   └── rules_by_status.png
│   ├── data_model.md
│   ├── data_quality_rules.md
│   └── index.html
├── sql/
│   └── quality/
├── src/
│   ├── generate_quality_report.py
│   ├── ingest_gtfs.py
│   └── run_quality_checks.py
├── .gitignore
├── README.md
└── requirements.txt
```

## Local setup

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1

python -m pip install -r requirements.txt
```

## Run the pipeline

```powershell
python .\src\ingest_gtfs.py
python .\src\run_quality_checks.py
python .\src\generate_quality_report.py
```

## Local report preview

```powershell
Start-Process .\docs\index.html
```

The local GTFS archives, extracted source files, and DuckDB database are intentionally excluded from Git.

## Data source

Static GTFS data supplied by the Société de transport de Montréal (STM).

## Roadmap

### Version 1 — Data Quality Foundation

- [x] Download static GTFS data
- [x] Build DuckDB ingestion
- [x] Create analytical model
- [x] Implement 10 quality checks
- [x] Generate static HTML Data Quality Overview
- [x] Document the data model and quality rules

### Version 2 — Service Reliability

- [ ] Capture GTFS-Realtime data
- [ ] Measure feed freshness and completeness
- [ ] Compare scheduled and real-time service data
- [ ] Build service reliability indicators

### Version 3 — Advanced Analytics

- [ ] Delay prediction
- [ ] Anomaly detection
- [ ] Reliability trends by route, period, and direction