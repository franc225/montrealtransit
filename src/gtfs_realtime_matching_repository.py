from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Callable

import duckdb

from gtfs_realtime_matching import (
    CalendarException,
    CalendarService,
    MatchAssessment,
    MatchingConfig,
    MatchingError,
    RealtimeEntityFact,
    RealtimeStopFact,
    StaticSnapshot,
    StaticStopTime,
    StaticTripCandidate,
)


@dataclass(frozen=True)
class MatchingSource:
    capture_uuid: str
    captured_at_utc: datetime
    payload_sha256: str
    persistence_schema_version: int | None
    snapshot: StaticSnapshot
    entities: tuple[RealtimeEntityFact, ...]
    trips: tuple[StaticTripCandidate, ...]
    calendars: tuple[CalendarService, ...]
    exceptions: tuple[CalendarException, ...]
    stop_times: tuple[StaticStopTime, ...]


@dataclass(frozen=True)
class MatchPersistenceResult:
    match_run_id: str
    inserted: bool


def _table_exists(connection: duckdb.DuckDBPyConnection, table: str) -> bool:
    return connection.execute(
        "SELECT count(*) FROM information_schema.tables WHERE table_name = ?", [table]
    ).fetchone()[0] == 1


def _parse_date(value: object) -> date | None:
    if value is None:
        return None
    if isinstance(value, date):
        return value
    text = str(value).replace("-", "")
    try:
        return datetime.strptime(text, "%Y%m%d").date()
    except ValueError:
        return None


