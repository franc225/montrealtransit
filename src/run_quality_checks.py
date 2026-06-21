from __future__ import annotations

from ast import arguments
import uuid
import pytz
import os
import argparse
from datetime import datetime
from pathlib import Path

import duckdb


DEFAULT_PROJECT_ROOT = Path(__file__).resolve().parents[1]

PROJECT_ROOT = Path(
    os.environ.get(
        "MONTREAL_TRANSIT_PROJECT_ROOT",
        str(DEFAULT_PROJECT_ROOT),
    )
).resolve()

DB_PATH = PROJECT_ROOT / "data" / "warehouse" / "montreal_transit.duckdb"

MONTREAL_TIMEZONE = pytz.timezone("America/Montreal")

RULES = [
    {
        "rule_id": "DQ001",
        "rule_name": "Required fields are populated in scheduled stop times",
        "severity": "CRITICAL",
        "table_name": "fct_scheduled_stop_time",
        "description": (
            "trip_id, stop_id and stop_sequence must be present "
            "for every scheduled stop time."
        ),
        "rows_checked_sql": """
            SELECT COUNT(*)
            FROM fct_scheduled_stop_time
        """,
        "rows_failed_sql": """
            SELECT COUNT(*)
            FROM fct_scheduled_stop_time
            WHERE NULLIF(trim(trip_id), '') IS NULL
               OR NULLIF(trim(stop_id), '') IS NULL
               OR stop_sequence IS NULL
        """,
    },
    {
        "rule_id": "DQ002",
        "rule_name": "Trip stop sequences are unique",
        "severity": "CRITICAL",
        "table_name": "fct_scheduled_stop_time",
        "description": (
            "A trip cannot contain more than one record "
            "for the same stop_sequence."
        ),
        "rows_checked_sql": """
            SELECT COUNT(*)
            FROM fct_scheduled_stop_time
        """,
        "rows_failed_sql": """
            SELECT COALESCE(SUM(duplicate_count - 1), 0)
            FROM (
                SELECT
                    trip_id,
                    stop_sequence,
                    COUNT(*) AS duplicate_count
                FROM fct_scheduled_stop_time
                WHERE NULLIF(trim(trip_id), '') IS NOT NULL
                  AND stop_sequence IS NOT NULL
                GROUP BY trip_id, stop_sequence
                HAVING COUNT(*) > 1
            )
        """,
    },
    {
        "rule_id": "DQ003",
        "rule_name": "Scheduled stop times reference valid trips",
        "severity": "CRITICAL",
        "table_name": "fct_scheduled_stop_time",
        "description": (
            "Every trip_id in scheduled stop times must exist "
            "in dim_trip."
        ),
        "rows_checked_sql": """
            SELECT COUNT(*)
            FROM fct_scheduled_stop_time
        """,
        "rows_failed_sql": """
            SELECT COUNT(*)
            FROM fct_scheduled_stop_time AS s
            LEFT JOIN dim_trip AS t
                ON s.trip_id = t.trip_id
            WHERE NULLIF(trim(s.trip_id), '') IS NOT NULL
              AND t.trip_id IS NULL
        """,
    },
    {
        "rule_id": "DQ004",
        "rule_name": "Scheduled stop times reference valid stops",
        "severity": "CRITICAL",
        "table_name": "fct_scheduled_stop_time",
        "description": (
            "Every stop_id in scheduled stop times must exist "
            "in dim_stop."
        ),
        "rows_checked_sql": """
            SELECT COUNT(*)
            FROM fct_scheduled_stop_time
        """,
        "rows_failed_sql": """
            SELECT COUNT(*)
            FROM fct_scheduled_stop_time AS s
            LEFT JOIN dim_stop AS st
                ON s.stop_id = st.stop_id
            WHERE NULLIF(trim(s.stop_id), '') IS NOT NULL
              AND st.stop_id IS NULL
        """,
    },
        {
        "rule_id": "DQ005",
        "rule_name": "Scheduled times use a valid GTFS format",
        "severity": "CRITICAL",
        "table_name": "fct_scheduled_stop_time",
        "description": (
            "Arrival and departure times must use GTFS HH:MM:SS format. "
            "Hours above 23 are valid for trips crossing midnight."
        ),
        "rows_checked_sql": """
            SELECT COUNT(*)
            FROM fct_scheduled_stop_time
        """,
        "rows_failed_sql": """
            SELECT COUNT(*)
            FROM fct_scheduled_stop_time
            WHERE (arrival_time IS NOT NULL AND arrival_seconds IS NULL)
               OR (departure_time IS NOT NULL AND departure_seconds IS NULL)
        """,
    },
    {
        "rule_id": "DQ006",
        "rule_name": "Departure is not earlier than arrival",
        "severity": "WARNING",
        "table_name": "fct_scheduled_stop_time",
        "description": (
            "When both values are present, departure time must be equal to "
            "or later than arrival time at the same stop."
        ),
        "rows_checked_sql": """
            SELECT COUNT(*)
            FROM fct_scheduled_stop_time
            WHERE arrival_seconds IS NOT NULL
              AND departure_seconds IS NOT NULL
        """,
        "rows_failed_sql": """
            SELECT COUNT(*)
            FROM fct_scheduled_stop_time
            WHERE arrival_seconds IS NOT NULL
              AND departure_seconds IS NOT NULL
              AND departure_seconds < arrival_seconds
        """,
    },
    {
        "rule_id": "DQ007",
        "rule_name": "Trips have at least one scheduled stop",
        "severity": "CRITICAL",
        "table_name": "dim_trip",
        "description": (
            "Each planned trip must have at least one associated scheduled stop."
        ),
        "rows_checked_sql": """
            SELECT COUNT(*)
            FROM dim_trip
        """,
        "rows_failed_sql": """
            SELECT COUNT(*)
            FROM dim_trip AS t
            LEFT JOIN fct_scheduled_stop_time AS s
                ON t.trip_id = s.trip_id
            WHERE s.trip_id IS NULL
        """,
    },
    {
        "rule_id": "DQ008",
        "rule_name": "Routes have at least one planned trip",
        "severity": "WARNING",
        "table_name": "dim_route",
        "description": (
            "Each route should have at least one associated trip in the current feed."
        ),
        "rows_checked_sql": """
            SELECT COUNT(*)
            FROM dim_route
        """,
        "rows_failed_sql": """
            SELECT COUNT(*)
            FROM dim_route AS r
            LEFT JOIN dim_trip AS t
                ON r.route_id = t.route_id
            WHERE t.route_id IS NULL
        """,
    },
    {
        "rule_id": "DQ009",
        "rule_name": "Stop coordinates are plausible for the STM service area",
        "severity": "WARNING",
        "table_name": "dim_stop",
        "description": (
            "Stop coordinates must be present and fall within a broad "
            "Montréal-area geographic envelope."
        ),
        "rows_checked_sql": """
            SELECT COUNT(*)
            FROM dim_stop
        """,
        "rows_failed_sql": """
            SELECT COUNT(*)
            FROM dim_stop
            WHERE stop_lat IS NULL
               OR stop_lon IS NULL
               OR stop_lat NOT BETWEEN 45.0 AND 46.0
               OR stop_lon NOT BETWEEN -74.5 AND -73.0
        """,
    },
    {
        "rule_id": "DQ010",
        "rule_name": "Stop sequence is positive",
        "severity": "CRITICAL",
        "table_name": "fct_scheduled_stop_time",
        "description": (
            "Each scheduled stop must have a positive stop sequence."
        ),
        "rows_checked_sql": """
            SELECT COUNT(*)
            FROM fct_scheduled_stop_time
        """,
        "rows_failed_sql": """
            SELECT COUNT(*)
            FROM fct_scheduled_stop_time
            WHERE stop_sequence IS NULL
               OR stop_sequence <= 0
        """,
    },
]

