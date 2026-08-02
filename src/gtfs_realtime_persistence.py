from __future__ import annotations

from typing import Iterable

import duckdb

from parse_gtfs_realtime import ParsedEntity, ParsedFeed


PARSER_MODEL_SCHEMA_VERSION = 1
PERSISTENCE_SCHEMA_VERSION = 2


BASE_TABLES = (
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


ADDITIVE_COLUMNS = {
    "gtfs_realtime_capture": (
        ("incrementality_name", "VARCHAR"),
        ("vehicle_position_count", "INTEGER"),
        ("trip_update_count", "INTEGER"),
        ("entities_missing_business_identifiers", "INTEGER"),
        ("validation_finding_count", "INTEGER"),
        ("sha256_verified", "BOOLEAN"),
        ("parser_model_schema_version", "INTEGER"),
        ("persistence_schema_version", "INTEGER"),
    ),
    "gtfs_realtime_entity": (
        ("parser_model_schema_version", "INTEGER"),
        ("persistence_schema_version", "INTEGER"),
    ),
    "gtfs_realtime_vehicle_position": (
        ("trip_schedule_relationship", "INTEGER"),
        ("trip_schedule_relationship_name", "VARCHAR"),
        ("vehicle_label", "VARCHAR"),
        ("vehicle_license_plate", "VARCHAR"),
        ("odometer", "DOUBLE"),
        ("current_status_name", "VARCHAR"),
        ("congestion_level", "INTEGER"),
        ("congestion_level_name", "VARCHAR"),
        ("occupancy_status_name", "VARCHAR"),
        ("parser_model_schema_version", "INTEGER"),
        ("persistence_schema_version", "INTEGER"),
    ),
    "gtfs_realtime_trip_update": (
        ("schedule_relationship_name", "VARCHAR"),
        ("vehicle_label", "VARCHAR"),
        ("vehicle_license_plate", "VARCHAR"),
        ("parser_model_schema_version", "INTEGER"),
        ("persistence_schema_version", "INTEGER"),
    ),
    "gtfs_realtime_stop_time_update": (
        ("schedule_relationship_name", "VARCHAR"),
        ("arrival_uncertainty", "INTEGER"),
        ("arrival_scheduled_time_unix", "BIGINT"),
        ("arrival_scheduled_time_utc", "TIMESTAMPTZ"),
        ("departure_uncertainty", "INTEGER"),
        ("departure_scheduled_time_unix", "BIGINT"),
        ("departure_scheduled_time_utc", "TIMESTAMPTZ"),
        ("parser_model_schema_version", "INTEGER"),
        ("persistence_schema_version", "INTEGER"),
    ),
}


def ensure_realtime_schema(connection: duckdb.DuckDBPyConnection) -> None:
    """Create the legacy-compatible base and apply the additive v2 migration."""
    connection.execute("BEGIN TRANSACTION")
    try:
        for statement in BASE_TABLES:
            connection.execute(statement)
        for table, columns in ADDITIVE_COLUMNS.items():
            for column, sql_type in columns:
                connection.execute(
                    f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {column} {sql_type}"
                )
        connection.execute(
            """CREATE TABLE IF NOT EXISTS gtfs_realtime_parser_finding (
                capture_uuid VARCHAR NOT NULL, finding_index INTEGER NOT NULL,
                finding_code VARCHAR NOT NULL, finding_message VARCHAR NOT NULL,
                entity_index INTEGER, parser_model_schema_version INTEGER NOT NULL,
                persistence_schema_version INTEGER NOT NULL,
                PRIMARY KEY (capture_uuid, finding_index))"""
        )
        connection.execute("COMMIT")
    except Exception:
        connection.execute("ROLLBACK")
        raise


def descriptor_values(entity: ParsedEntity) -> tuple[object, ...]:
    payload = entity.vehicle_position or entity.trip_update
    trip = payload.trip if payload is not None else None
    vehicle = payload.vehicle if payload is not None else None
    return (
        trip.trip_id if trip else None,
        trip.route_id if trip else None,
        trip.direction_id if trip else None,
        trip.start_time if trip else None,
        trip.start_date if trip else None,
        trip.schedule_relationship if trip else None,
        trip.schedule_relationship_name if trip else None,
        vehicle.vehicle_id if vehicle else None,
        vehicle.label if vehicle else None,
        vehicle.license_plate if vehicle else None,
    )


def entity_row(capture_uuid: str, entity: ParsedEntity, expected_type: str) -> list[object]:
    trip_id, route_id, direction_id, _, _, _, _, vehicle_id, _, _ = descriptor_values(entity)
    return [
        capture_uuid, entity.original_index, entity.entity_id, entity.entity_type,
        entity.is_deleted, entity.entity_type == expected_type,
        entity.entity_type != "unsupported", trip_id, route_id, direction_id, vehicle_id,
        PARSER_MODEL_SCHEMA_VERSION, PERSISTENCE_SCHEMA_VERSION,
    ]


def vehicle_position_row(capture_uuid: str, entity: ParsedEntity) -> list[object]:
    vehicle = entity.vehicle_position
    if vehicle is None:
        raise ValueError("Entity does not contain a Vehicle Position.")
    trip_id, route_id, direction_id, start_time, start_date, relationship, relationship_name, vehicle_id, label, license_plate = descriptor_values(entity)
    position = vehicle.position
    return [
        capture_uuid, entity.original_index, trip_id, route_id, direction_id,
        start_date, start_time, vehicle_id,
        position.latitude if position else None, position.longitude if position else None,
        position.bearing if position else None, position.speed if position else None,
        vehicle.current_stop_sequence, vehicle.stop_id, vehicle.current_status,
        vehicle.timestamp, vehicle.timestamp_utc, vehicle.occupancy_status,
        vehicle.occupancy_percentage, relationship, relationship_name, label,
        license_plate, position.odometer if position else None,
        vehicle.current_status_name, vehicle.congestion_level,
        vehicle.congestion_level_name, vehicle.occupancy_status_name,
        PARSER_MODEL_SCHEMA_VERSION, PERSISTENCE_SCHEMA_VERSION,
    ]


def trip_update_row(capture_uuid: str, entity: ParsedEntity) -> list[object]:
    update = entity.trip_update
    if update is None:
        raise ValueError("Entity does not contain a Trip Update.")
    trip_id, route_id, direction_id, start_time, start_date, relationship, relationship_name, vehicle_id, label, license_plate = descriptor_values(entity)
    return [
        capture_uuid, entity.original_index, trip_id, route_id, direction_id,
        start_date, start_time, relationship, vehicle_id, update.timestamp,
        update.timestamp_utc, update.delay, len(update.stop_time_updates),
        relationship_name, label, license_plate,
        PARSER_MODEL_SCHEMA_VERSION, PERSISTENCE_SCHEMA_VERSION,
    ]


def stop_time_update_rows(capture_uuid: str, entity: ParsedEntity) -> Iterable[list[object]]:
    if entity.trip_update is None:
        return ()
    rows: list[list[object]] = []
    for index, stop in enumerate(entity.trip_update.stop_time_updates):
        arrival, departure = stop.arrival, stop.departure
        rows.append([
            capture_uuid, entity.original_index, index, stop.stop_sequence, stop.stop_id,
            stop.schedule_relationship,
            arrival.delay if arrival else None, arrival.time if arrival else None,
            arrival.time_utc if arrival else None,
            departure.delay if departure else None, departure.time if departure else None,
            departure.time_utc if departure else None,
            stop.schedule_relationship_name,
            arrival.uncertainty if arrival else None,
            arrival.scheduled_time if arrival else None,
            arrival.scheduled_time_utc if arrival else None,
            departure.uncertainty if departure else None,
            departure.scheduled_time if departure else None,
            departure.scheduled_time_utc if departure else None,
            PARSER_MODEL_SCHEMA_VERSION, PERSISTENCE_SCHEMA_VERSION,
        ])
    return tuple(rows)


def capture_extension_values(feed: ParsedFeed) -> list[object]:
    return [
        feed.header.incrementality_name, feed.summary.vehicle_position_count,
        feed.summary.trip_update_count, feed.summary.entities_missing_business_identifiers,
        feed.summary.validation_finding_count, PARSER_MODEL_SCHEMA_VERSION,
        feed.sha256_verified, PERSISTENCE_SCHEMA_VERSION,
    ]
