# Interactive GTFS-Realtime Reliability Reporting

## Purpose

The reporting layer turns one persisted reliability run into a self-contained
interactive HTML dashboard for local exploration and static GitHub Pages
hosting. It does not parse protobuf, run matching, recalculate reliability
classifications, or contact STM.

The dashboard is a controlled feasibility view. One or a few manual captures
do not represent continuous or comprehensive system-wide reliability.

## Architecture and data sources

`src/generate_gtfs_realtime_dashboard.py` orchestrates the CLI and opens the
warehouse read-only. `src/gtfs_realtime_reporting.py` retrieves persisted
reliability, matching, optional feed-quality, route-name, and stop-name facts,
then creates and atomically writes HTML.

The browser reads only embedded presentation data. It does not query DuckDB,
call an API, require a backend, or require an STM key. Reporting schema 1 and
generator version 1.1 are separate from persistence, matching, and reliability
algorithm versions.

The default `public` profile embeds one canonical presentation model rather
than the complete analytical dataset. It retains all route-direction
aggregates, up to 200 stop aggregates, up to 200 trip summaries, and compact
delay-histogram bins. Complete event, stop, and trip facts remain available in
DuckDB and are not duplicated into the published HTML.

## Dashboard sections

The report contains overview and lineage; coverage and confidence; separate
arrival/departure punctuality and delay distributions; route, stop, and trip
views; observed schedule relationships; optional feed-quality context; and
methodology. Service-performance and coverage sections use different visual
containers and wording. Missing ratios display `N/A`, and low-sample results
are not ranked.

## Filters and tables

Native client-side controls filter route, direction, event type,
interpretation status, and delay-histogram classification. Route, stop, and
trip tables provide search, column sorting, scrollable bounded output, and
stable source ordering. Stops and trips are ranked by classified observation
count descending, then eligible observation count descending, followed by
stable identifiers. The UI reports source and embedded row counts whenever a
table is truncated. No interaction triggers a server request.

## Metric semantics

The dashboard consumes classifications and aggregate metrics exactly as
persisted. It keeps arrivals and departures separate and displays the policy
thresholds documented in
[GTFS-Realtime Service Reliability Indicators](gtfs_realtime_reliability.md).
Histogram bins are calculated from persisted selected deltas and persisted
punctuality classifications while the warehouse is open read-only. Binning and
unit conversion are presentation transformations only; classifications and
reliability indicators are not recalculated.

Only explicit observed `CANCELED` relationships are called reported
cancellations. Feed absence is not a cancellation. Stop metrics are not
passenger-experience measures, and no headway, passenger-weighted, or composite
index is displayed.

## Security and privacy

GTFS strings are serialized with `<`, `>`, and `&` escaped inside the JSON
script payload. Client code assigns dynamic values with `textContent`, never
source-derived HTML. Automated tests include script-like route and stop names.

The embedded dataset excludes credentials, environment values, request
headers, raw protobuf, complete capture metadata, absolute paths, licence
plates, and unnecessary vehicle identifiers.

## HTML generation

```powershell
python .\src\generate_gtfs_realtime_dashboard.py `
    --reliability-run-id <RELIABILITY_RUN_ID> `
    --profile public
```

The default output is `docs/gtfs_realtime_reliability.html`. Use `--warehouse`,
`--output`, and `--open` when needed. The output directory is created only
after source validation and HTML construction. A temporary sibling file is
atomically moved over the explicit destination; failure leaves no partial file
and preserves an existing dashboard.

The CLI reports the profile, output size, and embedded route, stop, trip, and
histogram-bin counts. Public output is checked against a 10 MiB project
publication policy before atomic replacement. This is a repository policy,
not a claim about an external GitHub service limit.

## GitHub Pages publication

The existing `docs/index.html` remains the static GTFS quality report and links
to `docs/gtfs_realtime_reliability.html`. Commit the generated reliability HTML
only after reviewing a controlled demonstration and its limitations. The
dashboard contains all required CSS and JavaScript and uses no CDN. Its compact
presentation data is intended to keep the committed artifact practical for
repository review and GitHub Pages delivery.

## Screenshot workflow

After a real controlled demonstration is reviewed, capture:

- `docs/assets/reliability/dashboard-overview.png`;
- `docs/assets/reliability/dashboard-coverage.png`;
- `docs/assets/reliability/dashboard-routes.png`.

Screenshots must come from the actual generated dashboard. Crop out local
paths, browser identity, developer tools, vehicle identifiers, and any secret.
Do not create placeholder images or README links before the files exist.

## Controlled live feasibility procedure

The manual operator should verify static coverage for the current Montréal
service date, capture Vehicle Positions and Trip Updates once each, ingest and
quality-check them, match eligible captures, calculate reliability, generate
the dashboard, and record capture/match/reliability identifiers. Before
publication, search persisted and generated output for the API key and confirm
the dashboard is labelled as a limited feasibility demonstration.

This procedure is manual and is never part of automated tests or CI.

## Limitations

The first reporting increment accepts one explicit reliability run. It does
not provide multi-run trends, controlled capture-availability measurement,
headways, excess wait time, travel-time reliability, passenger weighting,
production monitoring, or live browser data refresh.
