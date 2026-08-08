from __future__ import annotations

import html
import json
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import duckdb


REPORTING_SCHEMA_VERSION = 1
REPORTING_GENERATOR_VERSION = "1.1"
CLASSIFICATION_ORDER = ("EARLY", "ON_TIME", "LATE", "VERY_LATE", "UNCLASSIFIED")
PUBLIC_PROFILE = "public"
PUBLIC_STOP_LIMIT = 200
PUBLIC_TRIP_LIMIT = 200
PUBLIC_MAXIMUM_BYTES = 10 * 1024 * 1024


class ReportingError(RuntimeError):
    """A concise, secret-safe dashboard generation failure."""


@dataclass(frozen=True)
class ReportingData:
    run: dict[str, Any]
    system_aggregates: tuple[dict[str, Any], ...]
    route_aggregates: tuple[dict[str, Any], ...]
    stop_aggregates: tuple[dict[str, Any], ...]
    trips: tuple[dict[str, Any], ...]
    histogram_bins: tuple[dict[str, Any], ...]
    coverage: dict[str, Any]
    relationships: tuple[dict[str, Any], ...]
    quality_context: tuple[dict[str, Any], ...]
    source_stop_count: int
    source_trip_count: int


def _table_exists(connection: duckdb.DuckDBPyConnection, table: str) -> bool:
    return connection.execute(
        "SELECT count(*) FROM information_schema.tables WHERE table_name = ?", [table]
    ).fetchone()[0] == 1


def _rows(connection: duckdb.DuckDBPyConnection, query: str,
          parameters: list[object] | None = None) -> tuple[dict[str, Any], ...]:
    cursor = connection.execute(query, parameters or [])
    columns = [item[0] for item in cursor.description]
    return tuple(dict(zip(columns, row)) for row in cursor.fetchall())


def _first(connection: duckdb.DuckDBPyConnection, query: str,
           parameters: list[object] | None = None) -> dict[str, Any] | None:
    rows = _rows(connection, query, parameters)
    return rows[0] if rows else None


def _quality_context_row(row: dict[str, Any]) -> dict[str, Any]:
    """Adapt the persisted feed-quality schema to the public report model."""
    return {
        "rule_id": row["rule_id"],
        "status": row["status"],
        "metric_name": row["metric_name"],
        "numeric_value": row["numeric_value"],
        "numerator": row["numerator"],
        "denominator": row["denominator"],
        "ratio": row["ratio"],
        "threshold": row["threshold"],
        "threshold_operator": row["threshold_operator"],
        "unit": row["unit"],
    }


