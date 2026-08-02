from __future__ import annotations

import io
import os
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import duckdb


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SOURCE_DIRECTORY = REPOSITORY_ROOT / "src"
if str(SOURCE_DIRECTORY) not in sys.path:
    sys.path.insert(0, str(SOURCE_DIRECTORY))

from gtfs_realtime_config import load_gtfs_realtime_config  # noqa: E402
from gtfs_realtime_matching import MatchingError, assess_matches, load_matching_config  # noqa: E402
from gtfs_realtime_matching_repository import (  # noqa: E402
    ensure_matching_schema, load_matching_source, persist_assessment,
)
from gtfs_realtime_persistence import ensure_realtime_schema  # noqa: E402
from gtfs_realtime_quality import assess_quality, load_quality_config  # noqa: E402
from ingest_gtfs_realtime import persist_capture  # noqa: E402
from match_gtfs_realtime import build_argument_parser, run_cli  # noqa: E402
from parse_gtfs_realtime import decode_feed, validate_capture  # noqa: E402
from tests import test_gtfs_realtime_parser as parser_tests  # noqa: E402


class GtfsRealtimeMatchingPersistenceTest(unittest.TestCase):
    CAPTURE_UUID = parser_tests.GtfsRealtimeParserTest.CAPTURE_UUID
    CAPTURE_TIMESTAMP = parser_tests.GtfsRealtimeParserTest.CAPTURE_TIMESTAMP
    CAPTURED_AT = parser_tests.GtfsRealtimeParserTest.CAPTURED_AT

    def setUp(self) -> None:
        parser_tests.GtfsRealtimeParserTest.setUp(self)
        for name in ("gtfs_realtime_quality.json", "gtfs_realtime_matching.json"):
            (self.project_root / "config" / name).write_text(
                (REPOSITORY_ROOT / "config" / name).read_text(encoding="utf-8"), encoding="utf-8"
            )
        self.warehouse = self.project_root / "warehouse" / "matching.duckdb"
        self.warehouse.parent.mkdir()
        self._create_warehouse()

    def tearDown(self) -> None:
        parser_tests.GtfsRealtimeParserTest.tearDown(self)

    feed = parser_tests.GtfsRealtimeParserTest.feed
    add_trip_update = parser_tests.GtfsRealtimeParserTest.add_trip_update
    write_capture = parser_tests.GtfsRealtimeParserTest.write_capture

    def _create_warehouse(self) -> None:
        message = self.feed()
        self.add_trip_update(message)
        payload, metadata, _ = self.write_capture(message, "trip_updates")
        self.payload, self.metadata = payload, metadata
        with patch.dict(os.environ, self.environment, clear=True):
            config = load_gtfs_realtime_config(validate_credentials=False)
            capture = validate_capture(config, payload, metadata)
            feed = decode_feed(payload.read_bytes(), capture)
            quality = assess_quality(feed, load_quality_config(self.project_root))
        connection = duckdb.connect(str(self.warehouse))
        ensure_realtime_schema(connection)
        persist_capture(connection, capture, feed, quality, 1)
        connection.execute("CREATE TABLE dim_route (route_id VARCHAR)")
        connection.execute("INSERT INTO dim_route VALUES (?)", ["route-20"])
        connection.execute("""CREATE TABLE dim_trip (
            trip_id VARCHAR, route_id VARCHAR, service_id VARCHAR,
            trip_headsign VARCHAR, direction_id INTEGER, shape_id VARCHAR)""")
        connection.execute("INSERT INTO dim_trip VALUES (?, ?, ?, ?, ?, ?)",
                           ["trip-update", "route-20", "daily", None, None, "shape-20"])
        connection.execute("CREATE TABLE dim_stop (stop_id VARCHAR)")
        connection.execute("INSERT INTO dim_stop VALUES (?), (?)", ["stop-1", "stop-2"])
        connection.execute("""CREATE TABLE fct_scheduled_stop_time (
            trip_id VARCHAR, stop_id VARCHAR, stop_sequence INTEGER,
            arrival_time VARCHAR, departure_time VARCHAR,
            arrival_seconds INTEGER, departure_seconds INTEGER)""")
        connection.execute("INSERT INTO fct_scheduled_stop_time VALUES (?, ?, ?, ?, ?, ?, ?), (?, ?, ?, ?, ?, ?, ?)",
                           ["trip-update", "stop-1", 1, "12:30:00", "12:31:00", 45000, 45060,
                            "trip-update", "stop-2", 2, "25:30:00", "25:31:00", 91800, 91860])
        connection.execute("""CREATE TABLE dim_service (
            service_id VARCHAR, monday INTEGER, tuesday INTEGER, wednesday INTEGER,
            thursday INTEGER, friday INTEGER, saturday INTEGER, sunday INTEGER,
            start_date DATE, end_date DATE)""")
        connection.execute("INSERT INTO dim_service VALUES (?, 1, 1, 1, 1, 1, 1, 1, ?, ?)",
                           ["daily", "2026-08-01", "2026-08-31"])
        connection.execute("CREATE TABLE raw_calendar_dates (service_id VARCHAR, date VARCHAR, exception_type VARCHAR)")
        connection.execute("""CREATE TABLE meta_gtfs_feed (
            feed_publisher_name VARCHAR, feed_start_date VARCHAR, feed_end_date VARCHAR,
            feed_version VARCHAR, ingestion_run_id VARCHAR, ingested_at TIMESTAMPTZ)""")
        connection.execute("INSERT INTO meta_gtfs_feed VALUES (?, ?, ?, ?, ?, current_timestamp)",
                           ["Synthetic", "20260801", "20260831", "synthetic-v1", "static-run-1"])
        connection.close()

    def assessment(self, connection: duckdb.DuckDBPyConnection):
        config = load_matching_config(self.project_root)
        source = load_matching_source(connection, str(self.CAPTURE_UUID), config)
        assessment = assess_matches(source.capture_uuid, source.captured_at_utc, source.snapshot,
                                    source.entities, source.trips, source.calendars,
                                    source.exceptions, source.stop_times, config)
        return config, source, assessment

    def matching_counts(self, connection: duckdb.DuckDBPyConnection) -> tuple[int, int, int, int]:
        return tuple(connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0] for table in (
            "gtfs_realtime_match_run", "gtfs_realtime_trip_match",
            "gtfs_realtime_stop_time_match", "gtfs_realtime_match_finding"))

    def test_source_loading_snapshot_lineage_and_complete_schema(self) -> None:
        connection = duckdb.connect(str(self.warehouse))
        config, source, assessment = self.assessment(connection)
        self.assertEqual(source.persistence_schema_version, 2)
        self.assertEqual(source.snapshot.ingestion_run_id, "static-run-1")
        self.assertEqual(source.snapshot.feed_version, "synthetic-v1")
        self.assertEqual(source.entities[0].entity_index, 0)
        self.assertEqual(assessment.results[0].status, "MATCHED")
        connection.close()

    def test_incomplete_and_missing_schema_are_rejected_before_matching(self) -> None:
        for value in (None, 1):
            with self.subTest(value=value):
                connection = duckdb.connect(str(self.warehouse))
                connection.execute("UPDATE gtfs_realtime_capture SET persistence_schema_version = ?", [value])
                with self.assertRaisesRegex(MatchingError, "incomplete persistence lineage"):
                    load_matching_source(connection, str(self.CAPTURE_UUID), load_matching_config(self.project_root))
                ensure_matching_schema(connection)
                self.assertEqual(self.matching_counts(connection), (0, 0, 0, 0))
                connection.execute("UPDATE gtfs_realtime_capture SET persistence_schema_version = 2")
                connection.close()

    def test_schema_and_persistence_preserve_existing_layers(self) -> None:
        connection = duckdb.connect(str(self.warehouse))
        static_before = connection.execute("SELECT * FROM dim_trip").fetchall()
        realtime_before = connection.execute("SELECT count(*) FROM gtfs_realtime_entity").fetchone()[0]
        quality_before = connection.execute("SELECT count(*) FROM gtfs_realtime_quality_result").fetchone()[0]
        config, source, assessment = self.assessment(connection)
        ensure_matching_schema(connection)
        result = persist_assessment(connection, source, assessment, config)
        self.assertTrue(result.inserted)
        self.assertEqual(self.matching_counts(connection)[:3], (1, 1, 2))
        lineage = connection.execute("SELECT capture_uuid, realtime_persistence_schema_version, static_snapshot_identifier FROM gtfs_realtime_match_run").fetchone()
        self.assertEqual(lineage, (str(self.CAPTURE_UUID), 2, source.snapshot.snapshot_identifier))
        self.assertEqual(connection.execute("SELECT * FROM dim_trip").fetchall(), static_before)
        self.assertEqual(connection.execute("SELECT count(*) FROM gtfs_realtime_entity").fetchone()[0], realtime_before)
        self.assertEqual(connection.execute("SELECT count(*) FROM gtfs_realtime_quality_result").fetchone()[0], quality_before)
        connection.close()

    def test_stop_comparison_fields_and_order_are_persisted(self) -> None:
        connection = duckdb.connect(str(self.warehouse))
        config, source, assessment = self.assessment(connection)
        ensure_matching_schema(connection)
        persist_assessment(connection, source, assessment, config)
        rows = connection.execute("""SELECT stop_time_update_index, static_stop_id,
            scheduled_arrival_time, scheduled_arrival_service_seconds,
            scheduled_arrival_day_offset, realtime_arrival_delay_seconds,
            arrival_delta_source, match_status
            FROM gtfs_realtime_stop_time_match ORDER BY stop_time_update_index""").fetchall()
        self.assertEqual([row[0] for row in rows], [0, 1])
        self.assertEqual(rows[0][1:6], ("stop-1", "12:30:00", 45000, 0, 20))
        self.assertEqual(rows[1][2:5], ("25:30:00", 91800, 1))
        self.assertEqual(rows[0][6:], ("REPORTED_DELAY", "MATCHED"))
        connection.close()

    def test_idempotency_and_incompatible_lineage(self) -> None:
        connection = duckdb.connect(str(self.warehouse))
        config, source, assessment = self.assessment(connection)
        ensure_matching_schema(connection)
        first = persist_assessment(connection, source, assessment, config)
        before = self.matching_counts(connection)
        second = persist_assessment(connection, source, assessment, config)
        self.assertTrue(first.inserted)
        self.assertFalse(second.inserted)
        self.assertEqual(self.matching_counts(connection), before)
        with self.assertRaisesRegex(MatchingError, "incompatible source lineage"):
            persist_assessment(connection, replace(source, payload_sha256="f" * 64), assessment, config)
        connection.close()

    def test_transaction_failures_leave_no_partial_rows(self) -> None:
        for stage in ("after_trip_match", "after_stop_match", "after_finding"):
            with self.subTest(stage=stage):
                connection = duckdb.connect(str(self.warehouse))
                config, source, assessment = self.assessment(connection)
                if stage == "after_finding":
                    conflict = replace(assessment.results[0], conflict_code="SYNTHETIC", details="Synthetic finding")
                    assessment = replace(assessment, results=(conflict,))
                ensure_matching_schema(connection)
                def fail(current: str) -> None:
                    if current == stage:
                        raise RuntimeError(stage)
                with self.assertRaisesRegex(RuntimeError, stage):
                    persist_assessment(connection, source, assessment, config, fail)
                self.assertEqual(self.matching_counts(connection), (0, 0, 0, 0))
                connection.close()

    def test_previous_success_survives_later_failure(self) -> None:
        connection = duckdb.connect(str(self.warehouse))
        config, source, assessment = self.assessment(connection)
        ensure_matching_schema(connection)
        persist_assessment(connection, source, assessment, config)
        before = self.matching_counts(connection)
        other = replace(source, capture_uuid="other-capture", payload_sha256="e" * 64)
        other_assessment = replace(assessment, capture_uuid="other-capture")
        with self.assertRaises(RuntimeError):
            persist_assessment(connection, other, other_assessment, config,
                               lambda stage: (_ for _ in ()).throw(RuntimeError("later")) if stage == "after_trip_match" else None)
        self.assertEqual(self.matching_counts(connection), before)
        connection.close()

    def test_cli_help_no_persist_persisted_repeat_and_errors(self) -> None:
        with patch.dict(os.environ, {}, clear=True), redirect_stdout(io.StringIO()):
            with self.assertRaises(SystemExit) as help_exit:
                build_argument_parser().parse_args(["--help"])
        self.assertEqual(help_exit.exception.code, 0)
        with patch.dict(os.environ, self.environment, clear=True):
            output = io.StringIO()
            with redirect_stdout(output):
                self.assertEqual(run_cli(["--capture-uuid", str(self.CAPTURE_UUID),
                                          "--warehouse", str(self.warehouse), "--no-persist"]), 0)
            self.assertIn("Persistence: disabled", output.getvalue())
            self.assertNotIn(str(self.project_root), output.getvalue())
            first = io.StringIO()
            with redirect_stdout(first):
                self.assertEqual(run_cli(["--capture-uuid", str(self.CAPTURE_UUID),
                                          "--warehouse", str(self.warehouse)]), 0)
            self.assertIn("Persistence: inserted", first.getvalue())
            second = io.StringIO()
            with redirect_stdout(second):
                self.assertEqual(run_cli(["--capture-uuid", str(self.CAPTURE_UUID),
                                          "--warehouse", str(self.warehouse)]), 0)
            self.assertIn("already matched", second.getvalue())
            error = io.StringIO()
            with redirect_stderr(error):
                self.assertEqual(run_cli(["--capture-uuid", "unknown",
                                          "--warehouse", str(self.warehouse)]), 1)
            self.assertIn("not found", error.getvalue())


if __name__ == "__main__":
    unittest.main()