def load_matching_source(connection: duckdb.DuckDBPyConnection, capture_uuid: str,
                         config: MatchingConfig) -> MatchingSource:
    required = (
        "gtfs_realtime_capture", "gtfs_realtime_entity", "dim_trip",
        "fct_scheduled_stop_time", "raw_calendar_dates", "meta_gtfs_feed",
    )
    missing = tuple(table for table in required if not _table_exists(connection, table))
    if missing:
        raise MatchingError("Required matching tables are missing: " + ", ".join(missing) + ".")
    capture = connection.execute(
        """SELECT captured_at_utc, payload_sha256, persistence_schema_version
           FROM gtfs_realtime_capture WHERE capture_uuid = ?""", [capture_uuid]
    ).fetchone()
    if capture is None:
        raise MatchingError("GTFS-Realtime capture UUID was not found.")
    if capture[2] != config.required_persistence_schema_version:
        raise MatchingError("Capture has incomplete persistence lineage; schema version 2 is required.")
    meta = connection.execute(
        """SELECT ingestion_run_id,
                  nullif(trim(feed_version), ''),
                  try_strptime(feed_start_date, '%Y%m%d')::DATE,
                  try_strptime(feed_end_date, '%Y%m%d')::DATE
           FROM meta_gtfs_feed ORDER BY ingested_at DESC LIMIT 1"""
    ).fetchone()
    if meta is None:
        raise MatchingError("No static GTFS snapshot metadata is available.")
    snapshot_identifier = f"{meta[0]}:{meta[1] or 'unversioned'}"
    snapshot = StaticSnapshot(snapshot_identifier, str(meta[0]), meta[1], meta[2], meta[3])
    frequency_ids: set[str] = set()
    if _table_exists(connection, "raw_frequencies"):
        frequency_ids = {str(row[0]).strip() for row in connection.execute("SELECT trip_id FROM raw_frequencies").fetchall()}
    start_times = {
        str(row[0]): row[1]
        for row in connection.execute(
            """SELECT trip_id, coalesce(
                   min(arrival_time) FILTER (WHERE stop_sequence = first_sequence),
                   min(departure_time) FILTER (WHERE stop_sequence = first_sequence))
               FROM (SELECT *, min(stop_sequence) OVER (PARTITION BY trip_id) AS first_sequence
                     FROM fct_scheduled_stop_time)
               GROUP BY trip_id"""
        ).fetchall()
    }
    trips = tuple(
        StaticTripCandidate(str(row[0]), str(row[1]), str(row[2]), row[3], row[4],
                            start_times.get(str(row[0])), str(row[0]) in frequency_ids)
        for row in connection.execute(
            "SELECT trip_id, route_id, service_id, direction_id, shape_id FROM dim_trip"
        ).fetchall()
    )
    calendars: tuple[CalendarService, ...] = ()
    if _table_exists(connection, "dim_service"):
        calendars = tuple(
            CalendarService(str(row[0]), tuple(bool(value) for value in row[1:8]), row[8], row[9])
            for row in connection.execute(
                "SELECT service_id, monday, tuesday, wednesday, thursday, friday, saturday, sunday, start_date, end_date FROM dim_service"
            ).fetchall()
            if row[8] is not None and row[9] is not None
        )
    exceptions = tuple(
        CalendarException(str(row[0]).strip(), parsed, int(row[2]))
        for row in connection.execute("SELECT service_id, date, exception_type FROM raw_calendar_dates").fetchall()
        if (parsed := _parse_date(row[1])) is not None
    )
    stop_times = tuple(
        StaticStopTime(str(row[0]), int(row[1]), str(row[2]), row[3], row[4])
        for row in connection.execute(
            "SELECT trip_id, stop_sequence, stop_id, arrival_time, departure_time FROM fct_scheduled_stop_time ORDER BY trip_id, stop_sequence"
        ).fetchall()
    )
    entity_rows = connection.execute(
        """SELECT e.entity_index, e.entity_id, e.entity_type, e.is_deleted,
                  e.persistence_schema_version,
                  coalesce(v.trip_id, u.trip_id), coalesce(v.route_id, u.route_id),
                  coalesce(v.direction_id, u.direction_id), coalesce(v.start_time, u.start_time),
                  coalesce(v.start_date, u.start_date),
                  coalesce(v.trip_schedule_relationship, u.schedule_relationship),
                  coalesce(v.trip_schedule_relationship_name, u.schedule_relationship_name),
                  coalesce(v.vehicle_id, u.vehicle_id), u.trip_delay_seconds
           FROM gtfs_realtime_entity e
           LEFT JOIN gtfs_realtime_vehicle_position v USING (capture_uuid, entity_index)
           LEFT JOIN gtfs_realtime_trip_update u USING (capture_uuid, entity_index)
           WHERE e.capture_uuid = ? ORDER BY e.entity_index""", [capture_uuid]
    ).fetchall()
    entities: list[RealtimeEntityFact] = []
    for row in entity_rows:
        updates = tuple(
            RealtimeStopFact(*update)
            for update in connection.execute(
                """SELECT stop_time_update_index, stop_sequence, stop_id,
                          schedule_relationship, schedule_relationship_name,
                          arrival_delay_seconds, arrival_time_utc,
                          departure_delay_seconds, departure_time_utc
                   FROM gtfs_realtime_stop_time_update
                   WHERE capture_uuid = ? AND entity_index = ?
                   ORDER BY stop_time_update_index""", [capture_uuid, row[0]]
            ).fetchall()
        )
        entities.append(RealtimeEntityFact(capture_uuid, *row, updates))
    return MatchingSource(capture_uuid, capture[0], str(capture[1]), capture[2], snapshot,
                          tuple(entities), trips, calendars, exceptions, stop_times)