def montreal_now() -> datetime:
    return datetime.now(MONTREAL_TIMEZONE).replace(tzinfo=None)

def create_quality_tables(connection: duckdb.DuckDBPyConnection) -> None:
    connection.execute("""
        CREATE TABLE IF NOT EXISTS dq_rule (
            rule_id VARCHAR PRIMARY KEY,
            rule_name VARCHAR,
            severity VARCHAR,
            table_name VARCHAR,
            description VARCHAR
        )
    """)

    connection.execute("""
        CREATE TABLE IF NOT EXISTS dq_run (
            run_id VARCHAR PRIMARY KEY,
            executed_at TIMESTAMP,
            database_path VARCHAR
        )
    """)

    connection.execute("""
        CREATE TABLE IF NOT EXISTS dq_result (
            run_id VARCHAR,
            rule_id VARCHAR,
            rule_name VARCHAR,
            severity VARCHAR,
            table_name VARCHAR,
            status VARCHAR,
            rows_checked BIGINT,
            rows_failed BIGINT,
            failure_rate DOUBLE,
            executed_at TIMESTAMP
        )
    """)


def register_rules(connection: duckdb.DuckDBPyConnection) -> None:
    for rule in RULES:
        connection.execute("""
            INSERT OR REPLACE INTO dq_rule
            VALUES (?, ?, ?, ?, ?)
        """, [
            rule["rule_id"],
            rule["rule_name"],
            rule["severity"],
            rule["table_name"],
            rule["description"],
        ])


