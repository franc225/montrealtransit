from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable

import duckdb

from gtfs_realtime_reliability import (
    AggregateReliabilityResult,
    EventReliabilityResult,
    MatchRunLineage,
    ReliabilityAssessment,
    ReliabilityConfiguration,
    ReliabilityError,
    ReliabilityObservation,
    TripRelationshipFact,
)


@dataclass(frozen=True)
class ReliabilitySource:
    lineage: MatchRunLineage
    observations: tuple[ReliabilityObservation, ...]
    relationships: tuple[TripRelationshipFact, ...]
    route_filter: str | None


@dataclass(frozen=True)
class ReliabilityPersistenceResult:
    reliability_run_id: str
    inserted: bool


def _table_exists(connection: duckdb.DuckDBPyConnection, table: str) -> bool:
    return connection.execute(
        "SELECT count(*) FROM information_schema.tables WHERE table_name = ?", [table]
    ).fetchone()[0] == 1


def load_reliability_source(connection: duckdb.DuckDBPyConnection, match_run_id: str,
                            config: ReliabilityConfiguration,
                            route_id: str | None = None) -> ReliabilitySource:
    required = ("gtfs_realtime_match_run", "gtfs_realtime_trip_match",
                "gtfs_realtime_stop_time_match", "gtfs_realtime_capture")
    missing = [table for table in required if not _table_exists(connection, table)]
    if missing:
        raise ReliabilityError("Required reliability source tables are missing: " + ", ".join(missing) + ".")
    row = connection.execute(
        """SELECT match_run_id, static_snapshot_identifier, matching_algorithm_version,
                  matching_config_schema_version, realtime_persistence_schema_version,
                  payload_sha256
           FROM gtfs_realtime_match_run WHERE match_run_id = ?""", [match_run_id]
    ).fetchone()
    if row is None:
        raise ReliabilityError("GTFS-Realtime matching run was not found.")
    lineage = MatchRunLineage(str(row[0]), str(row[1]), str(row[2]), int(row[3]), int(row[4]), str(row[5]))
    if lineage.realtime_persistence_schema_version != config.required_persistence_schema_version:
        raise ReliabilityError("Matching run has incomplete realtime persistence lineage.")
    if lineage.matching_algorithm_version != config.required_matching_algorithm_version:
        raise ReliabilityError("Matching algorithm version is incompatible with reliability policy.")
    route_clause = " AND (? IS NULL OR t.static_route_id = ?)"
    parameters = [match_run_id, route_id, route_id]
    relationships = tuple(
        TripRelationshipFact(str(item[0]), int(item[1]), item[2], item[3], item[4], item[5],
                             str(item[6]), str(item[7]), item[8])
        for item in connection.execute(
            """SELECT t.capture_uuid, t.entity_index, t.static_route_id, t.static_direction_id,
                      t.resolved_service_date, t.static_trip_id, t.match_status,
                      t.relationship_treatment, t.schedule_relationship_name
               FROM gtfs_realtime_trip_match t
               WHERE t.match_run_id = ?""" + route_clause + " ORDER BY t.entity_index",
            parameters,
        ).fetchall()
    )
    rows = connection.execute(
        """SELECT s.capture_uuid, c.captured_at_utc, s.entity_index,
                  s.stop_time_update_index, c.provider, t.static_snapshot_identifier,
                  t.resolved_service_date, t.static_trip_id, t.static_route_id,
                  t.static_direction_id, s.static_stop_sequence, s.static_stop_id,
                  s.scheduled_arrival_utc, s.scheduled_arrival_service_seconds,
                  s.realtime_arrival_delay_seconds, s.calculated_arrival_delta_seconds,
                  s.arrival_consistency_difference_seconds, s.arrival_time_resolution_status,
                  s.scheduled_departure_utc, s.scheduled_departure_service_seconds,
                  s.realtime_departure_delay_seconds, s.calculated_departure_delta_seconds,
                  s.departure_consistency_difference_seconds, s.departure_time_resolution_status,
                  t.match_status, t.match_method, s.match_status,
                  t.relationship_treatment, t.schedule_relationship_name
           FROM gtfs_realtime_stop_time_match s
           JOIN gtfs_realtime_trip_match t USING (match_run_id, capture_uuid, entity_index)
           JOIN gtfs_realtime_capture c USING (capture_uuid)
           WHERE s.match_run_id = ?""" + route_clause +
        " ORDER BY s.capture_uuid, s.entity_index, s.stop_time_update_index",
        parameters,
    ).fetchall()
    observations: list[ReliabilityObservation] = []
    for item in rows:
        common = dict(
            match_run_id=match_run_id, capture_uuid=str(item[0]), captured_at_utc=item[1],
            entity_index=int(item[2]), stop_time_update_index=int(item[3]), provider=str(item[4]),
            static_snapshot_identifier=str(item[5]), service_date=item[6], static_trip_id=item[7],
            static_route_id=item[8], direction_id=item[9], static_stop_sequence=item[10],
            static_stop_id=item[11], trip_match_status=str(item[24]), trip_match_method=str(item[25]),
            stop_match_status=str(item[26]), relationship_treatment=str(item[27]),
            schedule_relationship_name=item[28],
        )
        observations.append(ReliabilityObservation(
            **common, event_type="ARRIVAL", scheduled_utc=item[12], scheduled_service_seconds=item[13],
            reported_delay_seconds=item[14], calculated_delta_seconds=item[15],
            consistency_difference_seconds=item[16], time_resolution_status=str(item[17]),
        ))
        observations.append(ReliabilityObservation(
            **common, event_type="DEPARTURE", scheduled_utc=item[18], scheduled_service_seconds=item[19],
            reported_delay_seconds=item[20], calculated_delta_seconds=item[21],
            consistency_difference_seconds=item[22], time_resolution_status=str(item[23]),
        ))
    return ReliabilitySource(lineage, tuple(observations), relationships, route_id)