def load_reporting_data(connection: duckdb.DuckDBPyConnection,
                        reliability_run_id: str) -> ReportingData:
    required = (
        "gtfs_realtime_reliability_run", "gtfs_realtime_reliability_event",
        "gtfs_realtime_reliability_trip", "gtfs_realtime_reliability_aggregate",
        "gtfs_realtime_reliability_finding", "gtfs_realtime_match_run",
        "gtfs_realtime_trip_match", "gtfs_realtime_stop_time_match",
    )
    missing = [table for table in required if not _table_exists(connection, table)]
    if missing:
        raise ReportingError("Required reporting tables are missing: " + ", ".join(missing) + ".")
    run = _first(connection, """SELECT * FROM gtfs_realtime_reliability_run
        WHERE reliability_run_id = ?""", [reliability_run_id])
    if run is None:
        raise ReportingError("GTFS-Realtime reliability run was not found.")
    match_run_id = str(run["match_run_id"])
    route_names: dict[str, str] = {}
    stop_names: dict[str, str] = {}
    if _table_exists(connection, "dim_route"):
        route_names = {str(row[0]): " – ".join(str(value) for value in row[1:] if value)
                       for row in connection.execute(
                           "SELECT route_id, route_short_name, route_long_name FROM dim_route"
                       ).fetchall()}
    if _table_exists(connection, "dim_stop"):
        stop_names = {str(row[0]): str(row[1]) for row in connection.execute(
            "SELECT stop_id, stop_name FROM dim_stop"
        ).fetchall()}
    aggregates = _rows(connection, """SELECT dimension_type, service_date, route_id,
        direction_id, stop_id, event_type, eligible_event_count, classified_event_count,
        early_count, on_time_count, late_count, very_late_count, unclassified_count,
        early_ratio, on_time_ratio, late_ratio, very_late_ratio, median_delay_seconds,
        p90_delay_seconds, p95_delay_seconds, interpretation_status, trip_matching_ratio,
        stop_matching_ratio, comparison_availability_ratio, classification_ratio
        FROM gtfs_realtime_reliability_aggregate
        WHERE reliability_run_id = ? AND dimension_type IN
            ('SYSTEM_CAPTURE_SCOPE', 'ROUTE_DIRECTION')
        ORDER BY dimension_type, service_date, route_id, direction_id, event_type,
        aggregate_index""", [reliability_run_id])
    routes: list[dict[str, Any]] = []
    for item in aggregates:
        enriched = dict(item)
        if item["route_id"] is not None:
            enriched["route_name"] = route_names.get(str(item["route_id"]), "")
        if item["dimension_type"] == "ROUTE_DIRECTION":
            routes.append(enriched)
    source_stop_count = connection.execute("""SELECT count(*)
        FROM gtfs_realtime_reliability_aggregate
        WHERE reliability_run_id = ? AND dimension_type = 'STOP_ROUTE_DIRECTION'""",
        [reliability_run_id]).fetchone()[0]
    stop_rows = _rows(connection, """SELECT service_date, stop_id, route_id,
        direction_id, event_type, eligible_event_count, classified_event_count,
        on_time_ratio, median_delay_seconds, p95_delay_seconds, interpretation_status
        FROM gtfs_realtime_reliability_aggregate
        WHERE reliability_run_id = ? AND dimension_type = 'STOP_ROUTE_DIRECTION'
        ORDER BY classified_event_count DESC, eligible_event_count DESC, stop_id,
        route_id, direction_id, event_type, aggregate_index LIMIT ?""",
        [reliability_run_id, PUBLIC_STOP_LIMIT])
    stops = tuple({**item, "stop_name": stop_names.get(str(item["stop_id"]), "")}
                  for item in stop_rows)
    source_trip_count = connection.execute("""SELECT count(*)
        FROM gtfs_realtime_reliability_trip WHERE reliability_run_id = ?""",
        [reliability_run_id]).fetchone()[0]
    trips = _rows(connection, """SELECT service_date, static_trip_id, static_route_id,
        direction_id, event_type, eligible_event_count, classified_event_count,
        on_time_ratio, maximum_lateness_seconds, median_delay_seconds,
        start_delay_seconds, end_delay_seconds, delay_change_seconds, any_very_late,
        reported_cancellation, coverage_status
        FROM gtfs_realtime_reliability_trip WHERE reliability_run_id = ?
        ORDER BY classified_event_count DESC, eligible_event_count DESC, service_date,
        static_route_id, direction_id, static_trip_id, event_type LIMIT ?""",
        [reliability_run_id, PUBLIC_TRIP_LIMIT])
    histogram_bins = _rows(connection, """WITH eligible AS (
            SELECT punctuality_classification,
                CASE WHEN selected_delta_seconds <= -600 THEN 0
                     WHEN selected_delta_seconds <= -300 THEN 1
                     WHEN selected_delta_seconds <= 0 THEN 2
                     WHEN selected_delta_seconds <= 300 THEN 3
                     WHEN selected_delta_seconds <= 600 THEN 4
                     WHEN selected_delta_seconds <= 900 THEN 5
                     WHEN selected_delta_seconds <= 1800 THEN 6 ELSE 7 END AS bin_index
            FROM gtfs_realtime_reliability_event
            WHERE reliability_run_id = ? AND eligibility_status = 'ELIGIBLE'
              AND selected_delta_seconds IS NOT NULL
        )
        SELECT punctuality_classification, bin_index, count(*) AS observation_count
        FROM eligible GROUP BY punctuality_classification, bin_index
        ORDER BY bin_index, punctuality_classification""", [reliability_run_id])
    match = _first(connection, "SELECT * FROM gtfs_realtime_match_run WHERE match_run_id = ?",
                   [match_run_id])
    if match is None:
        raise ReportingError("Reliability run references a missing matching run.")
    stop_counts = _first(connection, """SELECT count(*) AS stop_time_update_count,
        count(*) FILTER (WHERE match_status = 'MATCHED') AS matched_stop_count,
        count(*) FILTER (WHERE match_status = 'UNMATCHED') AS unmatched_stop_count,
        count(*) FILTER (WHERE match_status = 'AMBIGUOUS') AS ambiguous_stop_count,
        count(*) FILTER (WHERE match_status = 'CONFLICT') AS conflict_stop_count
        FROM gtfs_realtime_stop_time_match WHERE match_run_id = ?""", [match_run_id]) or {}
    coverage_by_event = {str(item["event_type"]): item for item in aggregates
                         if item["dimension_type"] == "SYSTEM_CAPTURE_SCOPE"}
    coverage_aggregate = next(iter(coverage_by_event.values()), None)
    coverage = {
        "source_realtime_entities": match["entity_count"],
        "matched_trips": match["matched_count"],
        "unmatched_trips": match["unmatched_count"],
        "ambiguous_trips": match["ambiguous_count"],
        "conflict_trips": match["conflict_count"],
        "unsupported_trips": match["unsupported_count"],
        **stop_counts,
        "candidate_observations": run["source_observation_count"],
        "canonical_events": run["canonical_event_count"],
        "eligible_events": run["eligible_event_count"],
        "classified_events": run["classified_event_count"],
        "trip_matching_ratio": coverage_aggregate["trip_matching_ratio"] if coverage_aggregate else None,
        "stop_matching_ratio": coverage_aggregate["stop_matching_ratio"] if coverage_aggregate else None,
        "arrival_comparison_availability_ratio": coverage_by_event.get("ARRIVAL", {}).get("comparison_availability_ratio"),
        "departure_comparison_availability_ratio": coverage_by_event.get("DEPARTURE", {}).get("comparison_availability_ratio"),
        "arrival_classification_ratio": coverage_by_event.get("ARRIVAL", {}).get("classification_ratio"),
        "departure_classification_ratio": coverage_by_event.get("DEPARTURE", {}).get("classification_ratio"),
    }
    relationships = _rows(connection, """SELECT coalesce(schedule_relationship_name, 'ABSENT')
        AS relationship, count(*) AS observation_count
        FROM gtfs_realtime_trip_match WHERE match_run_id = ?
        GROUP BY relationship ORDER BY relationship""", [match_run_id])
    quality_context: tuple[dict[str, Any], ...] = ()
    if _table_exists(connection, "gtfs_realtime_quality_run") and _table_exists(connection, "gtfs_realtime_quality_result"):
        quality_run = _first(connection, """SELECT q.quality_run_id, q.overall_status
            FROM gtfs_realtime_quality_run q JOIN gtfs_realtime_match_run m
              ON q.capture_uuid = m.capture_uuid
            WHERE m.match_run_id = ? ORDER BY q.analyzed_at_utc DESC, q.quality_run_id DESC LIMIT 1""",
            [match_run_id])
        if quality_run:
            quality_rows = _rows(connection, """SELECT rule_id, status,
                metric_name, numeric_value, numerator, denominator, ratio, threshold,
                threshold_operator, unit
                FROM gtfs_realtime_quality_result WHERE quality_run_id = ?
                ORDER BY result_index""", [quality_run["quality_run_id"]])
            quality_context = tuple(_quality_context_row(row) for row in quality_rows)
            run["feed_quality_status"] = quality_run["overall_status"]
    run["match_run_overall_status"] = match["overall_status"]
    run["capture_uuid"] = match["capture_uuid"]
    return ReportingData(
        run, tuple(item for item in aggregates if item["dimension_type"] == "SYSTEM_CAPTURE_SCOPE"),
        tuple(routes), stops, trips, histogram_bins, coverage, relationships,
        quality_context, int(source_stop_count), int(source_trip_count),
    )


