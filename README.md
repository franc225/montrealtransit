# Montréal Transit Reliability & Data Quality

![Project status](https://img.shields.io/badge/status-V2%20interactive%20reporting-2E8B57?style=flat-square)
![Python](https://img.shields.io/badge/Python-3.11-3776AB?style=flat-square&logo=python&logoColor=white)
![DuckDB](https://img.shields.io/badge/DuckDB-analytics-FCC624?style=flat-square&logo=duckdb&logoColor=black)
![Static GTFS](https://img.shields.io/badge/Static%20GTFS-pipeline%20complete-0085CA?style=flat-square)
![GTFS-Realtime](https://img.shields.io/badge/GTFS--Realtime-interactive%20dashboard-6F42C1?style=flat-square)
![API security](https://img.shields.io/badge/API%20security-redirect%20safe-137333?style=flat-square)
![Report](https://img.shields.io/badge/report-HTML%20static-5B5FC7?style=flat-square)
[![Validate pipeline](https://github.com/franc225/montrealtransit/actions/workflows/validate.yml/badge.svg)](https://github.com/franc225/montrealtransit/actions/workflows/validate.yml)
[![Live report](https://img.shields.io/badge/live%20report-GitHub%20Pages-2E8B57?style=flat-square&logo=githubpages&logoColor=white)](https://franc225.github.io/montrealtransit/)


A data quality and operational analytics project built from Montréal STM GTFS data.

[View the live Data Quality Overview](https://franc225.github.io/montrealtransit/)

[View the live GTFS-Realtime Reliability Dashboard](https://franc225.github.io/montrealtransit/gtfs_realtime_reliability.html)

## Objective

Transform STM static and GTFS-Realtime data into traceable analytical datasets,
validate their quality, compare observed service with the schedule, and publish
static HTML quality and reliability reports.

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
- Automated the static GTFS refresh and report regeneration process.
- Established validated, nonsecret GTFS-Realtime configuration.
- Added environment-variable API key handling with secret-safe validation.
- Configured the official STM Vehicle Positions and Trip Updates endpoints.
- Validated the official `apiKey` authentication and protobuf Accept headers.
- Added strict HTTPS, STM-host, URL credential, query, fragment, and path validation.
- Defined safe, isolated raw-storage and capture filename conventions.
- Added network-free GTFS-Realtime contract, secret-safety, and path tests.
- Documented STM attribution, CC BY 4.0 terms, and unofficial-project status.
- Added secure one-shot raw capture with streamed size enforcement.
- Added atomic `.pb` payload and nonsecret JSON metadata persistence.
- Enforced exact `Content-Length` integrity when the header is present.
- Added redirect-safe transport, protocol-failure handling, and network-free
  security regression tests.
- Added one-shot capture for both Vehicle Positions and Trip Updates with
  network-free dry-run support.
- Added incremental SHA-256 generation and non-overwriting rollback-based
  payload/metadata persistence.
- Added capture-relative freshness and entity/field completeness analytics.
- Added transactional normalized GTFS-Realtime DuckDB tables with safe
  idempotent ingestion and rollback.
- Added complete parser-field persistence with numeric/readable enums,
  Unix/UTC timestamps, ordering, and parser-finding lineage.
- Added additive persistence schema v2 migration while keeping older
  incomplete rows explicitly distinguishable.
- Added deterministic scheduled-service, service-date, and StopTimeUpdate
  matching with static-snapshot lineage and DST-safe comparison facts.
- Added transparent arrival/departure punctuality, trip, route, stop,
  service-date, cancellation, and coverage indicators with versioned policy.
- Added a self-contained interactive reliability dashboard with read-only
  persisted-metric loading, client-side filters, and safe HTML serialization.

### Planned

- Schedule recurring capture after the one-shot collector is operationally
  validated.
- Add static frequency-instance persistence and matching policy.
- Add controlled recurring capture and collector-coverage monitoring.
- Add headway and travel-time reliability after recurring coverage is proven.
- Add multi-run historical trends and recurring controlled capture.
- Measure collector availability only after recurring coverage is established.
- Add headway adherence, excess wait time, travel-time reliability, and
  passenger weighting only when their data requirements are met.
- Add a continuously refreshed public dashboard only with production-grade
  collection and monitoring.
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
Python refresh and ingestion
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
        |
        v
GitHub Pages
```

The realtime path remains separate from static ingestion and uses its static
service snapshot only during deterministic matching:

```text
Static GTFS
    ↓
GTFS-Realtime capture
    ↓
Protocol Buffer parsing
    ↓
DuckDB persistence
    ↓
Freshness & completeness
    ↓
Scheduled-service matching
    ↓
Reliability indicators
    ↓
Interactive HTML dashboard
```

## Data Quality Overview

The project generates a static HTML report published through GitHub Pages.

[View the live report](https://franc225.github.io/montrealtransit/)

The local HTML report is generated at:

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
- automated validation coverage across configuration, capture, parsing, and
  static-pipeline tests.

## Report preview

These images are generated from `docs/index.html`:

### Data Quality Overview

![Data Quality Overview](docs/assets/screenshots/data-quality-overview.png)

### Data Quality Rule Results

![Data Quality Rule Results](docs/assets/screenshots/data-quality-rule-results.png)

Regenerate the README screenshots with an installed Microsoft Edge, Google
Chrome, or Chromium browser:

```powershell
python .\src\generate_report_screenshots.py
```

Use `--report static` or `--report realtime` to regenerate only one report's
screenshots.

## Data model

The project separates the data warehouse into three layers.

| Layer | Purpose | Examples |
|---|---|---|
| Raw | Preserve GTFS source files as loaded | `raw_routes`, `raw_stops`, `raw_trips`, `raw_stop_times` |
| Analytical | Provide typed tables for reporting and validation | `dim_route`, `dim_stop`, `dim_trip`, `fct_scheduled_stop_time` |
| Quality | Store rules, runs, and validation results | `dq_rule`, `dq_run`, `dq_result` |

Detailed documentation is available in [data_model.md](docs/data_model.md).

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
| DQ005 | CRITICAL | Scheduled times use valid GTFS hours and `00`–`59` minute/second components |
| DQ006 | WARNING | Departure is not earlier than arrival |
| DQ007 | CRITICAL | Trips have at least one scheduled stop |
| DQ008 | WARNING | Routes have at least one planned trip |
| DQ009 | WARNING | Stop coordinates are plausible for the STM service area |
| DQ010 | CRITICAL | Stop sequence is positive |

Detailed documentation is available in [data_quality_rules.md](docs/data_quality_rules.md).

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

## Implemented GTFS-Realtime capabilities

The implemented GTFS-Realtime foundation validates the official STM Vehicle
Positions and Trip Updates endpoints and provides a controlled one-shot raw
capture command.

Current capabilities include:

- API-key loading only from `STM_GTFS_REALTIME_API_KEY`;
- local configuration and path validation;
- network-free dry runs for both supported feeds;
- one HTTPS request per selected one-shot capture;
- redirect rejection so credentials are never forwarded;
- bounded streaming with maximum-response-size enforcement;
- exact `Content-Length` integrity validation when the header is present;
- incremental SHA-256 generation over untouched response bytes;
- raw `.pb` persistence with nonsecret `.json` metadata;
- atomic non-overwriting file finalization with rollback;
- deterministic, network-free mocked tests.
- capture-integrity validation and GTFS-Realtime Protocol Buffer decoding;
- immutable normalized Vehicle Position and Trip Update models with UTC
  timestamps, enum names, and validation findings.

Supported feeds:

```text
vehicle_positions
trip_updates
```

Load the API key interactively for the current PowerShell session without
placing its value directly in command history:

```powershell
$secureKey = Read-Host "STM API key" -AsSecureString
$keyPointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secureKey)
try {
    $env:STM_GTFS_REALTIME_API_KEY = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($keyPointer)
}
finally {
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($keyPointer)
}
```

Raw payloads and sidecars use a shared UTC timestamp and UUID under
`data/raw/gtfs_realtime/stm/<FEED_TYPE>/YYYY/MM/DD/` and remain excluded from
Git. The reproducible commands for capture through reporting follow below.

Never paste Swagger-generated curl commands containing the API key into
documentation, issues, commits, or chat tools.

Data source: Société de transport de Montréal (STM), under the Creative
Commons Attribution 4.0 licence. This is an independent and unofficial
portfolio project and is not affiliated with, sponsored by, endorsed by, or
associated with the STM.

## GTFS-Realtime end-to-end workflow

The interactive workflow has been validated using controlled live STM
GTFS-Realtime observations. A limited set of captures demonstrates technical
feasibility of the complete pipeline, but does not represent continuous,
comprehensive, or system-wide STM reliability.

Prerequisites are Windows PowerShell, the activated Python virtual environment,
installed repository dependencies, and an STM developer API key loaded into
`STM_GTFS_REALTIME_API_KEY` using the secure session-only pattern above. Never
commit an API key. `.env` and `.env.*` are ignored; `.env.example` is
intentionally trackable and contains only a placeholder.

### 1. Refresh static GTFS

This refreshes and validates the static STM feed, rebuilds DuckDB, runs its
quality checks, and makes the service snapshot available for matching.

```powershell
python .\src\refresh_static_gtfs.py
```

See [Data Model](docs/data_model.md) and
[Data Quality Rules](docs/data_quality_rules.md).

### 2. Capture Vehicle Positions

The one-shot capture produces an immutable `.pb` payload, nonsecret JSON
sidecar, SHA-256 lineage, and UTC capture timestamp.

```powershell
python .\src\capture_gtfs_realtime.py `
    --feed vehicle_positions

$vpPayload = (
    Get-ChildItem `
        .\data\raw\gtfs_realtime\stm\vehicle_positions `
        -Recurse `
        -Filter *.pb `
        -File |
    Sort-Object LastWriteTimeUtc -Descending |
    Select-Object -First 1
).FullName
```

Files are stored under
`data/raw/gtfs_realtime/stm/vehicle_positions/YYYY/MM/DD/` and ignored by Git.

### 3. Capture Trip Updates

Capture the two feed types close together for a controlled demonstration. This
does not establish continuous observation coverage.

```powershell
python .\src\capture_gtfs_realtime.py `
    --feed trip_updates

$tuPayload = (
    Get-ChildItem `
        .\data\raw\gtfs_realtime\stm\trip_updates `
        -Recurse `
        -Filter *.pb `
        -File |
    Sort-Object LastWriteTimeUtc -Descending |
    Select-Object -First 1
).FullName
```

### 4. Ingest Vehicle Positions

Ingestion validates capture integrity, parses protobuf internally, normalizes
the feed, analyzes freshness and completeness, and transactionally persists
the resulting lineage.

```powershell
python .\src\ingest_gtfs_realtime.py `
    --payload $vpPayload |
    Tee-Object -Variable vpIngestOutput

$vpCaptureUuid = (
    ($vpIngestOutput |
        Select-String '^Capture UUID:').Line `
        -replace '^Capture UUID:\s*', ''
)
```

See [Protocol Buffer Parsing](docs/gtfs_realtime_parsing.md),
[Normalized Persistence](docs/gtfs_realtime_persistence.md), and
[Feed Quality](docs/gtfs_realtime_feed_quality.md).

### 5. Ingest Trip Updates

```powershell
python .\src\ingest_gtfs_realtime.py `
    --payload $tuPayload |
    Tee-Object -Variable tuIngestOutput

$tuCaptureUuid = (
    ($tuIngestOutput |
        Select-String '^Capture UUID:').Line `
        -replace '^Capture UUID:\s*', ''
)
```

### 6. Match Vehicle Positions to scheduled GTFS

Matching uses static trip identity, Montréal service dates, service calendars,
and stop sequence where relevant. Unmatched, ambiguous, conflict, unsupported,
and not-applicable outcomes remain explicit.

```powershell
python .\src\match_gtfs_realtime.py `
    --capture-uuid $vpCaptureUuid |
    Tee-Object -Variable vpMatchOutput

$vpMatchRunId = (
    ($vpMatchOutput |
        Select-String '^Match run ID:').Line `
        -replace '^Match run ID:\s*', ''
)
```

See [Scheduled-Service Matching](docs/gtfs_realtime_schedule_matching.md).

### 7. Match Trip Updates

Trip Updates provide the primary scheduled-versus-observed event facts used by
the current reliability demonstration.

```powershell
python .\src\match_gtfs_realtime.py `
    --capture-uuid $tuCaptureUuid |
    Tee-Object -Variable tuMatchOutput

$tuMatchRunId = (
    ($tuMatchOutput |
        Select-String '^Match run ID:').Line `
        -replace '^Match run ID:\s*', ''
)
```

### 8. Calculate reliability indicators

When sufficient comparable data exists, indicators cover punctuality,
separate arrival/departure metrics, delay distributions, route and stop
aggregates, trip summaries, matching/comparison coverage, and observed schedule
relationships. Service-performance and data-coverage metrics remain separate.

```powershell
python .\src\calculate_gtfs_realtime_reliability.py `
    --match-run-id $tuMatchRunId |
    Tee-Object -Variable reliabilityOutput

$reliabilityRunId = (
    ($reliabilityOutput |
        Select-String '^Reliability run ID:').Line `
        -replace '^Reliability run ID:\s*', ''
)
```

See [Service Reliability Indicators](docs/gtfs_realtime_reliability.md).

### 9. Review lineage identifiers

```powershell
[PSCustomObject]@{
    VehiclePositionsCapture  = $vpCaptureUuid
    VehiclePositionsMatchRun = $vpMatchRunId
    TripUpdatesCapture       = $tuCaptureUuid
    TripUpdatesMatchRun      = $tuMatchRunId
    ReliabilityRun           = $reliabilityRunId
}
```

These identifiers preserve reproducibility across capture, matching, and
reliability stages.

### 10. Generate the interactive dashboard

The generator reads persisted metrics without recalculation. Its self-contained
HTML needs no backend server or API key and is suitable for static GitHub Pages
hosting. The public profile embeds aggregate and bounded presentation data;
complete event-level analytical facts remain in DuckDB.

```powershell
python .\src\generate_gtfs_realtime_dashboard.py `
    --reliability-run-id $reliabilityRunId `
    --profile public `
    --output .\docs\gtfs_realtime_reliability.html `
    --open
```

See [Interactive Reliability Reporting](docs/gtfs_realtime_reporting.md).

### 11. Verify secret handling

The expected result of this check is no output. Do not share matching output if
a leak is found.

```powershell
$searchRoots = @(
    ".\src",
    ".\tests",
    ".\config",
    ".\docs"
)

Get-ChildItem $searchRoots `
    -Recurse `
    -File `
    -ErrorAction SilentlyContinue |
    Select-String `
        -SimpleMatch `
        $env:STM_GTFS_REALTIME_API_KEY

Remove-Item Env:STM_GTFS_REALTIME_API_KEY
```

### 12. Re-run validation

```powershell
python -m compileall -q src tests

python -m unittest discover -s tests -p "test_*.py" -v
```

## Interactive reliability dashboard

[Open the generated dashboard](docs/gtfs_realtime_reliability.html)

The dashboard contains overview and lineage, coverage/confidence, punctuality,
delay distribution, route and stop performance, trip detail, explicitly
observed cancellations and schedule relationships, optional feed-quality
context, and methodology. It is not a production monitor or continuously
refreshed public reliability product.

For publication size and clarity, all route aggregates are retained while stop
and trip tables use deterministic bounded subsets and clearly report when they
are truncated. Histogram counts are embedded as compact bins rather than the
complete canonical event history.

### Reliability overview and coverage

![GTFS-Realtime reliability overview and coverage](docs/assets/screenshots/gtfs-realtime-reliability-overview.png)

### Route and stop performance

![GTFS-Realtime route and stop performance](docs/assets/screenshots/gtfs-realtime-reliability-performance.png)

These screenshots are generated from the reviewed
`docs/gtfs_realtime_reliability.html` artifact. The dashboard remains a
controlled feasibility demonstration rather than continuous STM monitoring.

## Documentation map

| Topic | Documentation |
|---|---|
| Static data model | [Data Model](docs/data_model.md) |
| Static and realtime quality rules | [Data Quality Rules](docs/data_quality_rules.md) |
| Realtime configuration and storage | [GTFS-Realtime Foundation](docs/gtfs_realtime_foundation.md) |
| Secure one-shot capture | [One-Shot Capture](docs/gtfs_realtime_capture.md) |
| Protobuf validation and parsing | [Protocol Buffer Parsing](docs/gtfs_realtime_parsing.md) |
| Normalized DuckDB persistence | [Normalized Persistence](docs/gtfs_realtime_persistence.md) |
| Freshness and completeness | [Feed Quality](docs/gtfs_realtime_feed_quality.md) |
| Static/realtime matching | [Scheduled-Service Matching](docs/gtfs_realtime_schedule_matching.md) |
| Reliability methodology | [Service Reliability Indicators](docs/gtfs_realtime_reliability.md) |
| Interactive reporting | [Interactive Reliability Reporting](docs/gtfs_realtime_reporting.md) |
| Attribution and licence | [Data Source and Terms](docs/data_source_and_terms.md) |

## Refresh static GTFS data

The project uses the current STM static GTFS feed for schedules, stops, routes, trips, service calendars, and shapes.

The GTFS source is available from:

- [STM Developers - GTFS scheduled data](https://www.stm.info/fr/a-propos/developpeurs)
- [Montréal Open Data - STM planned schedules and routes](https://donnees.montreal.ca/en/dataset/stm-horaires-planifies-et-trajets-des-bus-et-du-metro)

The refresh workflow downloads the current GTFS archive, validates its structure, replaces the local source files, rebuilds the DuckDB warehouse, runs the data quality checks, and regenerates the HTML report.

### Refresh the complete pipeline

Activate the local Python environment:

```powershell
.\.venv\Scripts\Activate.ps1
```

Run the full refresh and open the updated report:

```powershell
python .\src\refresh_static_gtfs.py --open-report
```

The refresh script performs the following steps:

```text
Download current STM GTFS ZIP
        |
        v
Validate ZIP integrity and required GTFS files
        |
        v
Archive the downloaded GTFS snapshot locally
        |
        v
Replace data/raw/gtfs/current
        |
        v
Run ingest_gtfs.py
        |
        v
Run run_quality_checks.py
        |
        v
Run generate_quality_report.py
        |
        v
Update docs/index.html and report charts
```

The script updates these local files:

```text
data/raw/gtfs/current/
data/warehouse/montreal_transit.duckdb
docs/index.html
docs/assets/rules_by_status.png
docs/assets/rules_by_severity.png
```

Downloaded GTFS ZIP snapshots are stored under:

```text
data/archive/gtfs/
```

The archived GTFS files, extracted source files, and DuckDB database are intentionally excluded from Git.

### Publish the refreshed report

After validating the report locally, commit the updated HTML report and chart images:

```powershell
git add docs
git commit -m "data: refresh STM GTFS snapshot and quality report"
git push
```

GitHub Pages will publish the updated report automatically.

### If the STM changes the download URL

Check the STM Developers page or the Montréal Open Data dataset page for the new static GTFS link.

Then run:

```powershell
python .\src\refresh_static_gtfs.py --download-url "https://new-stm-download-url/gtfs_stm.zip" --open-report
```

### Optional shapefile data

The `stm_sig.zip` file is not required for the current data quality pipeline.

It can be used later for geographic analysis, mapping, or route visualization.

## Project structure

```text
montrealtransit/
├── config/
│   └── gtfs_realtime.json          # Nonsecret GTFS-Realtime configuration
├── data/
│   ├── archive/                     # Ignored: downloaded GTFS snapshots
│   ├── raw/                         # Ignored: extracted GTFS files
│   └── warehouse/                   # Ignored: local DuckDB database
├── docs/
│   ├── assets/
│   │   ├── screenshots/
│   │   │   ├── data-quality-overview.png
│   │   │   └── data-quality-rule-results.png
│   │   ├── rules_by_severity.png
│   │   └── rules_by_status.png
│   ├── .nojekyll
│   ├── data_source_and_terms.md
│   ├── data_model.md
│   ├── data_quality_rules.md
│   ├── gtfs_realtime_foundation.md
│   ├── gtfs_realtime_capture.md
│   ├── gtfs_realtime_parsing.md
│   └── index.html
├── sql/
│   └── quality/
├── src/
│   ├── generate_quality_report.py
│   ├── generate_report_screenshots.py
│   ├── capture_gtfs_realtime.py
│   ├── gtfs_realtime_config.py
│   ├── parse_gtfs_realtime.py
│   ├── ingest_gtfs.py
│   ├── refresh_static_gtfs.py
│   └── run_quality_checks.py
├── tests/
│   ├── test_gtfs_realtime_config.py
│   ├── test_gtfs_realtime_capture.py
│   ├── test_gtfs_realtime_parser.py
│   └── test_pipeline.py
├── .env.example
├── .gitignore
├── README.md
└── requirements.txt
```

## Local setup

The concise tree above highlights the original static pipeline. The realtime
implementation adds configuration for capture, quality, matching, and
reliability; source modules for capture, parsing, persistence, quality,
matching, reliability, and dashboard generation; matching/reliability/reporting
tests; and the focused documents linked in the documentation map.

```text
config/gtfs_realtime*.json
src/capture_gtfs_realtime.py
src/parse_gtfs_realtime.py
src/ingest_gtfs_realtime.py
src/match_gtfs_realtime.py
src/calculate_gtfs_realtime_reliability.py
src/generate_gtfs_realtime_dashboard.py
tests/test_gtfs_realtime_*.py
docs/gtfs_realtime_*.md
```

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1

python -m pip install -r requirements.txt
```

## Run the pipeline manually

The recommended approach is the automated refresh script. The commands below are useful when running an existing local GTFS snapshot manually.

```powershell
python .\src\ingest_gtfs.py
python .\src\run_quality_checks.py
python .\src\generate_quality_report.py
```

## Local report preview

```powershell
Start-Process .\docs\index.html
```

## Continuous integration

GitHub Actions validates the Python pipeline on every relevant push and pull request.

The workflow:

- installs the pinned Python dependencies;
- compiles the Python scripts;
- validates the refresh script command-line interface;
- validates GTFS-Realtime configuration, secret handling, response integrity,
  redirect rejection, and network isolation with synthetic fixtures;
- runs the ingestion, quality checks, and HTML report generation against a synthetic GTFS fixture;
- confirms that a valid fixture passes all 10 rules;
- confirms that an intentionally invalid stop sequence is detected by `DQ010`.

The CI workflow does not download the live STM GTFS feed. This keeps validation deterministic, fast, and independent of external service availability.

Run the same tests locally:

```powershell
python -m unittest discover -s tests -p "test_*.py" -v
```

## Data source

Static and GTFS-Realtime data are supplied by the Société de transport de
Montréal (STM) under the applicable terms described in
[Data Source, Attribution, and Terms of Use](docs/data_source_and_terms.md).

This is an independent and unofficial portfolio project. It is not affiliated
with, sponsored by, or endorsed by the STM. Data is provided as-is and
according to availability.

## Roadmap

### Version 1 — Data Quality Foundation

- [x] Download static GTFS data
- [x] Build DuckDB ingestion
- [x] Create analytical model
- [x] Implement 10 quality checks
- [x] Generate static HTML Data Quality Overview
- [x] Document the data model and quality rules
- [x] Automate static GTFS refresh and report generation

### Version 2 — Service Reliability

- [x] Establish GTFS-Realtime configuration and secret handling
- [x] Configure and validate the official STM GTFS-Realtime API contract
- [x] Define safe local raw-storage conventions
- [x] Add isolated, network-free foundation tests and documentation
- [x] Capture one selected GTFS-Realtime feed securely
- [x] Preserve raw GTFS-Realtime responses with nonsecret metadata
- [x] Parse GTFS-Realtime protobuf messages
- [x] Measure feed freshness and completeness
- [x] Persist complete normalized realtime lineage in DuckDB
- [x] Match realtime entities to scheduled service deterministically
- [x] Build transparent service reliability and coverage indicators
- [x] Generate a self-contained interactive reliability dashboard
- [x] Validate the complete pipeline with controlled live STM observations
- [x] Generate a controlled feasibility dashboard locally
- [ ] Establish recurring controlled observation coverage
- [ ] Add multi-run historical trends and collector availability metrics
- [ ] Add headway adherence, excess wait time, and travel-time reliability
- [ ] Add passenger weighting where suitable source data exists
- [ ] Establish production monitoring and a continuously refreshed dashboard

### Version 3 — Advanced Analytics

- [ ] Delay prediction
- [ ] Anomaly detection
- [ ] Reliability trends by route, period, and direction