def ensure_reliability_schema(connection: duckdb.DuckDBPyConnection) -> None:
    statements = (
        """CREATE TABLE IF NOT EXISTS gtfs_realtime_reliability_run (
            reliability_run_id VARCHAR PRIMARY KEY, input_scope_type VARCHAR NOT NULL,
            match_run_id VARCHAR NOT NULL, route_filter VARCHAR,
            static_snapshot_identifier VARCHAR NOT NULL, matching_algorithm_version VARCHAR NOT NULL,
            matching_config_schema_version INTEGER NOT NULL,
            realtime_persistence_schema_version INTEGER NOT NULL,
            source_payload_sha256 VARCHAR NOT NULL, reliability_algorithm_version VARCHAR NOT NULL,
            reliability_config_schema_version INTEGER NOT NULL, analyzed_at_utc TIMESTAMPTZ NOT NULL,
            overall_status VARCHAR NOT NULL, source_observation_count INTEGER NOT NULL,
            canonical_event_count INTEGER NOT NULL, eligible_event_count INTEGER NOT NULL,
            classified_event_count INTEGER NOT NULL, finding_count INTEGER NOT NULL,
            reported_cancellation_count INTEGER NOT NULL,
            reported_cancellation_denominator INTEGER NOT NULL,
            reported_cancellation_ratio DOUBLE,
            UNIQUE(match_run_id, route_filter, reliability_algorithm_version,
                   reliability_config_schema_version))""",
        """CREATE TABLE IF NOT EXISTS gtfs_realtime_reliability_event (
            reliability_run_id VARCHAR NOT NULL, event_index INTEGER NOT NULL,
            capture_uuid VARCHAR NOT NULL,
            match_run_id VARCHAR NOT NULL, entity_index INTEGER NOT NULL,
            stop_time_update_index INTEGER NOT NULL, provider VARCHAR NOT NULL,
            static_snapshot_identifier VARCHAR NOT NULL, service_date DATE,
            static_trip_id VARCHAR, static_route_id VARCHAR, direction_id INTEGER,
            static_stop_sequence INTEGER, static_stop_id VARCHAR, event_type VARCHAR NOT NULL,
            scheduled_utc TIMESTAMPTZ, scheduled_service_seconds INTEGER,
            reported_delay_seconds INTEGER, calculated_delta_seconds INTEGER,
            selected_delta_seconds INTEGER, selected_delta_source VARCHAR NOT NULL,
            consistency_difference_seconds INTEGER, eligibility_status VARCHAR NOT NULL,
            exclusion_reason VARCHAR, punctuality_classification VARCHAR NOT NULL,
            candidate_observation_count INTEGER NOT NULL, first_observed_at_utc TIMESTAMPTZ NOT NULL,
            selected_observed_at_utc TIMESTAMPTZ NOT NULL,
            delta_changed_across_observations BOOLEAN NOT NULL,
            threshold_config_schema_version INTEGER NOT NULL,
            PRIMARY KEY(reliability_run_id, event_index))""",
        """CREATE TABLE IF NOT EXISTS gtfs_realtime_reliability_trip (
            reliability_run_id VARCHAR NOT NULL, static_snapshot_identifier VARCHAR NOT NULL,
            service_date DATE NOT NULL, static_trip_id VARCHAR NOT NULL,
            static_route_id VARCHAR, direction_id INTEGER, event_type VARCHAR NOT NULL,
            eligible_event_count INTEGER NOT NULL, classified_event_count INTEGER NOT NULL,
            on_time_count INTEGER NOT NULL, on_time_ratio DOUBLE,
            maximum_lateness_seconds INTEGER, median_delay_seconds DOUBLE,
            p95_delay_seconds DOUBLE, first_stop_sequence INTEGER, last_stop_sequence INTEGER,
            start_delay_seconds INTEGER, end_delay_seconds INTEGER, delay_change_seconds INTEGER,
            any_very_late BOOLEAN NOT NULL, reported_cancellation BOOLEAN NOT NULL,
            coverage_status VARCHAR NOT NULL,
            PRIMARY KEY(reliability_run_id, static_snapshot_identifier, service_date,
                        static_trip_id, event_type))""",
        """CREATE TABLE IF NOT EXISTS gtfs_realtime_reliability_aggregate (
            reliability_run_id VARCHAR NOT NULL, aggregate_index INTEGER NOT NULL,
            dimension_type VARCHAR NOT NULL, service_date DATE, route_id VARCHAR,
            direction_id INTEGER, stop_id VARCHAR, event_type VARCHAR NOT NULL,
            eligible_event_count INTEGER NOT NULL, classified_event_count INTEGER NOT NULL,
            early_count INTEGER NOT NULL, on_time_count INTEGER NOT NULL,
            late_count INTEGER NOT NULL, very_late_count INTEGER NOT NULL,
            unclassified_count INTEGER NOT NULL, early_ratio DOUBLE, on_time_ratio DOUBLE,
            late_ratio DOUBLE, very_late_ratio DOUBLE, minimum_delay_seconds INTEGER,
            maximum_delay_seconds INTEGER, mean_delay_seconds DOUBLE,
            median_delay_seconds DOUBLE, p90_delay_seconds DOUBLE, p95_delay_seconds DOUBLE,
            eligible_trip_instance_count INTEGER NOT NULL,
            reported_cancellation_count INTEGER NOT NULL, interpretation_status VARCHAR NOT NULL,
            trip_matching_ratio DOUBLE, stop_matching_ratio DOUBLE,
            comparison_availability_ratio DOUBLE, classification_ratio DOUBLE,
            PRIMARY KEY(reliability_run_id, aggregate_index))""",
        """CREATE TABLE IF NOT EXISTS gtfs_realtime_reliability_finding (
            reliability_run_id VARCHAR NOT NULL, finding_index INTEGER NOT NULL,
            indicator_id VARCHAR NOT NULL, category VARCHAR NOT NULL, status VARCHAR NOT NULL,
            entity_index INTEGER, stop_time_update_index INTEGER, metric_value DOUBLE,
            threshold DOUBLE, numerator INTEGER, denominator INTEGER, unit VARCHAR,
            details VARCHAR NOT NULL, PRIMARY KEY(reliability_run_id, finding_index))""",
    )
    connection.execute("BEGIN TRANSACTION")
    try:
        for statement in statements:
            connection.execute(statement)
        connection.execute("COMMIT")
    except Exception:
        connection.execute("ROLLBACK")
        raise


