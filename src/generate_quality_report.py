from __future__ import annotations

import html
import pytz
import os
from collections import Counter
from datetime import datetime
from pathlib import Path

import duckdb
import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt


DEFAULT_PROJECT_ROOT = Path(__file__).resolve().parents[1]

PROJECT_ROOT = Path(
    os.environ.get(
        "MONTREAL_TRANSIT_PROJECT_ROOT",
        str(DEFAULT_PROJECT_ROOT),
    )
).resolve()

DB_PATH = PROJECT_ROOT / "data" / "warehouse" / "montreal_transit.duckdb"
DOCS_DIR = PROJECT_ROOT / "docs"
ASSETS_DIR = DOCS_DIR / "assets"
REPORT_PATH = DOCS_DIR / "index.html"

MONTREAL_TIMEZONE = pytz.timezone("America/Montreal")

REQUIRED_TABLES = [
    "dq_run",
    "dq_result",
    "dim_route",
    "dim_stop",
    "dim_trip",
    "fct_scheduled_stop_time",
]

def montreal_now() -> datetime:
    return datetime.now(MONTREAL_TIMEZONE)

def table_exists(connection: duckdb.DuckDBPyConnection, table_name: str) -> bool:
    result = connection.execute(
        """
        SELECT COUNT(*)
        FROM information_schema.tables
        WHERE table_schema = 'main'
          AND table_name = ?
        """,
        [table_name],
    ).fetchone()

    return bool(result[0])


def get_first_row_as_dict(
    connection: duckdb.DuckDBPyConnection,
    table_name: str,
) -> dict[str, object]:
    if not table_exists(connection, table_name):
        return {}

    cursor = connection.execute(f"SELECT * FROM {table_name} LIMIT 1")
    row = cursor.fetchone()

    if row is None:
        return {}

    columns = [column[0] for column in cursor.description]
    return dict(zip(columns, row))


def format_number(value: int | float | None) -> str:
    return f"{int(value or 0):,}"


def format_percent(value: float | None) -> str:
    return f"{float(value or 0):.4%}"


def format_datetime(value: object) -> str:
    if value is None:
        return "Not available"

    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M:%S")

    return str(value)


def format_gtfs_date(value: object) -> str:
    if value is None:
        return "Not available"

    value_as_text = str(value)

    if len(value_as_text) == 8 and value_as_text.isdigit():
        return (
            f"{value_as_text[0:4]}-"
            f"{value_as_text[4:6]}-"
            f"{value_as_text[6:8]}"
        )

    return value_as_text


def create_status_chart(results: list[dict[str, object]]) -> None:
    status_counts = Counter(str(row["status"]) for row in results)

    labels = ["PASS", "FAIL"]
    values = [status_counts.get(label, 0) for label in labels]

    figure, axis = plt.subplots(figsize=(7, 4))
    bars = axis.bar(labels, values)

    axis.set_title("Data Quality Rules by Status")
    axis.set_ylabel("Number of Rules")
    axis.set_ylim(bottom=0)

    axis.bar_label(bars, padding=3)

    figure.tight_layout()
    figure.savefig(ASSETS_DIR / "rules_by_status.png", dpi=160)
    plt.close(figure)


def create_severity_chart(results: list[dict[str, object]]) -> None:
    severity_counts = Counter(str(row["severity"]) for row in results)

    preferred_order = ["CRITICAL", "WARNING", "INFO"]
    labels = [
        severity
        for severity in preferred_order
        if severity_counts.get(severity, 0) > 0
    ]
    values = [severity_counts[label] for label in labels]

    figure, axis = plt.subplots(figsize=(7, 4))
    bars = axis.bar(labels, values)

    axis.set_title("Implemented Rules by Severity")
    axis.set_ylabel("Number of Rules")
    axis.set_ylim(bottom=0)

    axis.bar_label(bars, padding=3)

    figure.tight_layout()
    figure.savefig(ASSETS_DIR / "rules_by_severity.png", dpi=160)
    plt.close(figure)


def get_latest_results(
    connection: duckdb.DuckDBPyConnection,
) -> list[dict[str, object]]:
    cursor = connection.execute(
        """
        WITH latest_run AS (
            SELECT run_id
            FROM dq_run
            ORDER BY executed_at DESC
            LIMIT 1
        )
        SELECT
            result.run_id,
            result.rule_id,
            result.rule_name,
            result.severity,
            result.table_name,
            result.status,
            result.rows_checked,
            result.rows_failed,
            result.failure_rate,
            result.executed_at
        FROM dq_result AS result
        INNER JOIN latest_run AS latest
            ON result.run_id = latest.run_id
        ORDER BY
            CASE result.severity
                WHEN 'CRITICAL' THEN 1
                WHEN 'WARNING' THEN 2
                WHEN 'INFO' THEN 3
                ELSE 4
            END,
            result.rule_id
        """
    )

    columns = [column[0] for column in cursor.description]

    return [
        dict(zip(columns, row))
        for row in cursor.fetchall()
    ]


