# AGENTS.md

## Project overview

Montréal Transit Reliability & Data Quality is a Python and DuckDB analytics project built from Société de transport de Montréal (STM) GTFS data.

The completed Version 1 scope includes:

- static GTFS download and validation;
- raw GTFS ingestion into DuckDB;
- analytical data modelling;
- automated data quality checks;
- traceable quality validation runs;
- static HTML reporting with Matplotlib;
- GitHub Pages publication;
- integration tests using synthetic GTFS fixtures;
- GitHub Actions continuous integration;
- a complete automated refresh workflow.

The public report is generated at:

```text
docs/index.html
```

The current project repository is:

```text
https://github.com/franc225/montrealtransit
```

The public GitHub Pages report is:

```text
https://franc225.github.io/montrealtransit/
```

## Project priorities

Follow these priorities in order:

1. Keep the existing static GTFS pipeline stable.
2. Preserve reproducibility and traceability.
3. Maintain compatibility with Windows and GitHub Actions on Ubuntu.
4. Add GTFS-Realtime incrementally.
5. Validate data quality before producing operational indicators.
6. Compare scheduled and real-time data before adding predictive models.
7. Add delay prediction or anomaly detection only after the reliability layer is complete.

Do not introduce unrelated tools, frameworks, or architectural complexity without a clear project benefit.

## Development environment

The primary local environment is:

- Windows
- Visual Studio Code
- PowerShell
- Python virtual environment under `.venv`
- DuckDB local analytical warehouse
- GitKraken for Git operations
- GitHub Actions running on Ubuntu

The project root is normally:

```text
C:\dev\montrealtransit
```

Never hard-code this local path in application code or tests.

The project supports an alternate root through:

```text
MONTREAL_TRANSIT_PROJECT_ROOT
```

This environment variable is used by integration tests to run the pipeline in temporary directories.

## Python environment

Activate the virtual environment in PowerShell:

```powershell
.\.venv\Scripts\Activate.ps1
```

Install dependencies:

```powershell
python -m pip install -r requirements.txt
```

When troubleshooting Python path issues, the virtual environment interpreter can be called explicitly:

```powershell
.\.venv\Scripts\python.exe
```

## Primary validation commands

Run the integration test suite:

```powershell
python -m unittest discover -s tests -p "test_*.py" -v
```

Compile the Python source and tests:

```powershell
python -m compileall -q src tests
```

Run the complete static GTFS refresh:

```powershell
python .\src\refresh_static_gtfs.py --open-report
```

Run the individual pipeline stages:

```powershell
python .\src\ingest_gtfs.py
python .\src\run_quality_checks.py
python .\src\generate_quality_report.py
```

Preview the generated HTML report:

```powershell
Start-Process .\docs\index.html
```

Before declaring a task complete, run at minimum:

```powershell
python -m compileall -q src tests
python -m unittest discover -s tests -p "test_*.py" -v
```

## Current pipeline

The static GTFS refresh workflow is:

```text
Download current STM GTFS ZIP
        |
        v
Validate ZIP integrity and required GTFS files
        |
        v
Archive the downloaded snapshot locally
        |
        v
Replace data/raw/gtfs/current
        |
        v
Run ingest_gtfs.py
        |
        v
Build DuckDB raw and analytical tables
        |
        v
Run run_quality_checks.py
        |
        v
Store dq_rule / dq_run / dq_result
        |
        v
Run generate_quality_report.py
        |
        v
Generate docs/index.html and report charts
        |
        v
Publish through GitHub Pages
```

The refresh script passes an explicit quality `run_id` to the quality-check and report-generation scripts.

The generated report must contain the same `run_id` created by the current refresh execution.

Do not revert to selecting a report run only through an ambiguous timestamp when an explicit `run_id` is available.

## Main source files

### `src/refresh_static_gtfs.py`

Responsibilities:

- download the current STM static GTFS archive;
- validate the ZIP archive;
- verify required GTFS files;
- calculate a SHA-256 hash;
- archive the downloaded snapshot locally;
- replace the current extracted GTFS directory;
- execute the ingestion, quality, and reporting scripts;
- pass the same quality `run_id` through the validation and reporting stages;
- verify that the generated report contains the expected `run_id`;
- optionally open the generated report.