def _json_default(value: object) -> str:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    raise TypeError(f"Unsupported dashboard value type: {type(value).__name__}")


def safe_json(data: object) -> str:
    serialized = json.dumps(data, default=_json_default, ensure_ascii=False,
                            sort_keys=True, separators=(",", ":"))
    return (serialized.replace("&", "\\u0026").replace("<", "\\u003c")
            .replace(">", "\\u003e").replace("\u2028", "\\u2028")
            .replace("\u2029", "\\u2029"))


def _public_data(data: ReportingData) -> dict[str, Any]:
    return {
        "run": data.run,
        "overview": data.system_aggregates,
        "routes": data.route_aggregates,
        "stops": data.stop_aggregates,
        "trips": data.trips,
        "histogram": data.histogram_bins,
        "coverage": data.coverage,
        "relationships": data.relationships,
        "quality": data.quality_context,
        "presentation": {
            "profile": PUBLIC_PROFILE,
            "sourceStopRows": data.source_stop_count,
            "sourceTripRows": data.source_trip_count,
            "embeddedStopRows": len(data.stop_aggregates),
            "embeddedTripRows": len(data.trips),
            "stopsTruncated": data.source_stop_count > len(data.stop_aggregates),
            "tripsTruncated": data.source_trip_count > len(data.trips),
        },
        "classificationOrder": CLASSIFICATION_ORDER,
        "reportingSchemaVersion": REPORTING_SCHEMA_VERSION,
        "reportingGeneratorVersion": REPORTING_GENERATOR_VERSION,
    }