def get_latest_run(
    connection: duckdb.DuckDBPyConnection,
) -> dict[str, object]:
    cursor = connection.execute(
        """
        SELECT
            run_id,
            executed_at,
            database_path
        FROM dq_run
        ORDER BY executed_at DESC
        LIMIT 1
        """
    )

    row = cursor.fetchone()

    if row is None:
        raise RuntimeError(
            "No data quality run was found. Run run_quality_checks.py first."
        )

    columns = [column[0] for column in cursor.description]
    return dict(zip(columns, row))


def get_dataset_profile(
    connection: duckdb.DuckDBPyConnection,
) -> list[dict[str, object]]:
    datasets = [
        ("Routes", "dim_route"),
        ("Stops", "dim_stop"),
        ("Trips", "dim_trip"),
        ("Scheduled stop times", "fct_scheduled_stop_time"),
        ("Shapes", "raw_shapes"),
    ]

    profile = []

    for dataset_name, table_name in datasets:
        if table_exists(connection, table_name):
            row_count = connection.execute(
                f"SELECT COUNT(*) FROM {table_name}"
            ).fetchone()[0]

            profile.append(
                {
                    "dataset_name": dataset_name,
                    "table_name": table_name,
                    "row_count": row_count,
                }
            )

    return profile


def render_result_rows(results: list[dict[str, object]]) -> str:
    rows = []

    for result in results:
        status_class = (
            "status-pass"
            if result["status"] == "PASS"
            else "status-fail"
        )

        rows.append(
            f"""
            <tr>
                <td>{html.escape(str(result["rule_id"]))}</td>
                <td>{html.escape(str(result["rule_name"]))}</td>
                <td>{html.escape(str(result["severity"]))}</td>
                <td>{html.escape(str(result["table_name"]))}</td>
                <td><span class="{status_class}">{html.escape(str(result["status"]))}</span></td>
                <td class="number">{format_number(result["rows_checked"])}</td>
                <td class="number">{format_number(result["rows_failed"])}</td>
                <td class="number">{format_percent(result["failure_rate"])}</td>
            </tr>
            """
        )

    return "\n".join(rows)


def render_dataset_rows(profile: list[dict[str, object]]) -> str:
    rows = []

    for dataset in profile:
        rows.append(
            f"""
            <tr>
                <td>{html.escape(str(dataset["dataset_name"]))}</td>
                <td><code>{html.escape(str(dataset["table_name"]))}</code></td>
                <td class="number">{format_number(dataset["row_count"])}</td>
            </tr>
            """
        )

    return "\n".join(rows)


