from __future__ import annotations

import io
import os
import socket
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
from gtfs_realtime_quality import assess_quality, load_quality_config  # noqa: E402
from ingest_gtfs_realtime import (  # noqa: E402
    IngestionError,
    create_realtime_tables,
    ingest_capture,
    persist_capture,
    run_cli,
    build_argument_parser,
)
from parse_gtfs_realtime import decode_feed, validate_capture  # noqa: E402
from tests import test_gtfs_realtime_parser as parser_tests  # noqa: E402


class GtfsRealtimeIngestionTest(unittest.TestCase):
    CAPTURE_UUID = parser_tests.GtfsRealtimeParserTest.CAPTURE_UUID
    CAPTURE_TIMESTAMP = parser_tests.GtfsRealtimeParserTest.CAPTURE_TIMESTAMP
    CAPTURED_AT = parser_tests.GtfsRealtimeParserTest.CAPTURED_AT

    def setUp(self) -> None:
        parser_tests.GtfsRealtimeParserTest.setUp(self)
        (self.project_root / "config" / "gtfs_realtime_quality.json").write_text(
            (REPOSITORY_ROOT / "config" / "gtfs_realtime_quality.json").read_text(encoding="utf-8"),
            encoding="utf-8",
        )
        self.warehouse = self.project_root / "warehouse" / "test.duckdb"
        self.warehouse.parent.mkdir()

    def tearDown(self) -> None:
        parser_tests.GtfsRealtimeParserTest.tearDown(self)

    feed = parser_tests.GtfsRealtimeParserTest.feed
    add_vehicle = parser_tests.GtfsRealtimeParserTest.add_vehicle
    add_trip_update = parser_tests.GtfsRealtimeParserTest.add_trip_update
    write_capture = parser_tests.GtfsRealtimeParserTest.write_capture

    def fixture(self, feed_type: str) -> tuple[object, object, object, Path, Path]:
        message = self.feed()
        if feed_type == "vehicle_positions":
            self.add_vehicle(message, "v-first")
            self.add_vehicle(message, "v-second")
        else:
            self.add_trip_update(message, "tu-first")
            self.add_trip_update(message, "tu-second")
        payload, metadata, _ = self.write_capture(message, feed_type)
        with patch.dict(os.environ, self.environment, clear=True):
            config = load_gtfs_realtime_config(validate_credentials=False)
            capture = validate_capture(config, payload, metadata)
            parsed = decode_feed(payload.read_bytes(), capture)
            assessment = assess_quality(parsed, load_quality_config(self.project_root))
        return capture, parsed, assessment, payload, metadata

    def counts(self, connection: duckdb.DuckDBPyConnection) -> dict[str, int]:
        tables = (
            "gtfs_realtime_capture", "gtfs_realtime_entity",
            "gtfs_realtime_vehicle_position", "gtfs_realtime_trip_update",
            "gtfs_realtime_stop_time_update", "gtfs_realtime_quality_run",
            "gtfs_realtime_quality_result",
        )
        return {table: connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0] for table in tables}

    def test_schema_creation_preserves_existing_static_table(self) -> None:
        connection = duckdb.connect(str(self.warehouse))
        connection.execute("CREATE TABLE dim_route (route_id VARCHAR)")
        connection.execute("INSERT INTO dim_route VALUES (?)", ["static-route"])
        create_realtime_tables(connection)
        create_realtime_tables(connection)
        self.assertEqual(connection.execute("SELECT * FROM dim_route").fetchall(), [("static-route",)])
        self.assertEqual(len(self.counts(connection)), 7)
        connection.close()

    def test_vehicle_capture_inventory_entities_timestamps_paths_and_quality(self) -> None:
        capture, feed, assessment, _, _ = self.fixture("vehicle_positions")
        connection = duckdb.connect(str(self.warehouse))
        create_realtime_tables(connection)
        result = persist_capture(connection, capture, feed, assessment, 1)
        self.assertTrue(result.inserted)
        counts = self.counts(connection)
        self.assertEqual((counts["gtfs_realtime_capture"], counts["gtfs_realtime_entity"], counts["gtfs_realtime_vehicle_position"]), (1, 2, 2))
        row = connection.execute("SELECT payload_relative_path, metadata_relative_path, feed_timestamp_unix, feed_timestamp_utc FROM gtfs_realtime_capture").fetchone()
        self.assertFalse(Path(row[0]).is_absolute())
        self.assertFalse(Path(row[1]).is_absolute())
        self.assertEqual(row[2], feed.header.timestamp)
        self.assertIsNotNone(row[3].tzinfo)
        order = connection.execute("SELECT entity_index, entity_id FROM gtfs_realtime_entity ORDER BY entity_index").fetchall()
        self.assertEqual(order, [(0, "v-first"), (1, "v-second")])
        self.assertEqual(counts["gtfs_realtime_quality_run"], 1)
        self.assertEqual(counts["gtfs_realtime_quality_result"], len(assessment.results))
        connection.close()

    def test_trip_updates_and_stop_time_update_order(self) -> None:
        capture, feed, assessment, _, _ = self.fixture("trip_updates")
        connection = duckdb.connect(str(self.warehouse))
        create_realtime_tables(connection)
        persist_capture(connection, capture, feed, assessment, 1)
        self.assertEqual(connection.execute("SELECT count(*) FROM gtfs_realtime_trip_update").fetchone()[0], 2)
        rows = connection.execute("SELECT entity_index, stop_time_update_index, stop_id, arrival_time_unix, arrival_time_utc FROM gtfs_realtime_stop_time_update ORDER BY entity_index, stop_time_update_index").fetchall()
        self.assertEqual([(row[0], row[1], row[2]) for row in rows], [(0, 0, "stop-1"), (0, 1, "stop-2"), (1, 0, "stop-1"), (1, 1, "stop-2")])
        self.assertIsInstance(rows[0][3], int)
        self.assertIsNotNone(rows[0][4].tzinfo)
        connection.close()

    def test_repeat_is_idempotent_and_hash_conflict_is_rejected(self) -> None:
        capture, feed, assessment, _, _ = self.fixture("vehicle_positions")
        connection = duckdb.connect(str(self.warehouse))
        create_realtime_tables(connection)
        first = persist_capture(connection, capture, feed, assessment, 1)
        before = self.counts(connection)
        second = persist_capture(connection, capture, feed, assessment, 1)
        self.assertTrue(first.inserted)
        self.assertFalse(second.inserted)
        self.assertEqual(self.counts(connection), before)
        with self.assertRaisesRegex(IngestionError, "different payload SHA-256"):
            persist_capture(connection, replace(capture, payload_sha256="f" * 64), feed, assessment, 1)
        self.assertEqual(self.counts(connection), before)
        connection.close()

    def test_transaction_rolls_back_entity_and_quality_failures(self) -> None:
        for stage in ("after_entities", "after_quality_results"):
            with self.subTest(stage=stage):
                capture, feed, assessment, _, _ = self.fixture("vehicle_positions")
                connection = duckdb.connect(":memory:")
                create_realtime_tables(connection)
                with self.assertRaisesRegex(RuntimeError, stage):
                    persist_capture(connection, capture, feed, assessment, 1,
                                    lambda current: (_ for _ in ()).throw(RuntimeError(stage)) if current == stage else None)
                self.assertTrue(all(count == 0 for count in self.counts(connection).values()))
                connection.close()

    def test_later_failure_preserves_earlier_capture(self) -> None:
        capture, feed, assessment, _, _ = self.fixture("vehicle_positions")
        connection = duckdb.connect(":memory:")
        create_realtime_tables(connection)
        persist_capture(connection, capture, feed, assessment, 1)
        before = self.counts(connection)
        other_capture = replace(capture, capture_uuid="87654321-4321-8765-4321-876543218765",
                                payload_sha256="e" * 64)
        other_feed = replace(feed, capture_uuid=other_capture.capture_uuid, payload_sha256=other_capture.payload_sha256)
        with self.assertRaises(RuntimeError):
            persist_capture(connection, other_capture, other_feed, assessment, 1,
                            lambda stage: (_ for _ in ()).throw(RuntimeError("synthetic")) if stage == "after_entities" else None)
        self.assertEqual(self.counts(connection), before)
        connection.close()

    def test_cli_help_analysis_persistence_secret_safety_and_raw_immutability(self) -> None:
        capture, feed, _, payload, metadata = self.fixture("vehicle_positions")
        payload_before, metadata_before = payload.read_bytes(), metadata.read_bytes()
        with patch.dict(os.environ, self.environment, clear=True):
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                self.assertEqual(run_cli(["--payload", str(payload), "--warehouse", str(self.warehouse)]), 0)
            output = stdout.getvalue()
            self.assertIn("Persistence: inserted", output)
            self.assertNotIn(str(self.project_root), output)
            second = io.StringIO()
            with redirect_stdout(second):
                self.assertEqual(run_cli(["--payload", str(payload), "--warehouse", str(self.warehouse)]), 0)
            self.assertIn("already ingested", second.getvalue())
            analysis_warehouse = self.project_root / "must-not-exist.duckdb"
            with redirect_stdout(io.StringIO()):
                self.assertEqual(run_cli(["--payload", str(payload), "--warehouse", str(analysis_warehouse), "--no-persist"]), 0)
            self.assertFalse(analysis_warehouse.exists())
        self.assertEqual((payload.read_bytes(), metadata.read_bytes()), (payload_before, metadata_before))

    def test_cli_help_needs_no_credentials_or_network(self) -> None:
        stdout = io.StringIO()
        with patch.dict(os.environ, {}, clear=True), redirect_stdout(stdout):
            with self.assertRaises(SystemExit) as context:
                build_argument_parser().parse_args(["--help"])
        self.assertEqual(context.exception.code, 0)
        self.assertIn("--no-persist", stdout.getvalue())


if __name__ == "__main__":
    unittest.main()