def ensure_matching_schema(connection: duckdb.DuckDBPyConnection) -> None:
    statements = (
        """CREATE TABLE IF NOT EXISTS gtfs_realtime_match_run (
            match_run_id VARCHAR PRIMARY KEY, capture_uuid VARCHAR NOT NULL,
            payload_sha256 VARCHAR NOT NULL, realtime_persistence_schema_version INTEGER NOT NULL,
            static_snapshot_identifier VARCHAR NOT NULL, static_ingestion_run_id VARCHAR NOT NULL,
            static_feed_version VARCHAR, matching_algorithm_version VARCHAR NOT NULL,
            matching_config_schema_version INTEGER NOT NULL, matched_at_utc TIMESTAMPTZ NOT NULL,
            overall_status VARCHAR NOT NULL, entity_count INTEGER NOT NULL,
            matched_count INTEGER NOT NULL, unmatched_count INTEGER NOT NULL,
            ambiguous_count INTEGER NOT NULL, conflict_count INTEGER NOT NULL,
            unsupported_count INTEGER NOT NULL, not_applicable_count INTEGER NOT NULL,
            UNIQUE(capture_uuid, static_snapshot_identifier, matching_algorithm_version,
                   matching_config_schema_version))""",
        """CREATE TABLE IF NOT EXISTS gtfs_realtime_trip_match (
            match_run_id VARCHAR NOT NULL, capture_uuid VARCHAR NOT NULL, entity_index INTEGER NOT NULL,
            entity_id VARCHAR NOT NULL, entity_type VARCHAR NOT NULL,
            realtime_persistence_schema_version INTEGER NOT NULL,
            schedule_relationship INTEGER, schedule_relationship_name VARCHAR,
            relationship_treatment VARCHAR NOT NULL, resolved_service_date DATE,
            service_date_source VARCHAR NOT NULL, service_date_candidate_count INTEGER NOT NULL,
            static_snapshot_identifier VARCHAR NOT NULL, static_trip_id VARCHAR,
            static_route_id VARCHAR, static_direction_id INTEGER, static_service_id VARCHAR,
            static_shape_id VARCHAR, match_status VARCHAR NOT NULL, match_method VARCHAR NOT NULL,
            candidate_count INTEGER NOT NULL, conflict_code VARCHAR, details VARCHAR NOT NULL,
            realtime_trip_delay_seconds INTEGER, PRIMARY KEY(match_run_id, entity_index))""",
        """CREATE TABLE IF NOT EXISTS gtfs_realtime_stop_time_match (
            match_run_id VARCHAR NOT NULL, capture_uuid VARCHAR NOT NULL, entity_index INTEGER NOT NULL,
            stop_time_update_index INTEGER NOT NULL, realtime_stop_sequence INTEGER,
            realtime_stop_id VARCHAR, static_stop_sequence INTEGER, static_stop_id VARCHAR,
            scheduled_arrival_time VARCHAR, scheduled_arrival_service_seconds INTEGER,
            scheduled_arrival_day_offset INTEGER, scheduled_arrival_local TIMESTAMPTZ,
            scheduled_arrival_utc TIMESTAMPTZ, arrival_time_resolution_status VARCHAR NOT NULL,
            scheduled_departure_time VARCHAR, scheduled_departure_service_seconds INTEGER,
            scheduled_departure_day_offset INTEGER, scheduled_departure_local TIMESTAMPTZ,
            scheduled_departure_utc TIMESTAMPTZ, departure_time_resolution_status VARCHAR NOT NULL,
            realtime_arrival_delay_seconds INTEGER, realtime_arrival_utc TIMESTAMPTZ,
            calculated_arrival_delta_seconds INTEGER, arrival_delta_source VARCHAR NOT NULL,
            arrival_consistency_difference_seconds INTEGER,
            realtime_departure_delay_seconds INTEGER, realtime_departure_utc TIMESTAMPTZ,
            calculated_departure_delta_seconds INTEGER, departure_delta_source VARCHAR NOT NULL,
            departure_consistency_difference_seconds INTEGER,
            stop_schedule_relationship INTEGER, stop_schedule_relationship_name VARCHAR,
            match_status VARCHAR NOT NULL, match_method VARCHAR NOT NULL,
            conflict_code VARCHAR, details VARCHAR NOT NULL,
            PRIMARY KEY(match_run_id, entity_index, stop_time_update_index))""",
        """CREATE TABLE IF NOT EXISTS gtfs_realtime_match_finding (
            match_run_id VARCHAR NOT NULL, finding_index INTEGER NOT NULL,
            entity_index INTEGER, stop_time_update_index INTEGER,
            finding_code VARCHAR NOT NULL, details VARCHAR NOT NULL,
            PRIMARY KEY(match_run_id, finding_index))""",
    )
    connection.execute("BEGIN TRANSACTION")
    try:
        for statement in statements:
            connection.execute(statement)
        connection.execute("COMMIT")
    except Exception:
        connection.execute("ROLLBACK")
        raise