def build_report(
    latest_run: dict[str, object],
    results: list[dict[str, object]],
    profile: list[dict[str, object]],
    feed_info: dict[str, object],
) -> str:
    total_rules = len(results)
    passed_rules = sum(result["status"] == "PASS" for result in results)
    failed_rules = sum(result["status"] == "FAIL" for result in results)

    failed_critical_rules = sum(
        result["status"] == "FAIL" and result["severity"] == "CRITICAL"
        for result in results
    )

    total_rows_checked = sum(
        int(result["rows_checked"] or 0)
        for result in results
    )

    total_rows_failed = sum(
        int(result["rows_failed"] or 0)
        for result in results
    )

    overall_failure_rate = (
        total_rows_failed / total_rows_checked
        if total_rows_checked > 0
        else 0
    )

    if failed_critical_rules > 0:
        assessment = "NOT READY"
        assessment_class = "assessment-fail"
    elif failed_rules > 0:
        assessment = "REVIEW REQUIRED"
        assessment_class = "assessment-warning"
    else:
        assessment = "READY FOR REPORTING"
        assessment_class = "assessment-pass"

    feed_version = feed_info.get("feed_version", "Not available")
    feed_start_date = format_gtfs_date(feed_info.get("feed_start_date"))
    feed_end_date = format_gtfs_date(feed_info.get("feed_end_date"))

    generated_at = montreal_now().strftime("%Y-%m-%d %H:%M:%S America/Montreal")

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Montréal Transit Reliability & Data Quality</title>
    <style>
        :root {{
            --page-background: #f5f7fa;
            --card-background: #ffffff;
            --text-primary: #1f2933;
            --text-secondary: #52606d;
            --border: #d9e2ec;
            --pass-background: #d9f7e8;
            --pass-text: #137333;
            --warning-background: #fff4cc;
            --warning-text: #8a5d00;
            --fail-background: #ffe0e0;
            --fail-text: #b42318;
        }}

        * {{
            box-sizing: border-box;
        }}

        body {{
            margin: 0;
            font-family: Arial, Helvetica, sans-serif;
            background: var(--page-background);
            color: var(--text-primary);
            line-height: 1.5;
        }}

        header {{
            background: #18212f;
            color: #ffffff;
            padding: 36px 24px;
        }}

        header h1 {{
            margin: 0;
            font-size: 2rem;
        }}

        header p {{
            margin: 8px 0 0;
            color: #d9e2ec;
        }}

        main {{
            max-width: 1280px;
            margin: 0 auto;
            padding: 24px;
        }}

        section {{
            background: var(--card-background);
            border: 1px solid var(--border);
            border-radius: 8px;
            padding: 24px;
            margin-bottom: 24px;
        }}

        h2 {{
            margin-top: 0;
        }}

        .metrics {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(190px, 1fr));
            gap: 16px;
        }}

        .metric {{
            border: 1px solid var(--border);
            border-radius: 8px;
            padding: 18px;
        }}

        .metric-label {{
            color: var(--text-secondary);
            font-size: 0.9rem;
        }}

        .metric-value {{
            font-size: 1.8rem;
            font-weight: 700;
            margin-top: 6px;
        }}

        .assessment {{
            display: inline-block;
            font-weight: 700;
            border-radius: 999px;
            padding: 8px 14px;
            margin-bottom: 12px;
        }}

        .assessment-pass,
        .status-pass {{
            background: var(--pass-background);
            color: var(--pass-text);
        }}

        .assessment-warning {{
            background: var(--warning-background);
            color: var(--warning-text);
        }}

        .assessment-fail,
        .status-fail {{
            background: var(--fail-background);
            color: var(--fail-text);
        }}

        .status-pass,
        .status-fail {{
            display: inline-block;
            border-radius: 999px;
            padding: 3px 9px;
            font-size: 0.8rem;
            font-weight: 700;
        }}

        .charts {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(300px, 1fr));
            gap: 24px;
        }}

        .chart-card {{
            border: 1px solid var(--border);
            border-radius: 8px;
            padding: 16px;
        }}

        .chart-card img {{
            display: block;
            width: 100%;
            height: auto;
        }}

        .table-wrapper {{
            overflow-x: auto;
        }}

        table {{
            width: 100%;
            border-collapse: collapse;
            font-size: 0.92rem;
        }}

        th,
        td {{
            border-bottom: 1px solid var(--border);
            text-align: left;
            padding: 10px;
            vertical-align: top;
        }}

        th {{
            color: var(--text-secondary);
            font-size: 0.8rem;
            text-transform: uppercase;
            letter-spacing: 0.03em;
        }}

        td.number {{
            text-align: right;
            white-space: nowrap;
        }}

        .metadata {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
            gap: 12px;
        }}

        .metadata-item {{
            border-left: 3px solid var(--border);
            padding-left: 12px;
        }}

        .metadata-label {{
            display: block;
            color: var(--text-secondary);
            font-size: 0.82rem;
        }}

        .note {{
            color: var(--text-secondary);
            font-size: 0.9rem;
            margin-bottom: 0;
        }}

        footer {{
            max-width: 1280px;
            margin: 0 auto;
            padding: 0 24px 32px;
            color: var(--text-secondary);
            font-size: 0.85rem;
        }}

        code {{
            font-family: Consolas, "Courier New", monospace;
        }}
    </style>