def persist_reliability(connection: duckdb.DuckDBPyConnection, source: ReliabilitySource,
                        assessment: ReliabilityAssessment, config: ReliabilityConfiguration,
                        failure_hook: Callable[[str], None] | None = None) -> ReliabilityPersistenceResult:
    existing = connection.execute(
        """SELECT reliability_run_id, static_snapshot_identifier, matching_algorithm_version,
                  realtime_persistence_schema_version, source_payload_sha256
           FROM gtfs_realtime_reliability_run
           WHERE match_run_id = ? AND route_filter IS NOT DISTINCT FROM ?
             AND reliability_algorithm_version = ? AND reliability_config_schema_version = ?""",
        [source.lineage.match_run_id, source.route_filter, config.reliability_algorithm_version,
         config.schema_version],
    ).fetchone()
    if existing:
        expected = (source.lineage.static_snapshot_identifier, source.lineage.matching_algorithm_version,
                    source.lineage.realtime_persistence_schema_version, source.lineage.payload_sha256)
        if tuple(existing[1:]) != expected:
            raise ReliabilityError("Existing reliability run has incompatible source lineage.")
        return ReliabilityPersistenceResult(str(existing[0]), False)
    run_id = str(uuid.uuid4())
    coverage = assessment.coverage
    try:
        connection.execute("BEGIN TRANSACTION")
        connection.execute(
            "INSERT INTO gtfs_realtime_reliability_run VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [run_id, "MATCH_RUN", source.lineage.match_run_id, source.route_filter,
             source.lineage.static_snapshot_identifier, source.lineage.matching_algorithm_version,
             source.lineage.matching_config_schema_version, source.lineage.realtime_persistence_schema_version,
             source.lineage.payload_sha256, config.reliability_algorithm_version, config.schema_version,
             datetime.now(timezone.utc), assessment.overall_status, assessment.source_observation_count,
             len(assessment.events), sum(event.eligibility_status == "ELIGIBLE"
                                         for event in assessment.events),
             coverage.classified_event_count, len(assessment.findings),
             assessment.reported_cancellation_count, assessment.reported_cancellation_denominator,
             assessment.reported_cancellation_ratio],
        )
        for event_index, event in enumerate(assessment.events):
            item = event.observation
            connection.execute(
                "INSERT INTO gtfs_realtime_reliability_event VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [run_id, event_index, item.capture_uuid, item.match_run_id, item.entity_index,
                 item.stop_time_update_index, item.provider, item.static_snapshot_identifier,
                 item.service_date, item.static_trip_id, item.static_route_id, item.direction_id,
                 item.static_stop_sequence, item.static_stop_id, item.event_type, item.scheduled_utc,
                 item.scheduled_service_seconds, item.reported_delay_seconds,
                 item.calculated_delta_seconds, event.selected_delta_seconds,
                 event.selected_delta_source, item.consistency_difference_seconds,
                 event.eligibility_status, event.exclusion_reason, event.punctuality_classification,
                 event.candidate_observation_count, event.first_observed_at_utc,
                 event.selected_observed_at_utc, event.delta_changed_across_observations,
                 config.schema_version],
            )
            if failure_hook:
                failure_hook("after_event")
        for trip in assessment.trips:
            connection.execute(
                "INSERT INTO gtfs_realtime_reliability_trip VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [run_id, trip.static_snapshot_identifier, trip.service_date, trip.static_trip_id,
                 trip.static_route_id, trip.direction_id, trip.event_type, trip.eligible_event_count,
                 trip.classified_event_count, trip.on_time_count, trip.on_time_ratio,
                 trip.maximum_lateness_seconds, trip.median_delay_seconds, trip.p95_delay_seconds,
                 trip.first_stop_sequence, trip.last_stop_sequence, trip.start_delay_seconds,
                 trip.end_delay_seconds, trip.delay_change_seconds, trip.any_very_late,
                 trip.reported_cancellation, trip.coverage_status],
            )
            if failure_hook:
                failure_hook("after_trip")
        for index, aggregate in enumerate(assessment.aggregates):
            connection.execute(
                "INSERT INTO gtfs_realtime_reliability_aggregate VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [run_id, index, aggregate.dimension_type, aggregate.service_date, aggregate.route_id,
                 aggregate.direction_id, aggregate.stop_id, aggregate.event_type,
                 aggregate.eligible_event_count, aggregate.classified_event_count,
                 aggregate.early_count, aggregate.on_time_count, aggregate.late_count,
                 aggregate.very_late_count, aggregate.unclassified_count, aggregate.early_ratio,
                 aggregate.on_time_ratio, aggregate.late_ratio, aggregate.very_late_ratio,
                 aggregate.minimum_delay_seconds, aggregate.maximum_delay_seconds,
                 aggregate.mean_delay_seconds, aggregate.median_delay_seconds,
                 aggregate.p90_delay_seconds, aggregate.p95_delay_seconds,
                 aggregate.eligible_trip_instance_count, aggregate.reported_cancellation_count,
                 aggregate.interpretation_status, coverage.trip_matching_ratio,
                 coverage.stop_matching_ratio, coverage.comparison_availability_ratio,
                 coverage.classification_ratio],
            )
            if failure_hook:
                failure_hook("after_aggregate")
        for index, finding in enumerate(assessment.findings):
            connection.execute(
                "INSERT INTO gtfs_realtime_reliability_finding VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [run_id, index, finding.indicator_id, finding.category, finding.status,
                 finding.entity_index, finding.stop_time_update_index, finding.metric_value,
                 finding.threshold, finding.numerator, finding.denominator, finding.unit,
                 finding.details],
            )
            if failure_hook:
                failure_hook("after_finding")
        connection.execute("COMMIT")
    except Exception:
        connection.execute("ROLLBACK")
        raise
    return ReliabilityPersistenceResult(run_id, True)