def persist_assessment(connection: duckdb.DuckDBPyConnection, source: MatchingSource,
                       assessment: MatchAssessment, config: MatchingConfig,
                       failure_hook: Callable[[str], None] | None = None) -> MatchPersistenceResult:
    existing = connection.execute(
        """SELECT match_run_id, payload_sha256, realtime_persistence_schema_version
           FROM gtfs_realtime_match_run WHERE capture_uuid = ? AND static_snapshot_identifier = ?
             AND matching_algorithm_version = ? AND matching_config_schema_version = ?""",
        [source.capture_uuid, source.snapshot.snapshot_identifier,
         config.matching_algorithm_version, config.schema_version],
    ).fetchone()
    if existing:
        if existing[1] != source.payload_sha256 or existing[2] != source.persistence_schema_version:
            raise MatchingError("Existing matching run has incompatible source lineage.")
        return MatchPersistenceResult(str(existing[0]), False)
    match_run_id = str(uuid.uuid4())
    counts = assessment.status_counts
    try:
        connection.execute("BEGIN TRANSACTION")
        connection.execute(
            """INSERT INTO gtfs_realtime_match_run VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            [match_run_id, source.capture_uuid, source.payload_sha256,
             source.persistence_schema_version, source.snapshot.snapshot_identifier,
             source.snapshot.ingestion_run_id, source.snapshot.feed_version,
             config.matching_algorithm_version, config.schema_version, datetime.now(timezone.utc),
             assessment.overall_status, len(assessment.results), counts["MATCHED"],
             counts["UNMATCHED"], counts["AMBIGUOUS"], counts["CONFLICT"],
             counts["UNSUPPORTED"], counts["NOT_APPLICABLE"]],
        )
        findings: list[tuple[int | None, int | None, str, str]] = []
        entity_by_index = {entity.entity_index: entity for entity in source.entities}
        for result in assessment.results:
            entity = entity_by_index[result.entity_index]
            trip = result.static_trip
            connection.execute(
                """INSERT INTO gtfs_realtime_trip_match VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                [match_run_id, source.capture_uuid, result.entity_index, result.entity_id,
                 result.entity_type, source.persistence_schema_version,
                 entity.schedule_relationship, entity.schedule_relationship_name,
                 result.relationship_treatment, result.service_date, result.service_date_source,
                 result.service_date_candidate_count, source.snapshot.snapshot_identifier,
                 trip.trip_id if trip else None, trip.route_id if trip else None,
                 trip.direction_id if trip else None, trip.service_id if trip else None,
                 trip.shape_id if trip else None, result.status, result.method,
                 result.candidate_count, result.conflict_code, result.details, entity.trip_delay],
            )
            if result.conflict_code:
                findings.append((result.entity_index, None, result.conflict_code, result.details))
            if failure_hook:
                failure_hook("after_trip_match")
            for stop in result.stop_matches:
                connection.execute(
                    """INSERT INTO gtfs_realtime_stop_time_match VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    [match_run_id, source.capture_uuid, result.entity_index, stop.update_index,
                     stop.realtime_stop_sequence, stop.realtime_stop_id,
                     stop.static_stop_sequence, stop.static_stop_id,
                     stop.arrival.original, stop.arrival.service_seconds,
                     stop.arrival.service_day_offset, stop.arrival.local_datetime,
                     stop.arrival.utc_datetime, stop.arrival.resolution_status,
                     stop.departure.original, stop.departure.service_seconds,
                     stop.departure.service_day_offset, stop.departure.local_datetime,
                     stop.departure.utc_datetime, stop.departure.resolution_status,
                     stop.arrival_comparison.reported_delay, stop.realtime_arrival_utc,
                     stop.arrival_comparison.absolute_delta, stop.arrival_comparison.delta_source,
                     stop.arrival_comparison.consistency_difference,
                     stop.departure_comparison.reported_delay, stop.realtime_departure_utc,
                     stop.departure_comparison.absolute_delta, stop.departure_comparison.delta_source,
                     stop.departure_comparison.consistency_difference,
                     stop.realtime_schedule_relationship, stop.realtime_schedule_relationship_name,
                     stop.status, stop.method, stop.conflict_code, stop.details],
                )
                if stop.conflict_code:
                    findings.append((result.entity_index, stop.update_index, stop.conflict_code, stop.details))
                if failure_hook:
                    failure_hook("after_stop_match")
        for index, finding in enumerate(findings):
            connection.execute(
                "INSERT INTO gtfs_realtime_match_finding VALUES (?, ?, ?, ?, ?, ?)",
                [match_run_id, index, *finding],
            )
            if failure_hook:
                failure_hook("after_finding")
        connection.execute("COMMIT")
    except Exception:
        connection.execute("ROLLBACK")
        raise
    return MatchPersistenceResult(match_run_id, True)