</head>
<body>
    <header>
        <h1>Montréal Transit Reliability & Data Quality</h1>
        <p>Static GTFS quality validation report for STM operational data.</p>
    </header>

    <main>
        <section>
            <div class="{assessment_class}">{assessment}</div>
            <h2>Quality overview</h2>

            <div class="metrics">
                <div class="metric">
                    <div class="metric-label">Implemented rules</div>
                    <div class="metric-value">{format_number(total_rules)}</div>
                </div>
                <div class="metric">
                    <div class="metric-label">Passed rules</div>
                    <div class="metric-value">{format_number(passed_rules)}</div>
                </div>
                <div class="metric">
                    <div class="metric-label">Failed rules</div>
                    <div class="metric-value">{format_number(failed_rules)}</div>
                </div>
                <div class="metric">
                    <div class="metric-label">Failed critical rules</div>
                    <div class="metric-value">{format_number(failed_critical_rules)}</div>
                </div>
                <div class="metric">
                    <div class="metric-label">Rows checked</div>
                    <div class="metric-value">{format_number(total_rows_checked)}</div>
                </div>
                <div class="metric">
                    <div class="metric-label">Overall failure rate</div>
                    <div class="metric-value">{format_percent(overall_failure_rate)}</div>
                </div>
            </div>
        </section>

        <section>
            <h2>Latest validation run</h2>

            <div class="metadata">
                <div class="metadata-item">
                    <span class="metadata-label">Run identifier</span>
                    <code>{html.escape(str(latest_run["run_id"]))}</code>
                </div>
                <div class="metadata-item">
                    <span class="metadata-label">Executed at (America/Montreal)</span>
                    {html.escape(format_datetime(latest_run["executed_at"]))}
                </div>
                <div class="metadata-item">
                    <span class="metadata-label">GTFS feed version</span>
                    {html.escape(str(feed_version))}
                </div>
                <div class="metadata-item">
                    <span class="metadata-label">GTFS service period</span>
                    {html.escape(feed_start_date)} to {html.escape(feed_end_date)}
                </div>
            </div>
        </section>

        <section>
            <h2>Quality rule distribution</h2>

            <div class="charts">
                <div class="chart-card">
                    <img
                        src="assets/rules_by_status.png"
                        alt="Bar chart showing data quality rules by status"
                    >
                </div>
                <div class="chart-card">
                    <img
                        src="assets/rules_by_severity.png"
                        alt="Bar chart showing implemented rules by severity"
                    >
                </div>
            </div>
        </section>

        <section>
            <h2>Latest data quality results</h2>

            <div class="table-wrapper">
                <table>
                    <thead>
                        <tr>
                            <th>Rule</th>
                            <th>Control</th>
                            <th>Severity</th>
                            <th>Table</th>
                            <th>Status</th>
                            <th>Rows checked</th>
                            <th>Rows failed</th>
                            <th>Failure rate</th>
                        </tr>
                    </thead>
                    <tbody>
                        {render_result_rows(results)}
                    </tbody>
                </table>
            </div>
        </section>

        <section>
            <h2>Dataset profile</h2>

            <div class="table-wrapper">
                <table>
                    <thead>
                        <tr>
                            <th>Dataset</th>
                            <th>Source table</th>
                            <th>Rows</th>
                        </tr>
                    </thead>
                    <tbody>
                        {render_dataset_rows(profile)}
                    </tbody>
                </table>
            </div>
        </section>

        <section>
            <h2>Interpretation</h2>

            <p>
                This report evaluates the latest available static GTFS snapshot
                against the implemented quality controls. A PASS result means
                that no exception was detected for that rule during this run.
            </p>

            <p class="note">
                This report does not certify real-time feed availability,
                on-time performance, service reliability, or completeness
                beyond the controls currently implemented.
            </p>
        </section>
    </main>

    <footer>
        Generated on {html.escape(generated_at)} from the local DuckDB warehouse.
    </footer>
</body>
</html>
"""


def main() -> None:
    if not DB_PATH.exists():
        raise FileNotFoundError(
            f"Database not found: {DB_PATH}\n"
            "Run ingest_gtfs.py and run_quality_checks.py first."
        )

    DOCS_DIR.mkdir(parents=True, exist_ok=True)
    ASSETS_DIR.mkdir(parents=True, exist_ok=True)

    connection = duckdb.connect(str(DB_PATH), read_only=True)

    try:
        missing_tables = [
            table_name
            for table_name in REQUIRED_TABLES
            if not table_exists(connection, table_name)
        ]

        if missing_tables:
            missing_text = ", ".join(missing_tables)

            raise RuntimeError(
                "Required tables are missing: "
                f"{missing_text}\n"
                "Run ingest_gtfs.py and run_quality_checks.py first."
            )

        latest_run = get_latest_run(connection)
        results = get_latest_results(connection)
        profile = get_dataset_profile(connection)
        feed_info = get_first_row_as_dict(connection, "meta_gtfs_feed")

        create_status_chart(results)
        create_severity_chart(results)

        report_html = build_report(
            latest_run=latest_run,
            results=results,
            profile=profile,
            feed_info=feed_info,
        )

        REPORT_PATH.write_text(report_html, encoding="utf-8")

        print(f"Report created: {REPORT_PATH}")
        print(f"Chart created:  {ASSETS_DIR / 'rules_by_status.png'}")
        print(f"Chart created:  {ASSETS_DIR / 'rules_by_severity.png'}")

    finally:
        connection.close()


if __name__ == "__main__":
    main()