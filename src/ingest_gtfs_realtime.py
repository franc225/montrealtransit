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
from gtfs_realtime_persistence import (
    PARSER_MODEL_SCHEMA_VERSION,
    PERSISTENCE_SCHEMA_VERSION,
    capture_extension_values,
    ensure_realtime_schema,
    entity_row,
    stop_time_update_rows,
    trip_update_row,
    vehicle_position_row,
)
from parse_gtfs_realtime import ParserError, decode_feed, validate_capture


DEFAULT_WAREHOUSE_RELATIVE_PATH = Path("data/warehouse/montreal_transit.duckdb")


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
    ensure_realtime_schema(connection)


def _previous_capture(connection: duckdb.DuckDBPyConnection, provider: str, feed_type: str) -> PreviousCapture | None:
    row = connection.execute(
        """SELECT capture_uuid, captured_at_utc, feed_timestamp_unix, payload_sha256
           FROM gtfs_realtime_capture WHERE provider = ? AND feed_type = ?
           ORDER BY captured_at_utc DESC, capture_uuid DESC LIMIT 1""",
        [provider, feed_type],
    ).fetchone()
    return PreviousCapture(str(row[0]), row[1], row[2], str(row[3])) if row else None


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
        "SELECT payload_sha256, persistence_schema_version FROM gtfs_realtime_capture WHERE capture_uuid = ?", [capture_uuid]
    ).fetchone()
    if existing:
        if existing[0] != capture.payload_sha256:
            raise IngestionError("Capture UUID already exists with a different payload SHA-256.")
        if existing[1] != PERSISTENCE_SCHEMA_VERSION:
            raise IngestionError(
                "Capture was ingested with an older incomplete persistence schema; "
                "implicit destructive reingestion is not supported."
            )
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
            """INSERT INTO gtfs_realtime_capture (
                capture_uuid, provider, feed_type, captured_at_utc, filename_timestamp_utc,
                feed_timestamp_unix, feed_timestamp_utc, gtfs_realtime_version, incrementality,
                payload_relative_path, metadata_relative_path, payload_size_bytes, payload_sha256,
                total_entity_count, deleted_entity_count, supported_entity_count,
                unsupported_entity_count, ingested_at_utc, parser_schema_version,
                incrementality_name, vehicle_position_count, trip_update_count,
                entities_missing_business_identifiers, validation_finding_count,
                parser_model_schema_version, sha256_verified, persistence_schema_version
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            [capture_uuid, feed.provider, feed.feed_type, feed.captured_at_utc,
             feed.captured_at_utc.strftime("%Y%m%dT%H%M%SZ"), feed.header.timestamp,
             feed.header.timestamp_utc, feed.header.gtfs_realtime_version, feed.header.incrementality,
             capture.payload_relative_path, capture.metadata_relative_path,
             capture.payload_size_bytes, capture.payload_sha256, len(feed.entities),
             feed.summary.deleted_entity_count,
             len(feed.entities) - feed.summary.unsupported_entity_count,
             feed.summary.unsupported_entity_count, analyzed_at, PARSER_MODEL_SCHEMA_VERSION,
             *capture_extension_values(feed)],
        )
        for entity in feed.entities:
            connection.execute(
                """INSERT INTO gtfs_realtime_entity (
                    capture_uuid, entity_index, entity_id, entity_type, is_deleted,
                    expected_for_feed_type, supported, trip_id, route_id, direction_id,
                    vehicle_id, parser_model_schema_version, persistence_schema_version
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                entity_row(capture_uuid, entity, expected_type),
            )
            if entity.vehicle_position is not None:
                connection.execute(
                    """INSERT INTO gtfs_realtime_vehicle_position (
                        capture_uuid, entity_index, trip_id, route_id, direction_id, start_date,
                        start_time, vehicle_id, latitude, longitude, bearing, speed,
                        current_stop_sequence, stop_id, current_status, timestamp_unix, timestamp_utc,
                        occupancy_status, occupancy_percentage, trip_schedule_relationship,
                        trip_schedule_relationship_name, vehicle_label, vehicle_license_plate,
                        odometer, current_status_name, congestion_level, congestion_level_name,
                        occupancy_status_name, parser_model_schema_version, persistence_schema_version
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    vehicle_position_row(capture_uuid, entity),
                )
                if failure_hook:
                    failure_hook("after_vehicle_position")
            if entity.trip_update is not None:
                connection.execute(
                    """INSERT INTO gtfs_realtime_trip_update (
                        capture_uuid, entity_index, trip_id, route_id, direction_id, start_date,
                        start_time, schedule_relationship, vehicle_id, timestamp_unix, timestamp_utc,
                        trip_delay_seconds, stop_time_update_count, schedule_relationship_name,
                        vehicle_label, vehicle_license_plate, parser_model_schema_version,
                        persistence_schema_version
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    trip_update_row(capture_uuid, entity),
                )
                if failure_hook:
                    failure_hook("after_trip_update")
                for row in stop_time_update_rows(capture_uuid, entity):
                    connection.execute(
                        """INSERT INTO gtfs_realtime_stop_time_update (
                            capture_uuid, entity_index, stop_time_update_index, stop_sequence,
                            stop_id, schedule_relationship, arrival_delay_seconds, arrival_time_unix,
                            arrival_time_utc, departure_delay_seconds, departure_time_unix,
                            departure_time_utc, schedule_relationship_name, arrival_uncertainty,
                            arrival_scheduled_time_unix, arrival_scheduled_time_utc,
                            departure_uncertainty, departure_scheduled_time_unix,
                            departure_scheduled_time_utc, parser_model_schema_version,
                            persistence_schema_version
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        row,
                    )
                    if failure_hook:
                        failure_hook("after_stop_time_update")
        for finding_index, finding in enumerate(feed.findings):
            connection.execute(
                """INSERT INTO gtfs_realtime_parser_finding VALUES (?, ?, ?, ?, ?, ?, ?)""",
                [capture_uuid, finding_index, finding.code, finding.message,
                 finding.entity_index, PARSER_MODEL_SCHEMA_VERSION,
                 PERSISTENCE_SCHEMA_VERSION],
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
