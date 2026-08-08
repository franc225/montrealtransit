from __future__ import annotations

import io
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import replace
from datetime import date, datetime, timezone
from pathlib import Path
from unittest.mock import patch

import duckdb


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from calculate_gtfs_realtime_reliability import build_argument_parser, run_cli  # noqa: E402
from gtfs_realtime_matching_repository import ensure_matching_schema  # noqa: E402
from gtfs_realtime_reliability import ReliabilityError, assess_reliability, load_reliability_config  # noqa: E402
from gtfs_realtime_reliability_repository import (  # noqa: E402
    ensure_reliability_schema, load_reliability_source, persist_reliability,
)


class GtfsRealtimeReliabilityPersistenceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / "config").mkdir()
        (self.root / "config/gtfs_realtime_reliability.json").write_bytes(
            (ROOT / "config/gtfs_realtime_reliability.json").read_bytes()
        )
        self.warehouse = self.root / "warehouse.duckdb"
        self.match_run_id = "match-run-1"
        connection = duckdb.connect(str(self.warehouse))
        connection.execute("""CREATE TABLE gtfs_realtime_capture (
            capture_uuid VARCHAR PRIMARY KEY, captured_at_utc TIMESTAMPTZ,
            provider VARCHAR)""")
        connection.execute("CREATE TABLE protected_static (value VARCHAR)")
        connection.execute("CREATE TABLE protected_realtime (value VARCHAR)")
        connection.execute("CREATE TABLE protected_quality (value VARCHAR)")
        for table in ("protected_static", "protected_realtime", "protected_quality"):
            connection.execute(f"INSERT INTO {table} VALUES ('unchanged')")
        ensure_matching_schema(connection)
        connection.execute("INSERT INTO gtfs_realtime_capture VALUES (?, ?, ?)",
                           ["capture-1", datetime(2026, 8, 3, 16, tzinfo=timezone.utc), "stm"])
        connection.execute(
            "INSERT INTO gtfs_realtime_match_run VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [self.match_run_id, "capture-1", "a" * 64, 2, "snapshot-1", "static-run-1",
             "feed-v1", "1.0", 1, datetime(2026, 8, 3, 16, tzinfo=timezone.utc),
             "COMPLETE", 2, 1, 1, 0, 0, 0, 0],
        )
        connection.execute(
            """INSERT INTO gtfs_realtime_trip_match (
                match_run_id, capture_uuid, entity_index, entity_id, entity_type,
                realtime_persistence_schema_version, schedule_relationship,
                schedule_relationship_name, relationship_treatment, resolved_service_date,
                service_date_source, service_date_candidate_count, static_snapshot_identifier,
                static_trip_id, static_route_id, static_direction_id, static_service_id,
                static_shape_id, match_status, match_method, candidate_count, conflict_code,
                details, realtime_trip_delay_seconds)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            [self.match_run_id, "capture-1", 0, "entity-0", "trip_update", 2, 0,
             "SCHEDULED", "SCHEDULED", date(2026, 8, 3), "EXPLICIT_START_DATE", 1,
             "snapshot-1", "trip-1", "route-1", 1, "service-1", "shape-1",
             "MATCHED", "EXACT_TRIP_ID_AND_SERVICE_DATE", 1, None, "matched", 20],
        )
        connection.execute(
            """INSERT INTO gtfs_realtime_trip_match (
                match_run_id, capture_uuid, entity_index, entity_id, entity_type,
                realtime_persistence_schema_version, schedule_relationship,
                schedule_relationship_name, relationship_treatment, resolved_service_date,
                service_date_source, service_date_candidate_count, static_snapshot_identifier,
                match_status, match_method, candidate_count, details)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            [self.match_run_id, "capture-1", 1, "entity-1", "vehicle_position", 2, 3,
             "CANCELED", "CANCELED", date(2026, 8, 3), "EXPLICIT_START_DATE", 1,
             "snapshot-1", "UNMATCHED", "NO_CANDIDATE", 0, "unmatched"],
        )
        for index, values in enumerate(((20, 25, -5), (None, 700, None))):
            connection.execute(
                """INSERT INTO gtfs_realtime_stop_time_match (
                    match_run_id, capture_uuid, entity_index, stop_time_update_index,
                    realtime_stop_sequence, realtime_stop_id, static_stop_sequence, static_stop_id,
                    scheduled_arrival_time, scheduled_arrival_service_seconds,
                    scheduled_arrival_day_offset, scheduled_arrival_local, scheduled_arrival_utc,
                    arrival_time_resolution_status, scheduled_departure_time,
                    scheduled_departure_service_seconds, scheduled_departure_day_offset,
                    scheduled_departure_local, scheduled_departure_utc,
                    departure_time_resolution_status, realtime_arrival_delay_seconds,
                    realtime_arrival_utc, calculated_arrival_delta_seconds, arrival_delta_source,
                    arrival_consistency_difference_seconds, realtime_departure_delay_seconds,
                    realtime_departure_utc, calculated_departure_delta_seconds,
                    departure_delta_source, departure_consistency_difference_seconds,
                    stop_schedule_relationship, stop_schedule_relationship_name,
                    match_status, match_method, conflict_code, details)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                [self.match_run_id, "capture-1", 0, index, index + 1, f"stop-{index + 1}",
                 index + 1, f"stop-{index + 1}", "12:00:00", 43200 + index * 60, 0,
                 datetime(2026, 8, 3, 12, tzinfo=timezone.utc),
                 datetime(2026, 8, 3, 16, tzinfo=timezone.utc), "RESOLVED",
                 "12:00:30", 43230 + index * 60, 0,
                 datetime(2026, 8, 3, 12, 0, 30, tzinfo=timezone.utc),
                 datetime(2026, 8, 3, 16, 0, 30, tzinfo=timezone.utc), "RESOLVED",
                 values[0], datetime(2026, 8, 3, 16, 0, 25, tzinfo=timezone.utc), values[1],
                 "ABSOLUTE_EVENT_TIME", values[2], 30,
                 datetime(2026, 8, 3, 16, 1, tzinfo=timezone.utc), 30,
                 "ABSOLUTE_EVENT_TIME", 0, 0, "SCHEDULED", "MATCHED", "STOP_SEQUENCE",
                 None, "matched"],
            )
        connection.close()
        self.environment = {"MONTREAL_TRANSIT_PROJECT_ROOT": str(self.root)}

    def tearDown(self) -> None:
        self.temp.cleanup()

    def assessment(self, connection: duckdb.DuckDBPyConnection):
        config = load_reliability_config(self.root)
        source = load_reliability_source(connection, self.match_run_id, config)
        return config, source, assess_reliability(source.lineage, source.observations,
                                                  source.relationships, config)

    def counts(self, connection: duckdb.DuckDBPyConnection) -> tuple[int, ...]:
        names = ("gtfs_realtime_reliability_run", "gtfs_realtime_reliability_event",
                 "gtfs_realtime_reliability_trip", "gtfs_realtime_reliability_aggregate",
                 "gtfs_realtime_reliability_finding")
        return tuple(connection.execute(f"SELECT count(*) FROM {name}").fetchone()[0]
                     for name in names)

    def test_source_lineage_events_relationships_and_route_filter(self) -> None:
        connection = duckdb.connect(str(self.warehouse))
        config = load_reliability_config(self.root)
        source = load_reliability_source(connection, self.match_run_id, config)
        self.assertEqual((source.lineage.realtime_persistence_schema_version,
                          source.lineage.matching_algorithm_version), (2, "1.0"))
        self.assertEqual([item.event_type for item in source.observations],
                         ["ARRIVAL", "DEPARTURE", "ARRIVAL", "DEPARTURE"])
        self.assertEqual(len(source.relationships), 2)
        filtered = load_reliability_source(connection, self.match_run_id, config, "missing")
        self.assertEqual((len(filtered.observations), len(filtered.relationships)), (0, 0))
        connection.close()

    def test_prerequisite_versions_and_unknown_run(self) -> None:
        connection = duckdb.connect(str(self.warehouse))
        config = load_reliability_config(self.root)
        for column, value, message in (("realtime_persistence_schema_version", 1, "incomplete"),
                                       ("matching_algorithm_version", "0.9", "algorithm version")):
            with self.subTest(column=column):
                connection.execute(f"UPDATE gtfs_realtime_match_run SET {column} = ?", [value])
                with self.assertRaisesRegex(ReliabilityError, message):
                    load_reliability_source(connection, self.match_run_id, config)
                connection.execute(f"UPDATE gtfs_realtime_match_run SET {column} = ?",
                                   [2 if column.startswith("realtime") else "1.0"])
        with self.assertRaisesRegex(ReliabilityError, "not found"):
            load_reliability_source(connection, "unknown", config)
        connection.close()

    def test_schema_persistence_lineage_and_protected_tables(self) -> None:
        connection = duckdb.connect(str(self.warehouse))
        before = tuple(connection.execute(f"SELECT * FROM {name}").fetchall()
                       for name in ("protected_static", "protected_realtime", "protected_quality",
                                    "gtfs_realtime_match_run"))
        config, source, assessment = self.assessment(connection)
        ensure_reliability_schema(connection)
        result = persist_reliability(connection, source, assessment, config)
        self.assertTrue(result.inserted)
        counts = self.counts(connection)
        self.assertEqual(counts[0], 1)
        self.assertEqual(counts[1], len(assessment.events))
        self.assertEqual(counts[2], len(assessment.trips))
        self.assertEqual(counts[3], len(assessment.aggregates))
        lineage = connection.execute("""SELECT match_run_id, static_snapshot_identifier,
            realtime_persistence_schema_version FROM gtfs_realtime_reliability_run""").fetchone()
        self.assertEqual(lineage, (self.match_run_id, "snapshot-1", 2))
        after = tuple(connection.execute(f"SELECT * FROM {name}").fetchall()
                      for name in ("protected_static", "protected_realtime", "protected_quality",
                                   "gtfs_realtime_match_run"))
        self.assertEqual(after, before)
        connection.close()

    def test_event_trip_aggregate_and_finding_fields(self) -> None:
        connection = duckdb.connect(str(self.warehouse))
        config, source, assessment = self.assessment(connection)
        ensure_reliability_schema(connection)
        persist_reliability(connection, source, assessment, config)
        event = connection.execute("""SELECT event_type, selected_delta_seconds,
            selected_delta_source, eligibility_status, punctuality_classification,
            threshold_config_schema_version FROM gtfs_realtime_reliability_event
            WHERE event_type = 'ARRIVAL' ORDER BY static_stop_sequence""").fetchone()
        self.assertEqual(event, ("ARRIVAL", 25, "ABSOLUTE_EVENT_TIME", "ELIGIBLE", "ON_TIME", 1))
        self.assertGreater(connection.execute("SELECT count(*) FROM gtfs_realtime_reliability_trip").fetchone()[0], 0)
        aggregate = connection.execute("""SELECT trip_matching_ratio, stop_matching_ratio,
            comparison_availability_ratio FROM gtfs_realtime_reliability_aggregate LIMIT 1""").fetchone()
        self.assertEqual(aggregate[0], .5)
        connection.close()

    def test_idempotency_and_incompatible_source(self) -> None:
        connection = duckdb.connect(str(self.warehouse))
        config, source, assessment = self.assessment(connection)
        ensure_reliability_schema(connection)
        first = persist_reliability(connection, source, assessment, config)
        before = self.counts(connection)
        second = persist_reliability(connection, source, assessment, config)
        self.assertTrue(first.inserted)
        self.assertFalse(second.inserted)
        self.assertEqual(self.counts(connection), before)
        incompatible = replace(source, lineage=replace(source.lineage, payload_sha256="f" * 64))
        with self.assertRaisesRegex(ReliabilityError, "incompatible source lineage"):
            persist_reliability(connection, incompatible, assessment, config)
        connection.close()

    def test_transaction_failures_leave_no_partial_rows(self) -> None:
        for stage in ("after_event", "after_trip", "after_aggregate", "after_finding"):
            with self.subTest(stage=stage):
                connection = duckdb.connect(str(self.warehouse))
                config, source, assessment = self.assessment(connection)
                if stage == "after_finding" and not assessment.findings:
                    observation = replace(source.observations[0], consistency_difference_seconds=100)
                    assessment = assess_reliability(source.lineage,
                                                    (observation,) + source.observations[1:],
                                                    source.relationships, config)
                ensure_reliability_schema(connection)
                def fail(current: str) -> None:
                    if current == stage:
                        raise RuntimeError(stage)
                with self.assertRaisesRegex(RuntimeError, stage):
                    persist_reliability(connection, source, assessment, config, fail)
                self.assertEqual(self.counts(connection), (0, 0, 0, 0, 0))
                connection.close()

    def test_previous_success_survives_later_failure(self) -> None:
        connection = duckdb.connect(str(self.warehouse))
        config, source, assessment = self.assessment(connection)
        ensure_reliability_schema(connection)
        persist_reliability(connection, source, assessment, config)
        before = self.counts(connection)
        other = replace(source, route_filter="route-1")
        with self.assertRaises(RuntimeError):
            persist_reliability(connection, other, assessment, config,
                                lambda stage: (_ for _ in ()).throw(RuntimeError("later"))
                                if stage == "after_event" else None)
        self.assertEqual(self.counts(connection), before)
        connection.close()

    def test_cli_help_no_persist_persist_repeat_errors_and_network_tripwire(self) -> None:
        with redirect_stdout(io.StringIO()):
            with self.assertRaises(SystemExit) as help_exit:
                build_argument_parser().parse_args(["--help"])
        self.assertEqual(help_exit.exception.code, 0)
        with patch.dict(os.environ, self.environment, clear=True), \
             patch("socket.create_connection", side_effect=AssertionError("network attempted")):
            no_persist = io.StringIO()
            with redirect_stdout(no_persist):
                self.assertEqual(run_cli(["--match-run-id", self.match_run_id,
                                          "--warehouse", str(self.warehouse), "--no-persist"]), 0)
            self.assertIn("Persistence: disabled", no_persist.getvalue())
            self.assertNotIn(str(self.root), no_persist.getvalue())
            first = io.StringIO()
            with redirect_stdout(first):
                self.assertEqual(run_cli(["--match-run-id", self.match_run_id,
                                          "--warehouse", str(self.warehouse)]), 0)
            self.assertIn("Persistence: inserted", first.getvalue())
            repeat = io.StringIO()
            with redirect_stdout(repeat):
                self.assertEqual(run_cli(["--match-run-id", self.match_run_id,
                                          "--warehouse", str(self.warehouse)]), 0)
            self.assertIn("already analyzed", repeat.getvalue())
            error = io.StringIO()
            with redirect_stderr(error):
                self.assertEqual(run_cli(["--match-run-id", "unknown",
                                          "--warehouse", str(self.warehouse)]), 1)
            self.assertIn("not found", error.getvalue())
            self.assertNotIn("STM_GTFS_REALTIME_API_KEY", error.getvalue())


if __name__ == "__main__":
    unittest.main()