def run_checks(connection: duckdb.DuckDBPyConnection, run_id: str) -> None:
    executed_at = montreal_now()

    connection.execute("""
        INSERT INTO dq_run
        VALUES (?, ?, ?)
    """, [run_id, executed_at, str(DB_PATH)])

    print("\nData quality results")
    print("-" * 105)
    print(
        f"{'Rule':<8} {'Status':<8} {'Severity':<10} "
        f"{'Checked':>15} {'Failed':>15} {'Failure rate':>15}"
    )
    print("-" * 105)

    for rule in RULES:
        rows_checked = connection.execute(
            rule["rows_checked_sql"]
        ).fetchone()[0]

        rows_failed = connection.execute(
            rule["rows_failed_sql"]
        ).fetchone()[0]

        rows_checked = int(rows_checked or 0)
        rows_failed = int(rows_failed or 0)

        failure_rate = (
            rows_failed / rows_checked
            if rows_checked > 0
            else 0
        )

        status = "PASS" if rows_failed == 0 else "FAIL"

        connection.execute("""
            INSERT INTO dq_result
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, [
            run_id,
            rule["rule_id"],
            rule["rule_name"],
            rule["severity"],
            rule["table_name"],
            status,
            rows_checked,
            rows_failed,
            failure_rate,
            executed_at,
        ])

        print(
            f"{rule['rule_id']:<8} {status:<8} {rule['severity']:<10} "
            f"{rows_checked:>15,} {rows_failed:>15,} "
            f"{failure_rate:>14.4%}"
        )

def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run GTFS data quality checks."
    )

    parser.add_argument(
        "--run-id",
        default=None,
        help="Optional quality run identifier.",
    )

    return parser.parse_args()

def main() -> None:
    if not DB_PATH.exists():
        raise FileNotFoundError(
            f"Database not found: {DB_PATH}\n"
            "Run ingest_gtfs.py first."
        )

    arguments = parse_arguments()
    run_id = arguments.run_id or str(uuid.uuid4())
    print(f"Quality run ID: {run_id}")

    connection = duckdb.connect(str(DB_PATH))

    try:
        create_quality_tables(connection)
        register_rules(connection)
        run_checks(connection, run_id)
    finally:
        connection.close()


if __name__ == "__main__":
    main()