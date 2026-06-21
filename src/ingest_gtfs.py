from __future__ import annotations

import uuid
import os
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

GTFS_DIR = PROJECT_ROOT / "data" / "raw" / "gtfs" / "current"
DB_PATH = PROJECT_ROOT / "data" / "warehouse" / "montreal_transit.duckdb"

GTFS_FILES = [
    "agency.txt",
    "calendar.txt",
    "calendar_dates.txt",
    "directions.txt",
    "feed_info.txt",
    "route_patterns.txt",
    "routes.txt",
    "shapes.txt",
    "stop_times.txt",
    "stops.txt",
    "translations.txt",
    "trips.txt",
]


def sql_path(path: Path) -> str:
    """Return a DuckDB-safe absolute file path."""
    return path.resolve().as_posix().replace("'", "''")


def gtfs_time_to_seconds(column_name: str) -> str:
    """Convert GTFS HH:MM:SS to seconds, including times past midnight."""
    return f"""
        CASE
            WHEN regexp_matches({column_name}, '^[0-9]+:[0-9]{{2}}:[0-9]{{2}}$')
            THEN
                try_cast(split_part({column_name}, ':', 1) AS INTEGER) * 3600
                + try_cast(split_part({column_name}, ':', 2) AS INTEGER) * 60
                + try_cast(split_part({column_name}, ':', 3) AS INTEGER)
            ELSE NULL
        END
    """


def load_raw_files(connection: duckdb.DuckDBPyConnection, run_id: str) -> list[str]:
    """Load every GTFS text file into a raw DuckDB table."""
    loaded_tables = []

    connection.execute("""
        CREATE TABLE IF NOT EXISTS etl_load_log (
            run_id VARCHAR,
            source_file VARCHAR,
            target_table VARCHAR,
            row_count BIGINT,
            source_last_modified TIMESTAMP,
            loaded_at TIMESTAMP
        )
    """)

    for filename in GTFS_FILES:
        file_path = GTFS_DIR / filename

        if not file_path.exists():
            print(f"WARNING - File not found, skipped: {filename}")
            continue

        table_name = f"raw_{file_path.stem}"
        source_path = sql_path(file_path)

        connection.execute(f"""
            CREATE OR REPLACE TABLE {table_name} AS
            SELECT *
            FROM read_csv_auto(
                '{source_path}',
                header = true,
                all_varchar = true,
                nullstr = ''
            )
        """)

        row_count = connection.execute(
            f"SELECT COUNT(*) FROM {table_name}"
        ).fetchone()[0]

        source_last_modified = datetime.fromtimestamp(file_path.stat().st_mtime)

        connection.execute("""
            INSERT INTO etl_load_log
            VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        """, [
            run_id,
            filename,
            table_name,
            row_count,
            source_last_modified,
        ])

        loaded_tables.append(table_name)
        print(f"Loaded {filename:<22} -> {table_name:<24} ({row_count:,} rows)")

    return loaded_tables


def build_analytics_tables(connection: duckdb.DuckDBPyConnection, run_id: str) -> None:
    """Create the first analytical tables from raw GTFS data."""

    connection.execute("""
        CREATE OR REPLACE TABLE dim_route AS
        SELECT
            trim(route_id) AS route_id,
            nullif(trim(route_short_name), '') AS route_short_name,
            nullif(trim(route_long_name), '') AS route_long_name,
            try_cast(route_type AS INTEGER) AS route_type
        FROM raw_routes
    """)

    connection.execute("""
        CREATE OR REPLACE TABLE dim_stop AS
        SELECT
            trim(stop_id) AS stop_id,
            nullif(trim(stop_name), '') AS stop_name,
            try_cast(stop_lat AS DOUBLE) AS stop_lat,
            try_cast(stop_lon AS DOUBLE) AS stop_lon
        FROM raw_stops
    """)

    connection.execute("""
        CREATE OR REPLACE TABLE dim_service AS
        SELECT
            trim(service_id) AS service_id,
            try_cast(monday AS INTEGER) AS monday,
            try_cast(tuesday AS INTEGER) AS tuesday,
            try_cast(wednesday AS INTEGER) AS wednesday,
            try_cast(thursday AS INTEGER) AS thursday,
            try_cast(friday AS INTEGER) AS friday,
            try_cast(saturday AS INTEGER) AS saturday,
            try_cast(sunday AS INTEGER) AS sunday,
            try_strptime(start_date, '%Y%m%d')::DATE AS start_date,
            try_strptime(end_date, '%Y%m%d')::DATE AS end_date
        FROM raw_calendar
    """)

    connection.execute("""
        CREATE OR REPLACE TABLE dim_trip AS
        SELECT
            trim(trip_id) AS trip_id,
            trim(route_id) AS route_id,
            trim(service_id) AS service_id,
            nullif(trim(trip_headsign), '') AS trip_headsign,
            try_cast(direction_id AS INTEGER) AS direction_id,
            nullif(trim(shape_id), '') AS shape_id
        FROM raw_trips
    """)

    connection.execute(f"""
        CREATE OR REPLACE TABLE fct_scheduled_stop_time AS
        SELECT
            trim(trip_id) AS trip_id,
            trim(stop_id) AS stop_id,
            try_cast(stop_sequence AS INTEGER) AS stop_sequence,
            nullif(trim(arrival_time), '') AS arrival_time,
            nullif(trim(departure_time), '') AS departure_time,
            {gtfs_time_to_seconds("arrival_time")} AS arrival_seconds,
            {gtfs_time_to_seconds("departure_time")} AS departure_seconds
        FROM raw_stop_times
    """)

    connection.execute(f"""
        CREATE OR REPLACE TABLE meta_gtfs_feed AS
        SELECT
            *,
            '{run_id}' AS ingestion_run_id,
            CURRENT_TIMESTAMP AS ingested_at
        FROM raw_feed_info
    """)


def print_summary(connection: duckdb.DuckDBPyConnection) -> None:
    tables = [
        "raw_routes",
        "raw_stops",
        "raw_trips",
        "raw_stop_times",
        "dim_route",
        "dim_stop",
        "dim_service",
        "dim_trip",
        "fct_scheduled_stop_time",
        "meta_gtfs_feed",
    ]

    print("\nDatabase summary")
    print("-" * 55)

    for table_name in tables:
        row_count = connection.execute(
            f"SELECT COUNT(*) FROM {table_name}"
        ).fetchone()[0]
        print(f"{table_name:<30} {row_count:>12,} rows")


def main() -> None:
    if not GTFS_DIR.exists():
        raise FileNotFoundError(
            f"GTFS folder not found: {GTFS_DIR}\n"
            "Extract gtfs_stm.zip into data/raw/gtfs/current first."
        )

    DB_PATH.parent.mkdir(parents=True, exist_ok=True)

    run_id = str(uuid.uuid4())
    print(f"Run ID: {run_id}")
    print(f"Database: {DB_PATH}\n")

    connection = duckdb.connect(str(DB_PATH))

    try:
        load_raw_files(connection, run_id)
        build_analytics_tables(connection, run_id)
        print_summary(connection)
    finally:
        connection.close()


if __name__ == "__main__":
    main()