def build_dashboard_html(data: ReportingData, generated_at_utc: datetime | None = None) -> str:
    generated = generated_at_utc or datetime.now(timezone.utc)
    run = data.run
    service_dates = sorted({str(item["service_date"]) for item in data.system_aggregates
                            if item["service_date"]})
    payload = safe_json(_public_data(data))
    title = "Montréal Transit Reliability"
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="reporting-schema-version" content="{REPORTING_SCHEMA_VERSION}">
<meta name="reporting-generator-version" content="{REPORTING_GENERATOR_VERSION}">
<title>{html.escape(title)}</title>
<style>
:root{{--ink:#172033;--muted:#596579;--surface:#fff;--page:#f4f6f9;--line:#d9dee8;--accent:#315b7d;--early:#7c3aed;--ontime:#16803c;--late:#d97706;--verylate:#c43131;--info:#e8f1f8}}
*{{box-sizing:border-box}} body{{margin:0;background:var(--page);color:var(--ink);font:15px/1.5 Arial,sans-serif}} a{{color:#164f7a}} header{{background:#172b3a;color:#fff;padding:32px max(20px,calc((100% - 1400px)/2))}} header p{{max-width:900px;color:#dbe7ef}} nav{{display:flex;flex-wrap:wrap;gap:14px;margin-top:18px}} nav a{{color:#fff}} main,footer{{max-width:1400px;margin:auto;padding:20px}} section{{background:var(--surface);border:1px solid var(--line);border-radius:12px;padding:22px;margin:0 0 20px}} h2{{margin-top:0}} .grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:12px}} .kpi{{border:1px solid var(--line);border-radius:10px;padding:14px}} .kpi b{{display:block;font-size:1.55rem;overflow-wrap:anywhere}} .kpi small{{display:block}} .muted{{color:var(--muted)}} .warning{{background:#fff6dc;border-left:5px solid #d99b13;padding:12px}} .coverage{{background:#f5f9fc;border-color:#bcd0df}} .controls{{display:flex;flex-wrap:wrap;gap:10px;margin:12px 0}} label{{font-weight:700}} select,input{{display:block;padding:8px;border:1px solid #aeb8c6;border-radius:6px;min-width:150px}} .charts{{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:14px}} .chart{{border:1px solid var(--line);border-radius:10px;padding:14px;min-height:260px}} svg{{width:100%;height:230px}} .legend{{display:flex;flex-wrap:wrap;gap:12px}} .swatch{{width:12px;height:12px;display:inline-block;margin-right:5px}} .table-wrap{{overflow:auto;max-height:520px;border:1px solid var(--line)}} table{{border-collapse:collapse;width:100%;min-width:850px}} th,td{{padding:9px;border-bottom:1px solid var(--line);text-align:left}} th{{position:sticky;top:0;background:#edf2f6;cursor:pointer}} .badge{{display:inline-block;border-radius:999px;padding:3px 9px;background:var(--info);font-weight:700}} .na{{color:var(--muted)}} details{{margin-top:12px}} footer{{color:var(--muted)}} @media(max-width:600px){{header{{padding:24px 18px}}main{{padding:12px}}section{{padding:15px}}.kpi b{{font-size:1.25rem}}}}
</style>
</head>
<body>
<header>
<h1>{html.escape(title)}</h1>
<p>Interactive comparison of persisted STM GTFS-Realtime observations with scheduled GTFS service.</p>
<p><strong>Controlled feasibility view:</strong> limited observations do not represent continuous or comprehensive system-wide reliability.</p>
<nav aria-label="Dashboard sections"><a href="index.html">Static GTFS Data Quality</a><a href="#overview">Overview</a><a href="#coverage">Coverage</a><a href="#routes">Routes</a><a href="#methodology">Methodology</a></nav>
</header>
<main>
<section id="overview"><h2>1. Overview</h2><p class="warning">Service-performance metrics apply only to eligible classified observations. Capture coverage is incomplete unless a controlled recurring collection window has been established.</p><div id="overview-kpis" class="grid"></div>
<details><summary>Run and lineage details</summary><dl><dt>Reliability run</dt><dd><code>{html.escape(str(run['reliability_run_id']))}</code></dd><dt>Service date(s)</dt><dd>{html.escape(', '.join(service_dates) or 'N/A')}</dd><dt>Static snapshot</dt><dd><code>{html.escape(str(run['static_snapshot_identifier']))}</code></dd><dt>Reliability algorithm/configuration</dt><dd>{html.escape(str(run['reliability_algorithm_version']))} / {html.escape(str(run['reliability_config_schema_version']))}</dd><dt>Matching algorithm/configuration</dt><dd>{html.escape(str(run['matching_algorithm_version']))} / {html.escape(str(run['matching_config_schema_version']))}</dd><dt>Reporting version</dt><dd>{REPORTING_GENERATOR_VERSION} (schema {REPORTING_SCHEMA_VERSION})</dd><dt>Generated UTC</dt><dd>{html.escape(generated.isoformat())}</dd></dl></details></section>
<section id="coverage" class="coverage"><h2>2. Coverage and data confidence</h2><p>Coverage describes the observations available for analysis; it is not a service-performance score.</p><div id="coverage-kpis" class="grid"></div></section>
<section id="punctuality"><h2>3. Punctuality distribution</h2><div class="legend"><span><i class="swatch" style="background:var(--early)"></i>Early</span><span><i class="swatch" style="background:var(--ontime)"></i>On time</span><span><i class="swatch" style="background:var(--late)"></i>Late</span><span><i class="swatch" style="background:var(--verylate)"></i>Very late</span></div><div class="controls"><label>Delay histogram classification<select id="classification-filter"><option value="">All classified</option><option>EARLY</option><option>ON_TIME</option><option>LATE</option><option>VERY_LATE</option></select></label></div><div class="charts"><div class="chart"><h3>Classified observations</h3><svg id="punctuality-chart" role="img" aria-label="Grouped bar chart of punctuality classification by arrival and departure"></svg><p id="punctuality-summary" class="muted"></p></div><div class="chart"><h3>Route on-time comparison</h3><svg id="route-chart" role="img" aria-label="Route on-time ratio chart excluding low-sample results"></svg><p class="muted">Low-sample and not-applicable routes are not ranked.</p></div></div></section>
<section id="delay"><h2>4. Delay distribution</h2><div class="charts"><div class="chart"><h3>Selected delay histogram (minutes)</h3><svg id="delay-chart" role="img" aria-label="Histogram of selected delays including negative values"></svg></div><div id="delay-kpis" class="grid"></div></div></section>
<section id="routes"><h2>5. Route performance</h2><div id="global-filters" class="controls"></div><div class="controls"><label>Search routes<input id="route-search" type="search"></label></div><div class="table-wrap"><table id="route-table"><thead></thead><tbody></tbody></table></div></section>
<section id="stops"><h2>6. Stop performance</h2><p class="muted">Stop metrics are operational observations, not passenger-experience measures.</p><p id="stop-scope" class="muted"></p><div class="controls"><label>Search stops<input id="stop-search" type="search"></label></div><div class="table-wrap"><table id="stop-table"><thead></thead><tbody></tbody></table></div></section>
<section id="trips"><h2>7. Trip-level examples</h2><p id="trip-scope" class="muted"></p><div class="controls"><label>Search trips<input id="trip-search" type="search"></label></div><div class="table-wrap"><table id="trip-table"><thead></thead><tbody></tbody></table></div></section>
<section id="relationships"><h2>8. Reported cancellations and schedule relationships</h2><p>These are observed GTFS-Realtime relationships. “Reported cancellations observed” is not a complete STM cancellation rate, and feed absence is never treated as cancellation.</p><div id="relationship-kpis" class="grid"></div></section>
<section id="quality"><h2>9. Feed/data-quality context</h2><p>Feed-quality context is displayed separately and is never mixed into punctuality results.</p><div id="quality-table" class="table-wrap"></div></section>
<section id="methodology"><h2>10. Methodology and limitations</h2><ul><li>Project policy: EARLY &lt; -60 seconds; ON_TIME -60 through 300; LATE above 300 through 600; VERY_LATE above 600.</li><li>Calculated absolute-event-time delta is selected first; reported delay is the fallback.</li><li>Arrivals and departures use separate observations and denominators.</li><li>Canonical observations are selected deterministically from persisted reliability facts.</li><li>The public dashboard embeds persisted aggregates, compact histogram bins, and deterministic bounded stop/trip examples; complete event-level facts remain in DuckDB.</li><li>Matching requires complete persistence and static-snapshot lineage.</li><li>Reported cancellations include only explicit observed CANCELED relationships.</li><li>No passenger weighting, headway adherence, excess wait time, or composite index is included.</li><li>Manual or sparse captures demonstrate technical feasibility only.</li></ul><p><a href="gtfs_realtime_reporting.md">Full reporting methodology</a> · <a href="gtfs_realtime_reliability.md">Reliability calculation methodology</a></p></section>
</main>
<footer><p>Data source: Société de transport de Montréal (STM).</p><p>This is an independent and unofficial portfolio project and is not affiliated with or endorsed by the Société de transport de Montréal (STM). Data is provided as-is and according to availability.</p></footer>
<script id="dashboard-data" type="application/json">{payload}</script>
<script>
'use strict';
const D=JSON.parse(document.getElementById('dashboard-data').textContent); const COLORS={{EARLY:'#7c3aed',ON_TIME:'#16803c',LATE:'#d97706',VERY_LATE:'#c43131'}};
const M=D.presentation;document.getElementById('stop-scope').textContent=M.stopsTruncated?`Showing ${{M.embeddedStopRows}} of ${{M.sourceStopRows}} stop aggregate rows, ranked by classified then eligible observations.`:`Showing all ${{M.sourceStopRows}} stop aggregate rows.`;document.getElementById('trip-scope').textContent=M.tripsTruncated?`Showing ${{M.embeddedTripRows}} of ${{M.sourceTripRows}} trip summaries, ranked by classified then eligible observations.`:`Showing all ${{M.sourceTripRows}} trip summaries.`;
const fmt=v=>v===null||v===undefined?'N/A':String(v); const pct=v=>v===null||v===undefined?'N/A':(100*Number(v)).toFixed(1)+'%'; const mins=v=>v===null||v===undefined?'N/A':(Number(v)/60).toFixed(1)+' min';
function card(label,value,note=''){{const x=document.createElement('div');x.className='kpi';const b=document.createElement('b');b.textContent=value;const s=document.createElement('span');s.textContent=label;x.append(b,s);if(note){{const p=document.createElement('small');p.className='muted';p.textContent=note;x.append(p)}}return x}}
function addCards(id,items){{const el=document.getElementById(id);items.forEach(x=>el.append(card(...x)))}}
const byType=t=>D.overview.find(x=>x.event_type===t)||{{}}; const A=byType('ARRIVAL'),P=byType('DEPARTURE');
addCards('overview-kpis',[["Eligible / classified",D.run.eligible_event_count+' / '+D.run.classified_event_count],["Arrival on-time",pct(A.on_time_ratio)],["Departure on-time",pct(P.on_time_ratio)],["Arrival median / p95",mins(A.median_delay_seconds)+' / '+mins(A.p95_delay_seconds)],["Departure median / p95",mins(P.median_delay_seconds)+' / '+mins(P.p95_delay_seconds)],["Interpretation",fmt(D.run.overall_status)]]);
const C=D.coverage; addCards('coverage-kpis',[["Source entities",fmt(C.source_realtime_entities)],["Matched / unmatched trips",fmt(C.matched_trips)+' / '+fmt(C.unmatched_trips)],["Ambiguous / conflict trips",fmt(C.ambiguous_trips)+' / '+fmt(C.conflict_trips)],["StopTimeUpdates / matched",fmt(C.stop_time_update_count)+' / '+fmt(C.matched_stop_count)],["Candidate / canonical events",fmt(C.candidate_observations)+' / '+fmt(C.canonical_events)],["Trip matching",pct(C.trip_matching_ratio)],["Stop matching",pct(C.stop_matching_ratio)],["Arrival comparison / classification",pct(C.arrival_comparison_availability_ratio)+' / '+pct(C.arrival_classification_ratio)],["Departure comparison / classification",pct(C.departure_comparison_availability_ratio)+' / '+pct(C.departure_classification_ratio)],["Collector coverage","Incomplete / not measured","Requires controlled recurring capture"]]);
addCards('delay-kpis',[["Arrival median",mins(A.median_delay_seconds)],["Arrival p90 / p95",mins(A.p90_delay_seconds)+' / '+mins(A.p95_delay_seconds)],["Departure median",mins(P.median_delay_seconds)],["Departure p90 / p95",mins(P.p90_delay_seconds)+' / '+mins(P.p95_delay_seconds)]]);
addCards('relationship-kpis',D.relationships.map(x=>[x.relationship==='CANCELED'?'Reported cancellations observed':x.relationship,fmt(x.observation_count)]));
function svgBar(svgId,groups,valueKeys){{const svg=document.getElementById(svgId),w=600,h=210,pad=42,step=(w-2*pad)/Math.max(1,groups.length),bw=Math.max(6,Math.min(28,(step-6)/valueKeys.length));svg.setAttribute('viewBox',`0 0 ${{w}} ${{h}}`);while(svg.firstChild)svg.removeChild(svg.firstChild);const vals=groups.flatMap(g=>valueKeys.map(k=>Number(g[k]||0))),max=Math.max(1,...vals);groups.forEach((g,gi)=>valueKeys.forEach((k,ki)=>{{const x=pad+gi*step+ki*(bw+2),y=h-pad-(Number(g[k]||0)/max)*(h-2*pad);const r=document.createElementNS('http://www.w3.org/2000/svg','rect');r.setAttribute('x',x);r.setAttribute('y',y);r.setAttribute('width',bw);r.setAttribute('height',h-pad-y);r.setAttribute('fill',COLORS[k]||'#315b7d');const title=document.createElementNS('http://www.w3.org/2000/svg','title');title.textContent=`${{g.event_type||g.route_id}} ${{k}}: ${{g[k]||0}}`;r.append(title);svg.append(r)}}));groups.forEach((g,i)=>{{const t=document.createElementNS('http://www.w3.org/2000/svg','text');t.setAttribute('x',pad+i*step);t.setAttribute('y',h-12);t.textContent=g.event_type||g.route_id;t.setAttribute('font-size','12');svg.append(t)}})}}
svgBar('punctuality-chart',[A,P],['early_count','on_time_count','late_count','very_late_count']);document.getElementById('punctuality-summary').textContent='Exact classified denominators — arrivals: '+fmt(A.classified_event_count)+', departures: '+fmt(P.classified_event_count)+'.';
const ranked=D.routes.filter(x=>x.interpretation_status==='SUFFICIENT_DATA'&&x.on_time_ratio!==null).sort((a,b)=>b.on_time_ratio-a.on_time_ratio||String(a.route_id).localeCompare(String(b.route_id))).slice(0,8).map(x=>({{route_id:x.route_id,event_type:x.event_type,value:Math.round(100*x.on_time_ratio)}}));svgBar('route-chart',ranked,['value']);
function histogram(){{const selected=document.getElementById('classification-filter').value,labels=['≤ -10m','-10 to -5m','-5 to 0m','0 to 5m','5 to 10m','10 to 15m','15 to 30m','> 30m'],rows=labels.map((label,bin_index)=>({{route_id:label,value:D.histogram.filter(x=>x.bin_index===bin_index&&(!selected||x.punctuality_classification===selected)).reduce((sum,x)=>sum+Number(x.observation_count),0)}}));svgBar('delay-chart',rows,['value'])}} document.getElementById('classification-filter').addEventListener('change',histogram);histogram();
const filterDefs=[['route_id','Route'],['direction_id','Direction'],['event_type','Event type'],['interpretation_status','Status']];const controls=document.getElementById('global-filters');const filters={{}};filterDefs.forEach(([key,label])=>{{const l=document.createElement('label');l.textContent=label;const s=document.createElement('select');s.dataset.key=key;const all=document.createElement('option');all.value='';all.textContent='All';s.append(all);const values=[...new Set(D.routes.map(x=>fmt(x[key])))].sort();values.forEach(v=>{{const o=document.createElement('option');o.value=v;o.textContent=v;s.append(o)}});s.addEventListener('change',renderAll);l.append(s);controls.append(l);filters[key]=s}});
function filtered(rows){{return rows.filter(x=>(!filters.route_id.value||fmt(x.route_id||x.static_route_id)===filters.route_id.value)&&(!filters.direction_id.value||fmt(x.direction_id)===filters.direction_id.value)&&(!filters.event_type.value||fmt(x.event_type)===filters.event_type.value)&&(!filters.interpretation_status.value||fmt(x.interpretation_status||x.coverage_status)===filters.interpretation_status.value))}}
function table(id,rows,columns,searchId){{const el=document.getElementById(id),head=el.querySelector('thead'),body=el.querySelector('tbody');head.replaceChildren();body.replaceChildren();const tr=document.createElement('tr');columns.forEach(([k,l])=>{{const th=document.createElement('th');th.scope='col';th.textContent=l;th.tabIndex=0;th.addEventListener('click',()=>{{rows.sort((a,b)=>fmt(a[k]).localeCompare(fmt(b[k]),undefined,{{numeric:true}}));table(id,rows,columns,searchId)}});tr.append(th)}});head.append(tr);const q=(document.getElementById(searchId)?.value||'').toLowerCase();filtered(rows).filter(x=>!q||JSON.stringify(x).toLowerCase().includes(q)).slice(0,200).forEach(row=>{{const r=document.createElement('tr');columns.forEach(([k])=>{{const td=document.createElement('td');const v=k.endsWith('_ratio')?pct(row[k]):k.includes('delay')||k.includes('lateness')?mins(row[k]):fmt(row[k]);td.textContent=v;if(v==='N/A')td.className='na';r.append(td)}});body.append(r)}})}}
const routeCols=[['route_id','Route'],['route_name','Name'],['direction_id','Direction'],['event_type','Event'],['classified_event_count','Classified'],['on_time_ratio','On time'],['early_ratio','Early'],['late_ratio','Late'],['very_late_ratio','Very late'],['median_delay_seconds','Median'],['p95_delay_seconds','P95'],['comparison_availability_ratio','Comparison coverage'],['interpretation_status','Status']];
const stopCols=[['stop_id','Stop'],['stop_name','Name'],['route_id','Route'],['direction_id','Direction'],['event_type','Event'],['classified_event_count','Classified'],['on_time_ratio','On time'],['median_delay_seconds','Median'],['p95_delay_seconds','P95'],['interpretation_status','Status']];
const tripCols=[['service_date','Service date'],['static_trip_id','Trip'],['static_route_id','Route'],['direction_id','Direction'],['event_type','Event'],['classified_event_count','Classified'],['on_time_ratio','On time'],['maximum_lateness_seconds','Maximum lateness'],['median_delay_seconds','Median'],['start_delay_seconds','Start'],['end_delay_seconds','End'],['delay_change_seconds','Change'],['any_very_late','Very late'],['reported_cancellation','Reported canceled'],['coverage_status','Coverage']];
function renderAll(){{table('route-table',D.routes,routeCols,'route-search');table('stop-table',D.stops,stopCols,'stop-search');table('trip-table',D.trips,tripCols,'trip-search')}}['route-search','stop-search','trip-search'].forEach(id=>document.getElementById(id).addEventListener('input',renderAll));renderAll();
const q=document.getElementById('quality-table');if(!D.quality.length){{q.textContent='Optional feed-quality context is not available for this run.'}}else{{const t=document.createElement('table'),h=document.createElement('thead'),b=document.createElement('tbody'),cols=[['rule_id','Rule'],['metric_name','Metric'],['status','Status'],['numeric_value','Value'],['numerator','Numerator'],['denominator','Denominator'],['ratio','Ratio'],['threshold_operator','Operator'],['threshold','Threshold'],['unit','Unit']];const hr=document.createElement('tr');cols.forEach(c=>{{const th=document.createElement('th');th.scope='col';th.textContent=c[1];hr.append(th)}});h.append(hr);D.quality.forEach(x=>{{const r=document.createElement('tr');cols.forEach(c=>{{const td=document.createElement('td');td.textContent=fmt(x[c[0]]);r.append(td)}});b.append(r)}});t.append(h,b);q.append(t)}}
</script>
</body></html>"""


def write_dashboard_atomic(output_path: Path, dashboard_html: str) -> None:
    output = output_path.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.parent / f".{output.name}.{uuid.uuid4().hex}.tmp"
    try:
        temporary.write_text(dashboard_html, encoding="utf-8", newline="\n")
        temporary.replace(output)
    finally:
        temporary.unlink(missing_ok=True)
