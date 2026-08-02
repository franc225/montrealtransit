from __future__ import annotations

import os
import io
import sys
import unittest
from contextlib import redirect_stderr
from datetime import timezone
from pathlib import Path
from unittest.mock import patch

import duckdb


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SOURCE_DIRECTORY = REPOSITORY_ROOT / "src"
if str(SOURCE_DIRECTORY) not in sys.path:
    sys.path.insert(0, str(SOURCE_DIRECTORY))

from gtfs_realtime_config import load_gtfs_realtime_config  # noqa: E402
from gtfs_realtime_persistence import (  # noqa: E402
    BASE_TABLES,
    PARSER_MODEL_SCHEMA_VERSION,
    PERSISTENCE_SCHEMA_VERSION,
    ensure_realtime_schema,
)
from gtfs_realtime_quality import assess_quality, load_quality_config  # noqa: E402
from ingest_gtfs_realtime import IngestionError, persist_capture, run_cli  # noqa: E402
from parse_gtfs_realtime import decode_feed, validate_capture  # noqa: E402
from tests import test_gtfs_realtime_parser as parser_tests  # noqa: E402


class GtfsRealtimePersistenceTest(unittest.TestCase):
    CAPTURE_UUID = parser_tests.GtfsRealtimeParserTest.CAPTURE_UUID
    CAPTURE_TIMESTAMP = parser_tests.GtfsRealtimeParserTest.CAPTURE_TIMESTAMP
    CAPTURED_AT = parser_tests.GtfsRealtimeParserTest.CAPTURED_AT

    def setUp(self) -> None:
        parser_tests.GtfsRealtimeParserTest.setUp(self)
        (self.project_root / "config" / "gtfs_realtime_quality.json").write_text(
            (REPOSITORY_ROOT / "config" / "gtfs_realtime_quality.json").read_text(encoding="utf-8"),
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        parser_tests.GtfsRealtimeParserTest.tearDown(self)

    feed = parser_tests.GtfsRealtimeParserTest.feed
    add_vehicle = parser_tests.GtfsRealtimeParserTest.add_vehicle
    add_trip_update = parser_tests.GtfsRealtimeParserTest.add_trip_update
    write_capture = parser_tests.GtfsRealtimeParserTest.write_capture

    def parsed(self, feed_type: str, configure: object) -> tuple[object, object, object]:
        message = self.feed()
        configure(message)
        payload, metadata, _ = self.write_capture(message, feed_type)
        with patch.dict(os.environ, self.environment, clear=True):
            config = load_gtfs_realtime_config(validate_credentials=False)
            capture = validate_capture(config, payload, metadata)
            parsed = decode_feed(payload.read_bytes(), capture)
            assessment = assess_quality(parsed, load_quality_config(self.project_root))
        return capture, parsed, assessment

    def persist(self, feed_type: str, configure: object) -> tuple[duckdb.DuckDBPyConnection, object, object]:
        capture, feed, assessment = self.parsed(feed_type, configure)
        connection = duckdb.connect(":memory:")
        ensure_realtime_schema(connection)
        persist_capture(connection, capture, feed, assessment, 1)
        return connection, capture, feed

    def test_complete_vehicle_position_fields_and_lineage(self) -> None:
        connection, capture, feed = self.persist("vehicle_positions", lambda message: self.add_vehicle(message))
        row = connection.execute(
            """SELECT trip_id, route_id, direction_id, start_time, start_date,
                      trip_schedule_relationship, trip_schedule_relationship_name,
                      vehicle_id, vehicle_label, vehicle_license_plate,
                      latitude, longitude, bearing, odometer, speed,
                      current_stop_sequence, stop_id, current_status, current_status_name,
                      timestamp_unix, timestamp_utc, congestion_level, congestion_level_name,
                      occupancy_status, occupancy_status_name, occupancy_percentage,
                      parser_model_schema_version, persistence_schema_version
               FROM gtfs_realtime_vehicle_position"""
        ).fetchone()
        self.assertEqual(row[:10], ("trip-vehicle", "route-10", 1, "12:30:00", "20260802", 0, "SCHEDULED", "bus-42", "Bus 42", "SYNTHETIC"))
        self.assertAlmostEqual(row[10], 45.5017, places=4)
        self.assertAlmostEqual(row[11], -73.5673, places=4)
        self.assertEqual(row[12:16], (180.0, 1234.5, 12.5, 7))
        self.assertEqual(row[16:20], ("stop-7", 1, "STOPPED_AT", 1_754_138_100))
        self.assertIsNotNone(row[20].tzinfo)
        self.assertEqual(row[21:28], (1, "RUNNING_SMOOTHLY", 1, "MANY_SEATS_AVAILABLE", 25, 1, 2))
        entity = connection.execute("SELECT entity_index, parser_model_schema_version, persistence_schema_version FROM gtfs_realtime_entity").fetchone()
        self.assertEqual(entity, (0, PARSER_MODEL_SCHEMA_VERSION, PERSISTENCE_SCHEMA_VERSION))
        inventory = connection.execute("SELECT gtfs_realtime_version, incrementality_name, vehicle_position_count, parser_model_schema_version, sha256_verified, persistence_schema_version FROM gtfs_realtime_capture").fetchone()
        self.assertEqual(inventory, ("2.0", "FULL_DATASET", 1, 1, True, 2))
        connection.close()

    def test_complete_trip_update_stop_events_and_order(self) -> None:
        def configure(message: object) -> None:
            entity = self.add_trip_update(message)
            entity.trip_update.trip.start_time = "12:45:00"
            entity.trip_update.trip.direction_id = 0
            entity.trip_update.vehicle.license_plate = "SYNTHETIC-TU"
            first = entity.trip_update.stop_time_update[0]
            first.arrival.uncertainty = 4
            first.arrival.scheduled_time = 1_754_138_115
            first.departure.uncertainty = 6
            first.departure.scheduled_time = 1_754_138_125
        connection, _, _ = self.persist("trip_updates", configure)
        trip = connection.execute(
            """SELECT trip_id, route_id, direction_id, start_time, start_date,
                      schedule_relationship, schedule_relationship_name, vehicle_id,
                      vehicle_label, vehicle_license_plate, timestamp_unix, timestamp_utc,
                      trip_delay_seconds, parser_model_schema_version, persistence_schema_version
               FROM gtfs_realtime_trip_update"""
        ).fetchone()
        self.assertEqual(trip[:11], ("trip-update", "route-20", 0, "12:45:00", "20260802", 0, "SCHEDULED", "bus-84", "Bus 84", "SYNTHETIC-TU", 1_754_138_110))
        self.assertIsNotNone(trip[11].tzinfo)
        self.assertEqual(trip[12:], (30, 1, 2))
        stops = connection.execute(
            """SELECT stop_time_update_index, stop_sequence, stop_id, schedule_relationship,
                      schedule_relationship_name, arrival_delay_seconds, arrival_time_unix,
                      arrival_time_utc, arrival_uncertainty, arrival_scheduled_time_unix,
                      arrival_scheduled_time_utc, departure_delay_seconds, departure_time_unix,
                      departure_time_utc, departure_uncertainty, departure_scheduled_time_unix,
                      departure_scheduled_time_utc, parser_model_schema_version,
                      persistence_schema_version
               FROM gtfs_realtime_stop_time_update ORDER BY stop_time_update_index"""
        ).fetchall()
        self.assertEqual([(row[0], row[1], row[2]) for row in stops], [(0, 1, "stop-1"), (1, 2, "stop-2")])
        self.assertEqual(stops[0][3:7], (0, "SCHEDULED", 20, 1_754_138_120))
        self.assertIsNotNone(stops[0][7].tzinfo)
        self.assertEqual(stops[0][8:10], (4, 1_754_138_115))
        self.assertIsNotNone(stops[0][10].tzinfo)
        self.assertEqual(stops[0][11:13], (30, 1_754_138_130))
        self.assertIsNotNone(stops[0][13].tzinfo)
        self.assertEqual(stops[0][14:16], (6, 1_754_138_125))
        self.assertIsNotNone(stops[0][16].tzinfo)
        self.assertEqual(stops[0][17:], (1, 2))
        connection.close()

    def test_absent_optional_values_remain_null_and_explicit_zero_survives(self) -> None:
        def configure(message: object) -> None:
            entity = self.add_vehicle(message)
            entity.vehicle.ClearField("trip")
            entity.vehicle.ClearField("vehicle")
            entity.vehicle.ClearField("position")
            entity.vehicle.ClearField("timestamp")
            entity.vehicle.ClearField("current_status")
            entity.vehicle.ClearField("congestion_level")
            entity.vehicle.ClearField("occupancy_status")
            entity.vehicle.current_stop_sequence = 0
            entity.vehicle.occupancy_percentage = 0
        connection, _, _ = self.persist("vehicle_positions", configure)
        row = connection.execute(
            """SELECT trip_id, trip_schedule_relationship, vehicle_id, latitude,
                      timestamp_unix, current_status, congestion_level, occupancy_status,
                      current_stop_sequence, occupancy_percentage
               FROM gtfs_realtime_vehicle_position"""
        ).fetchone()
        self.assertEqual(row[:8], (None,) * 8)
        self.assertEqual(row[8:], (0, 0))
        connection.close()

    def test_parser_findings_are_ordered_and_persisted(self) -> None:
        def configure(message: object) -> None:
            entity = self.add_vehicle(message)
            entity.vehicle.trip.ClearField("trip_id")
        connection, _, _ = self.persist("vehicle_positions", configure)
        findings = connection.execute(
            "SELECT finding_index, finding_code, entity_index, parser_model_schema_version, persistence_schema_version FROM gtfs_realtime_parser_finding ORDER BY finding_index"
        ).fetchall()
        self.assertTrue(findings)
        self.assertEqual(findings[0], (0, "MISSING_TRIP_ID", 0, 1, 2))
        connection.close()

    def test_additive_migration_is_repeatable_and_preserves_old_rows(self) -> None:
        connection = duckdb.connect(":memory:")
        connection.execute("CREATE TABLE dim_trip (trip_id VARCHAR)")
        connection.execute("INSERT INTO dim_trip VALUES (?)", ["static-trip"])
        for statement in BASE_TABLES:
            connection.execute(statement)
        connection.execute(
            """INSERT INTO gtfs_realtime_capture VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            ["old-capture", "stm", "vehicle_positions", "2026-08-02T12:00:00+00:00",
             "20260802T120000Z", 1, "2026-08-02T12:00:00+00:00", "2.0", 0,
             "data/raw/old.pb", "data/raw/old.json", 1, "a" * 64, 0, 0, 0, 0,
             "2026-08-02T12:00:01+00:00", 1],
        )
        connection.execute(
            "INSERT INTO gtfs_realtime_quality_run VALUES (?, ?, ?, ?, ?, ?, ?)",
            ["old-quality", "old-capture", "2026-08-02T12:00:02+00:00", 1, "PASS", 0, 0],
        )
        ensure_realtime_schema(connection)
        ensure_realtime_schema(connection)
        old = connection.execute("SELECT parser_schema_version, persistence_schema_version FROM gtfs_realtime_capture").fetchone()
        self.assertEqual(old, (1, None))
        self.assertEqual(connection.execute("SELECT * FROM dim_trip").fetchall(), [("static-trip",)])
        self.assertEqual(connection.execute("SELECT count(*) FROM gtfs_realtime_quality_run").fetchone()[0], 1)
        nullable = connection.execute(
            "SELECT is_nullable FROM information_schema.columns WHERE table_name = ? AND column_name = ?",
            ["gtfs_realtime_vehicle_position", "odometer"],
        ).fetchone()[0]
        self.assertEqual(nullable, "YES")
        connection.close()

    def test_old_schema_capture_is_not_implicitly_reingested(self) -> None:
        capture, feed, assessment = self.parsed("vehicle_positions", lambda message: self.add_vehicle(message))
        connection = duckdb.connect(":memory:")
        ensure_realtime_schema(connection)
        persist_capture(connection, capture, feed, assessment, 1)
        connection.execute("UPDATE gtfs_realtime_capture SET persistence_schema_version = NULL")
        before = connection.execute("SELECT count(*) FROM gtfs_realtime_entity").fetchone()[0]
        with self.assertRaisesRegex(IngestionError, "older incomplete persistence schema"):
            persist_capture(connection, capture, feed, assessment, 1)
        self.assertEqual(connection.execute("SELECT count(*) FROM gtfs_realtime_entity").fetchone()[0], before)
        connection.close()

    def test_cli_reports_older_schema_without_destructive_upgrade(self) -> None:
        capture, feed, assessment = self.parsed("vehicle_positions", lambda message: self.add_vehicle(message))
        warehouse = self.project_root / "warehouse" / "old.duckdb"
        warehouse.parent.mkdir()
        connection = duckdb.connect(str(warehouse))
        ensure_realtime_schema(connection)
        persist_capture(connection, capture, feed, assessment, 1)
        connection.execute("UPDATE gtfs_realtime_capture SET persistence_schema_version = NULL")
        connection.close()
        stderr = io.StringIO()
        with patch.dict(os.environ, self.environment, clear=True), redirect_stderr(stderr):
            result = run_cli(["--payload", str(capture.payload_path), "--warehouse", str(warehouse)])
        self.assertEqual(result, 1)
        self.assertIn("older incomplete persistence schema", stderr.getvalue())
        connection = duckdb.connect(str(warehouse), read_only=True)
        self.assertEqual(connection.execute("SELECT count(*) FROM gtfs_realtime_capture").fetchone()[0], 1)
        connection.close()

    def test_subtype_failures_roll_back_complete_ingestion(self) -> None:
        cases = (
            ("vehicle_positions", lambda message: self.add_vehicle(message), "after_vehicle_position"),
            ("trip_updates", lambda message: self.add_trip_update(message), "after_trip_update"),
            ("trip_updates", lambda message: self.add_trip_update(message), "after_stop_time_update"),
        )
        for feed_type, configure, failure_stage in cases:
            with self.subTest(stage=failure_stage):
                capture, feed, assessment = self.parsed(feed_type, configure)
                connection = duckdb.connect(":memory:")
                ensure_realtime_schema(connection)
                def fail(stage: str) -> None:
                    if stage == failure_stage:
                        raise RuntimeError(failure_stage)
                with self.assertRaisesRegex(RuntimeError, failure_stage):
                    persist_capture(connection, capture, feed, assessment, 1, fail)
                for table in (
                    "gtfs_realtime_capture", "gtfs_realtime_entity",
                    "gtfs_realtime_vehicle_position", "gtfs_realtime_trip_update",
                    "gtfs_realtime_stop_time_update", "gtfs_realtime_parser_finding",
                    "gtfs_realtime_quality_run", "gtfs_realtime_quality_result",
                ):
                    self.assertEqual(connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0], 0)
                connection.close()


if __name__ == "__main__":
    unittest.main()
