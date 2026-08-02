from __future__ import annotations

import argparse
import sys
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

import duckdb

from gtfs_realtime_config import load_gtfs_realtime_config
from gtfs_realtime_quality import (
    PreviousCapture,
    QualityAssessment,
    QualityError,
    assess_quality,
    load_quality_config,
)
from parse_gtfs_realtime import ParserError, decode_feed, validate_capture


DEFAULT_WAREHOUSE_RELATIVE_PATH = Path("data/warehouse/montreal_transit.duckdb")
REALTIME_SCHEMA_VERSION = 1


class IngestionError(RuntimeError):
    """A concise, secret-safe GTFS-Realtime ingestion failure."""


@dataclass(frozen=True)
class IngestionResult:
    capture_uuid: str
    quality_run_id: str
    inserted: bool
    overall_status: str
    entity_count: int
    warehouse_relative_path: str | None


def create_realtime_tables(connection: duckdb.DuckDBPyConnection) -> None:
    statements = (
        """CREATE TABLE IF NOT EXISTS gtfs_realtime_capture (
            capture_uuid VARCHAR PRIMARY KEY, provider VARCHAR NOT NULL, feed_type VARCHAR NOT NULL,
            captured_at_utc TIMESTAMPTZ NOT NULL, filename_timestamp_utc VARCHAR NOT NULL,
            feed_timestamp_unix UBIGINT, feed_timestamp_utc TIMESTAMPTZ,
            gtfs_realtime_version VARCHAR NOT NULL, incrementality INTEGER,
            payload_relative_path VARCHAR NOT NULL, metadata_relative_path VARCHAR NOT NULL,
            payload_size_bytes UBIGINT NOT NULL, payload_sha256 VARCHAR NOT NULL,
            total_entity_count INTEGER NOT NULL, deleted_entity_count INTEGER NOT NULL,
            supported_entity_count INTEGER NOT NULL, unsupported_entity_count INTEGER NOT NULL,
            ingested_at_utc TIMESTAMPTZ NOT NULL, parser_schema_version INTEGER NOT NULL)""",
        """CREATE TABLE IF NOT EXISTS gtfs_realtime_entity (
            capture_uuid VARCHAR NOT NULL, entity_index INTEGER NOT NULL, entity_id VARCHAR NOT NULL,
            entity_type VARCHAR NOT NULL, is_deleted BOOLEAN NOT NULL,
            expected_for_feed_type BOOLEAN NOT NULL, supported BOOLEAN NOT NULL,
            trip_id VARCHAR, route_id VARCHAR, direction_id INTEGER, vehicle_id VARCHAR,
            PRIMARY KEY (capture_uuid, entity_index))""",
        """CREATE TABLE IF NOT EXISTS gtfs_realtime_vehicle_position (
            capture_uuid VARCHAR NOT NULL, entity_index INTEGER NOT NULL,
            trip_id VARCHAR, route_id VARCHAR, direction_id INTEGER, start_date VARCHAR, start_time VARCHAR,
            vehicle_id VARCHAR, latitude DOUBLE, longitude DOUBLE, bearing DOUBLE, speed DOUBLE,
            current_stop_sequence INTEGER, stop_id VARCHAR, current_status INTEGER,
            timestamp_unix UBIGINT, timestamp_utc TIMESTAMPTZ,
            occupancy_status INTEGER, occupancy_percentage INTEGER,
            PRIMARY KEY (capture_uuid, entity_index))""",
        """CREATE TABLE IF NOT EXISTS gtfs_realtime_trip_update (
            capture_uuid VARCHAR NOT NULL, entity_index INTEGER NOT NULL,
            trip_id VARCHAR, route_id VARCHAR, direction_id INTEGER, start_date VARCHAR, start_time VARCHAR,
            schedule_relationship INTEGER, vehicle_id VARCHAR, timestamp_unix UBIGINT,
            timestamp_utc TIMESTAMPTZ, trip_delay_seconds INTEGER, stop_time_update_count INTEGER NOT NULL,
            PRIMARY KEY (capture_uuid, entity_index))""",
        """CREATE TABLE IF NOT EXISTS gtfs_realtime_stop_time_update (
            capture_uuid VARCHAR NOT NULL, entity_index INTEGER NOT NULL, stop_time_update_index INTEGER NOT NULL,
            stop_sequence INTEGER, stop_id VARCHAR, schedule_relationship INTEGER,
            arrival_delay_seconds INTEGER, arrival_time_unix BIGINT, arrival_time_utc TIMESTAMPTZ,
            departure_delay_seconds INTEGER, departure_time_unix BIGINT, departure_time_utc TIMESTAMPTZ,
            PRIMARY KEY (capture_uuid, entity_index, stop_time_update_index))""",
        """CREATE TABLE IF NOT EXISTS gtfs_realtime_quality_run (
            quality_run_id VARCHAR PRIMARY KEY, capture_uuid VARCHAR NOT NULL,
            analyzed_at_utc TIMESTAMPTZ NOT NULL, quality_config_schema_version INTEGER NOT NULL,
            overall_status VARCHAR NOT NULL, result_count INTEGER NOT NULL,
            finding_count INTEGER NOT NULL)""",
        """CREATE TABLE IF NOT EXISTS gtfs_realtime_quality_result (
            quality_run_id VARCHAR NOT NULL, result_index INTEGER NOT NULL, rule_id VARCHAR NOT NULL,
            rule_name VARCHAR NOT NULL, category VARCHAR NOT NULL, status VARCHAR NOT NULL,
            metric_name VARCHAR NOT NULL, numeric_value DOUBLE, numerator BIGINT, denominator BIGINT,
            ratio DOUBLE, threshold DOUBLE, threshold_operator VARCHAR, unit VARCHAR,
            details VARCHAR NOT NULL, enabled BOOLEAN NOT NULL, informational BOOLEAN NOT NULL,
            PRIMARY KEY (quality_run_id, result_index))""",
    )
    for statement in statements:
        connection.execute(statement)