### `src/ingest_gtfs.py`

Responsibilities:

- load GTFS source files into DuckDB raw tables;
- preserve raw GTFS source values;
- build typed analytical tables;
- convert GTFS time values to seconds;
- support GTFS times beyond `23:59:59`;
- store ingestion metadata and row counts.

### `src/run_quality_checks.py`

Responsibilities:

- register quality rules;
- execute the implemented controls;
- store quality executions in `dq_run`;
- store rule results in `dq_result`;
- support an externally supplied `run_id`;
- store execution time using the `America/Montreal` timezone.

### `src/generate_quality_report.py`

Responsibilities:

- read a specific quality run from DuckDB;
- generate report metrics;
- generate Matplotlib charts;
- generate the static HTML report;
- write generated files under `docs/`;
- display validation and generation timestamps using Montréal time;
- support an externally supplied `run_id`.

### `tests/test_pipeline.py`

Responsibilities:

- construct a synthetic GTFS fixture;
- execute the pipeline in a temporary project root;
- verify that a valid fixture passes all implemented rules;
- verify that the HTML report and chart files are generated;
- verify that an intentionally invalid stop sequence is detected by `DQ010`;
- avoid modifying the developer’s real local DuckDB database.

## Data architecture

The project separates data into three main layers.

### Raw layer

Raw tables preserve GTFS source data as loaded.

Examples:

```text
raw_agency
raw_calendar
raw_calendar_dates
raw_feed_info
raw_routes
raw_shapes
raw_stop_times
raw_stops
raw_trips
```

### Analytical layer

Analytical tables provide typed and simplified data for validation and reporting.

Examples:

```text
dim_route
dim_stop
dim_service
dim_trip
fct_scheduled_stop_time
meta_gtfs_feed
```

### Data quality layer

Quality tables preserve rule definitions and execution history.

Examples:

```text
dq_rule
dq_run
dq_result
```

## Coding conventions

Follow these coding rules:

- Use Python type hints.
- Use `pathlib.Path` for filesystem paths.
- Prefer standard-library modules when they are sufficient.
- Keep functions focused and reasonably small.
- Use clear function and variable names.
- Include actionable error messages.
- Avoid unnecessary dependencies.
- Avoid global mutable state.
- Do not hard-code developer-specific absolute paths.
- Support `MONTREAL_TRANSIT_PROJECT_ROOT`.
- Use `pytz` for `America/Montreal` timezone handling on Windows.
- Preserve compatibility with Python 3.11.
- Preserve compatibility with both Windows and Ubuntu.
- Use parameterized DuckDB queries when values come from variables.
- Close DuckDB connections reliably.
- Do not silently ignore data or pipeline failures.
- Verify generated output when one pipeline stage depends on another.
- Keep generated HTML deterministic except for expected run metadata and timestamps.

## GTFS rules

Apply these domain rules:

- GTFS time values greater than `23:59:59` are valid.
- Values such as `24:15:00` and `25:30:00` must not be rejected solely because the hour is above 23.
- Raw source data should remain unchanged in raw tables.
- Type conversion belongs in the analytical layer.
- Static GTFS and GTFS-Realtime data must remain clearly separated.
- Real-time messages should be preserved in a raw form before analytical transformation.
- GTFS identifiers should be treated as strings unless the specification clearly defines another type.
- Every ingestion and validation execution should remain traceable.
- Do not assume that a newly downloaded archive necessarily contains a newer STM feed version.
- Distinguish the local refresh timestamp from the GTFS publisher’s feed version and service period.

## Data quality rules

The current Version 1 controls include:

```text
DQ001  Required fields are populated in scheduled stop times
DQ002  Trip stop sequences are unique
DQ003  Scheduled stop times reference valid trips
DQ004  Scheduled stop times reference valid stops
DQ005  Scheduled times use a valid GTFS format
DQ006  Departure is not earlier than arrival
DQ007  Trips have at least one scheduled stop
DQ008  Routes have at least one planned trip
DQ009  Stop coordinates are plausible for the STM service area
DQ010  Stop sequence is positive
```

When adding a quality rule:

1. assign a stable rule identifier;
2. define its severity;
3. document its purpose;
4. store its results in DuckDB;
5. add a passing test;
6. add a failing test;
7. update `docs/data_quality_rules.md`;
8. update the report when the new metric should be visible;
9. update the README if the project scope changes.

## Testing conventions

Integration tests must:

- run in an isolated temporary project root;
- avoid network access;
- avoid the full STM GTFS dataset;
- avoid modifying the real local warehouse;
- use deterministic synthetic fixtures;
- validate both successful and failure scenarios;
- provide readable failure messages;
- work on Windows and Ubuntu.

Do not make the GitHub Actions validation workflow depend on:

- STM website availability;
- the STM developer portal;
- large GTFS downloads;
- local secrets;
- a local DuckDB database;
- GitHub Pages availability.

Tests should validate application behaviour rather than exact cosmetic wording whenever possible.

For generated HTML, prefer assertions on:

- the readiness state;
- the report title;
- expected rule identifiers;
- expected run identifiers;
- required output files.

Avoid fragile assertions on capitalization or minor presentation wording unless that wording is a required public contract.

## Security and secrets

Never commit:

- STM API keys;
- `.env` files containing secrets;
- downloaded GTFS ZIP archives;
- extracted raw STM files;
- the local DuckDB database;
- local credentials;
- temporary test directories.

Never print or log an API key.

Never include an API key in:

- exception messages;
- report output;
- test output;
- URLs stored in logs;
- generated documentation.

Use environment variables for secrets.

An example environment file may contain placeholder values only.

## Generated and local files

The following are local or generated data assets and should remain excluded from Git when applicable:

```text
data/archive/
data/raw/
data/warehouse/
*.duckdb
*.wal
.env
```

The following generated public report assets are intended to be versioned:

```text
docs/index.html
docs/assets/rules_by_status.png
docs/assets/rules_by_severity.png
```

Screenshots under `docs/assets/screenshots/` are manually curated README assets and should not be overwritten automatically unless explicitly requested.

## Documentation requirements

Update documentation whenever any of these change:

- setup instructions;
- dependencies;
- pipeline commands;
- table schemas;
- quality rules;
- generated report content;
- environment variables;
- refresh workflow;
- GitHub Actions workflow;
- project scope or roadmap.

Relevant documentation files include:

```text
README.md
docs/data_model.md
docs/data_quality_rules.md
AGENTS.md
```

Keep documentation in English unless explicitly requested otherwise, because the public project currently uses English documentation.

## Git and GitKraken workflow

The user manages branches, staging, commits, pushes, pulls, and merges through GitKraken.

Do not assume that Git command-line operations should be performed.

Do not execute Git write operations unless explicitly requested.

In particular, do not automatically:

- create or switch branches;
- stage files;
- create commits;
- amend commits;
- push changes;
- pull or rebase;
- merge branches;
- reset files;
- delete branches;
- rewrite history.

After modifying files:

1. list the files changed;
2. summarize the important changes;
3. show or describe the diff;
4. run the required validation commands;
5. report the test results;
6. leave staging, committing, and pushing to the user in GitKraken.

When suggesting a commit, provide only a recommended commit message, for example:

```text
feat: add GTFS-Realtime configuration foundation
```

Do not create the commit unless the user explicitly asks.

## Codex working method

Before editing:

1. read `AGENTS.md`;
2. inspect the relevant implementation files;
3. inspect the relevant tests;
4. summarize the current behaviour;
5. identify assumptions and risks;
6. propose the files that would change.

During editing:

- keep the change focused;
- avoid unrelated refactoring;
- preserve existing public behaviour unless the task requires a change;
- add or update tests;
- update documentation when necessary.

After editing:

1. list changed files;
2. summarize the implementation;
3. report validation commands executed;
4. report exact test results;
5. identify remaining limitations;
6. do not stage or commit through Git;
7. let the user review and manage the changes in GitKraken.

## Current next phase

The next major phase is GTFS-Realtime foundation.

Implement it incrementally in this order:

1. configuration and secret handling;
2. local raw real-time storage conventions;
3. controlled network capture;
4. raw response preservation;
5. protobuf parsing;
6. DuckDB raw real-time tables;
7. feed freshness controls;
8. feed completeness controls;
9. scheduled versus real-time identifier matching;
10. planned versus observed service comparison;
11. service reliability indicators;
12. real-time HTML reporting.

The first GTFS-Realtime task must not calculate punctuality.

The first deliverable should establish:

- configuration;
- environment-variable secret handling;
- raw-storage conventions;
- validation;
- testability;
- documentation.

## Out of scope for the first GTFS-Realtime task

Do not implement these in the first task:

- punctuality metrics;
- delay prediction;
- anomaly detection;
- passenger occupancy analytics;
- Power BI dashboards;
- complex scheduling;
- cloud deployment;
- streaming infrastructure;
- Kafka;
- Spark;
- new orchestration platforms.

Keep the first Version 2 increment small, testable, and reversible.

## GTFS-Realtime foundation conventions

The nonsecret GTFS-Realtime configuration is stored at:

```text
config/gtfs_realtime.json
```

The API key is supplied only through:

```text
STM_GTFS_REALTIME_API_KEY
```

Do not automatically load `.env` files or add a dotenv dependency. Keep
static GTFS and GTFS-Realtime modules and storage paths separate.

Real-time raw-storage paths must remain under:

```text
data/raw/gtfs_realtime/stm/
```

Configuration loading and path derivation must not create directories, make
network requests, or expose the API key through logs, exceptions, object
representations, metadata, documentation, or reports.

The confirmed STM GTFS-Realtime request contract uses:

```text
Authentication header: apiKey
Accept header: application/x-protobuf
```

Do not add a Bearer prefix. Never copy, store, or publish Swagger-generated
commands containing credentials. GTFS-Realtime tests must remain network-free,
and raw responses must remain outside Git.

Preserve STM attribution and the Creative Commons Attribution 4.0 licence
notice. Do not imply STM affiliation, sponsorship, or endorsement, and do not
use STM logos or trademarks in a way that suggests endorsement. Metro schedule
data must not be presented as an official public metro schedule application.

## GTFS-Realtime capture conventions

One-shot capture must:

- make exactly one HTTPS GET request for the selected configured feed;
- reject all redirects so the `apiKey` header is never resent;
- stream response bytes without parsing or normalization;
- enforce `maximum_response_bytes` before and during streaming;
- preserve payload bytes unchanged and calculate SHA-256 incrementally;
- write payload and nonsecret metadata as one atomic logical pair;
- refuse to overwrite existing captures and clean temporary or orphan files;
- keep project-relative paths and exclude all raw captures from Git;
- keep tests deterministic, synthetic, isolated, and network-free.

Capture metadata must never contain credentials, request headers, cookies,
environment dumps, absolute local paths, or complete response headers.

## GTFS-Realtime parser conventions

Parsing must verify capture metadata, payload size, and SHA-256 before
decoding. Preserve raw protobuf bytes unchanged and keep decoded values in
immutable normalized Python models.

Preserve missing optional fields as absent values. Preserve original Unix
timestamps and derive timezone-aware UTC datetimes only in explicitly named
fields. Preserve enum numeric values alongside readable names. Do not discard
unsupported entity types silently.

Parser tests must use synthetic in-memory protobuf messages, isolated project
roots, and network tripwires. The parser layer must not persist decoded data,
modify static GTFS, or calculate delay, punctuality, or reliability metrics.

## GTFS-Realtime feed-quality conventions

Measure historical freshness relative to capture metadata `captured_at_utc`,
not the current wall clock. Local capture intervals are collector-operation
metrics and must not automatically be described as provider freshness or
availability.

Exclude deleted entities from business completeness denominators. Undefined
ratios remain null and `NOT_APPLICABLE`. Preserve original timestamp integers
and derived timezone-aware UTC values separately.

Keep pure quality calculation separate from DuckDB persistence. Feed-quality
tests use synthetic protobuf messages, isolated roots, and temporary DuckDB
databases. The feed-quality layer must not perform static matching or calculate
scheduled delay, punctuality, or service reliability.

For read-only audits and planning tasks, do not run shell commands, tests,
or validation workflows unless the user explicitly requests them.