def _previous_capture(connection: duckdb.DuckDBPyConnection, provider: str, feed_type: str) -> PreviousCapture | None:
    row = connection.execute(
        """SELECT capture_uuid, captured_at_utc, feed_timestamp_unix, payload_sha256
           FROM gtfs_realtime_capture WHERE provider = ? AND feed_type = ?
           ORDER BY captured_at_utc DESC, capture_uuid DESC LIMIT 1""",
        [provider, feed_type],
    ).fetchone()
    return PreviousCapture(str(row[0]), row[1], row[2], str(row[3])) if row else None


def _common_identifiers(entity: object) -> tuple[object, object, object, object]:
    payload = entity.vehicle_position or entity.trip_update
    trip = payload.trip if payload is not None else None
    vehicle = payload.vehicle if payload is not None else None
    return (
        trip.trip_id if trip else None, trip.route_id if trip else None,
        trip.direction_id if trip else None, vehicle.vehicle_id if vehicle else None,
    )


def persist_capture(
    connection: duckdb.DuckDBPyConnection,
    capture: object,
    feed: object,
    assessment: QualityAssessment,
    quality_schema_version: int,
    failure_hook: Callable[[str], None] | None = None,
) -> IngestionResult:
    capture_uuid = capture.capture_uuid
    existing = connection.execute(
        "SELECT payload_sha256 FROM gtfs_realtime_capture WHERE capture_uuid = ?", [capture_uuid]
    ).fetchone()
    if existing:
        if existing[0] != capture.payload_sha256:
            raise IngestionError("Capture UUID already exists with a different payload SHA-256.")
        run = connection.execute(
            "SELECT quality_run_id, overall_status FROM gtfs_realtime_quality_run WHERE capture_uuid = ? ORDER BY analyzed_at_utc LIMIT 1",
            [capture_uuid],
        ).fetchone()
        return IngestionResult(capture_uuid, str(run[0]), False, str(run[1]), len(feed.entities), None)
    quality_run_id = str(uuid.uuid4())
    analyzed_at = datetime.now(timezone.utc)
    expected_type = "vehicle_position" if feed.feed_type == "vehicle_positions" else "trip_update"
    try:
        connection.execute("BEGIN TRANSACTION")
        connection.execute(
            """INSERT INTO gtfs_realtime_capture VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            [capture_uuid, feed.provider, feed.feed_type, feed.captured_at_utc,
             feed.captured_at_utc.strftime("%Y%m%dT%H%M%SZ"), feed.header.timestamp,
             feed.header.timestamp_utc, feed.header.gtfs_realtime_version, feed.header.incrementality,
             capture.payload_relative_path, capture.metadata_relative_path,
             capture.payload_size_bytes, capture.payload_sha256, len(feed.entities),
             feed.summary.deleted_entity_count,
             len(feed.entities) - feed.summary.unsupported_entity_count,
             feed.summary.unsupported_entity_count, analyzed_at, REALTIME_SCHEMA_VERSION],
        )
        for entity in feed.entities:
            trip_id, route_id, direction_id, vehicle_id = _common_identifiers(entity)
            connection.execute(
                "INSERT INTO gtfs_realtime_entity VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [capture_uuid, entity.original_index, entity.entity_id, entity.entity_type,
                 entity.is_deleted, entity.entity_type == expected_type,
                 entity.entity_type != "unsupported", trip_id, route_id, direction_id, vehicle_id],
            )
            if entity.vehicle_position is not None:
                vehicle = entity.vehicle_position
                trip, descriptor, position = vehicle.trip, vehicle.vehicle, vehicle.position
                connection.execute(
                    "INSERT INTO gtfs_realtime_vehicle_position VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    [capture_uuid, entity.original_index, trip.trip_id if trip else None,
                     trip.route_id if trip else None, trip.direction_id if trip else None,
                     trip.start_date if trip else None, trip.start_time if trip else None,
                     descriptor.vehicle_id if descriptor else None,
                     position.latitude if position else None, position.longitude if position else None,
                     position.bearing if position else None, position.speed if position else None,
                     vehicle.current_stop_sequence, vehicle.stop_id, vehicle.current_status,
                     vehicle.timestamp, vehicle.timestamp_utc, vehicle.occupancy_status,
                     vehicle.occupancy_percentage],
                )
            if entity.trip_update is not None:
                update = entity.trip_update
                trip, descriptor = update.trip, update.vehicle
                connection.execute(
                    "INSERT INTO gtfs_realtime_trip_update VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    [capture_uuid, entity.original_index, trip.trip_id if trip else None,
                     trip.route_id if trip else None, trip.direction_id if trip else None,
                     trip.start_date if trip else None, trip.start_time if trip else None,
                     trip.schedule_relationship if trip else None,
                     descriptor.vehicle_id if descriptor else None, update.timestamp,
                     update.timestamp_utc, update.delay, len(update.stop_time_updates)],
                )
                for index, stop in enumerate(update.stop_time_updates):
                    connection.execute(
                        "INSERT INTO gtfs_realtime_stop_time_update VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        [capture_uuid, entity.original_index, index, stop.stop_sequence, stop.stop_id,
                         stop.schedule_relationship, stop.arrival.delay if stop.arrival else None,
                         stop.arrival.time if stop.arrival else None, stop.arrival.time_utc if stop.arrival else None,
                         stop.departure.delay if stop.departure else None,
                         stop.departure.time if stop.departure else None, stop.departure.time_utc if stop.departure else None],
                    )
        if failure_hook:
            failure_hook("after_entities")
        connection.execute(
            "INSERT INTO gtfs_realtime_quality_run VALUES (?, ?, ?, ?, ?, ?, ?)",
            [quality_run_id, capture_uuid, analyzed_at, quality_schema_version,
             assessment.overall_status, len(assessment.results), len(feed.findings)],
        )
        for index, result in enumerate(assessment.results):
            connection.execute(
                "INSERT INTO gtfs_realtime_quality_result VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [quality_run_id, index, result.rule_id, result.rule_name, result.category,
                 result.status, result.metric_name, result.value, result.numerator,
                 result.denominator, result.ratio, result.threshold, result.threshold_operator,
                 result.unit, result.details, result.enabled, result.informational],
            )
        if failure_hook:
            failure_hook("after_quality_results")
        connection.execute("COMMIT")
    except Exception:
        connection.execute("ROLLBACK")
        raise
    return IngestionResult(capture_uuid, quality_run_id, True, assessment.overall_status, len(feed.entities), None)


def ingest_capture(payload: Path, metadata: Path | None = None, warehouse: Path | None = None,
                   persist: bool = True) -> tuple[IngestionResult, QualityAssessment, object]:
    config = load_gtfs_realtime_config(validate_credentials=False)
    quality_config = load_quality_config(config.project_root)
    capture = validate_capture(config, payload, metadata)
    feed = decode_feed(capture.payload_path.read_bytes(), capture)
    warehouse_path = (warehouse or config.project_root / DEFAULT_WAREHOUSE_RELATIVE_PATH).resolve()
    previous = None
    connection = None
    if persist:
        warehouse_path.parent.mkdir(parents=True, exist_ok=True)
        connection = duckdb.connect(str(warehouse_path))
        create_realtime_tables(connection)
        previous = _previous_capture(connection, feed.provider, feed.feed_type)
    assessment = assess_quality(feed, quality_config, previous)
    if not persist:
        result = IngestionResult(capture.capture_uuid, "", False,
                                 assessment.overall_status, len(feed.entities), None)
        return result, assessment, feed
    try:
        result = persist_capture(connection, capture, feed, assessment, quality_config.schema_version)
    finally:
        connection.close()
    relative = warehouse_path.relative_to(config.project_root).as_posix() if warehouse_path.is_relative_to(config.project_root) else None
    return IngestionResult(result.capture_uuid, result.quality_run_id, result.inserted,
                           result.overall_status, result.entity_count, relative), assessment, feed


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Analyze and persist one captured GTFS-Realtime feed.")
    parser.add_argument("--payload", type=Path, required=True)
    parser.add_argument("--metadata", type=Path)
    parser.add_argument("--warehouse", type=Path)
    parser.add_argument("--summary", action="store_true", help="Print concise summary (default).")
    parser.add_argument("--no-persist", action="store_true", help="Calculate quality without DuckDB writes.")
    return parser


def run_cli(argv: list[str] | None = None) -> int:
    args = build_argument_parser().parse_args(argv)
    try:
        result, assessment, feed = ingest_capture(args.payload, args.metadata, args.warehouse, not args.no_persist)
    except (ParserError, QualityError, IngestionError, OSError, duckdb.Error) as error:
        print(f"GTFS-Realtime ingestion failed: {error}", file=sys.stderr)
        return 1
    counts = assessment.status_counts
    print(f"Capture UUID: {result.capture_uuid}")
    print(f"Feed type: {feed.feed_type}")
    print(f"Captured at UTC: {feed.captured_at_utc.isoformat()}")
    print(f"Feed timestamp UTC: {feed.header.timestamp_utc.isoformat() if feed.header.timestamp_utc else 'missing'}")
    print(f"Entities: {result.entity_count}")
    print(f"Maximum entity age seconds: {assessment.freshness.maximum_entity_age_seconds}")
    print(f"Overall quality status: {assessment.overall_status}")
    print("Results: " + ", ".join(f"{status}={counts[status]}" for status in counts))
    print("Persistence: " + ("inserted" if result.inserted else "already ingested" if not args.no_persist else "disabled"))
    if result.warehouse_relative_path:
        print(f"Warehouse: {result.warehouse_relative_path}")
    return 0


def main() -> None:
    raise SystemExit(run_cli())


if __name__ == "__main__":
    main()
