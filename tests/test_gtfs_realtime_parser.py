from __future__ import annotations

import hashlib
import io
import json
import os
import socket
import sys
import tempfile
import unittest
import urllib.request
import uuid
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from google.transit import gtfs_realtime_pb2


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SOURCE_DIRECTORY = REPOSITORY_ROOT / "src"
if str(SOURCE_DIRECTORY) not in sys.path:
    sys.path.insert(0, str(SOURCE_DIRECTORY))

from parse_gtfs_realtime import (  # noqa: E402
    ParsedEntity,
    ParsedTripDescriptor,
    ParsedVehiclePosition,
    ParserError,
    _business_findings,
    build_argument_parser,
    parse_capture,
    run_cli,
)


class GtfsRealtimeParserTest(unittest.TestCase):
    CAPTURE_UUID = uuid.UUID("12345678-1234-5678-1234-567812345678")
    CAPTURE_TIMESTAMP = "20260802T123456Z"
    CAPTURED_AT = "2026-08-02T12:34:56Z"

    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.project_root = Path(self.temporary_directory.name)
        config_directory = self.project_root / "config"
        config_directory.mkdir()
        (config_directory / "gtfs_realtime.json").write_text(
            (REPOSITORY_ROOT / "config" / "gtfs_realtime.json").read_text(
                encoding="utf-8"
            ),
            encoding="utf-8",
        )
        self.environment = {"MONTREAL_TRANSIT_PROJECT_ROOT": str(self.project_root)}
        self.repository_raw_realtime_existed = (
            REPOSITORY_ROOT / "data" / "raw" / "gtfs_realtime"
        ).exists()
        self.network_patches = (
            patch(
                "urllib.request.urlopen",
                side_effect=AssertionError("Network access is forbidden."),
            ),
            patch(
                "urllib.request.build_opener",
                side_effect=AssertionError("Network access is forbidden."),
            ),
            patch.object(
                socket.socket,
                "connect",
                side_effect=AssertionError("Network access is forbidden."),
            ),
        )
        for network_patch in self.network_patches:
            network_patch.start()

    def tearDown(self) -> None:
        for network_patch in reversed(self.network_patches):
            network_patch.stop()
        self.temporary_directory.cleanup()

    def feed(self) -> gtfs_realtime_pb2.FeedMessage:
        feed = gtfs_realtime_pb2.FeedMessage()
        feed.header.gtfs_realtime_version = "2.0"
        feed.header.incrementality = gtfs_realtime_pb2.FeedHeader.FULL_DATASET
        feed.header.timestamp = 1_754_138_096
        return feed

    def add_vehicle(
        self,
        feed: gtfs_realtime_pb2.FeedMessage,
        entity_id: str = "vehicle-1",
    ) -> gtfs_realtime_pb2.FeedEntity:
        entity = feed.entity.add()
        entity.id = entity_id
        vehicle = entity.vehicle
        vehicle.trip.trip_id = "trip-vehicle"
        vehicle.trip.route_id = "route-10"
        vehicle.trip.direction_id = 1
        vehicle.trip.start_time = "12:30:00"
        vehicle.trip.start_date = "20260802"
        vehicle.trip.schedule_relationship = (
            gtfs_realtime_pb2.TripDescriptor.SCHEDULED
        )
        vehicle.vehicle.id = "bus-42"
        vehicle.vehicle.label = "Bus 42"
        vehicle.vehicle.license_plate = "SYNTHETIC"
        vehicle.position.latitude = 45.5017
        vehicle.position.longitude = -73.5673
        vehicle.position.bearing = 180.0
        vehicle.position.odometer = 1234.5
        vehicle.position.speed = 12.5
        vehicle.current_stop_sequence = 7
        vehicle.stop_id = "stop-7"
        vehicle.current_status = gtfs_realtime_pb2.VehiclePosition.STOPPED_AT
        vehicle.timestamp = 1_754_138_100
        vehicle.congestion_level = gtfs_realtime_pb2.VehiclePosition.RUNNING_SMOOTHLY
        vehicle.occupancy_status = gtfs_realtime_pb2.VehiclePosition.MANY_SEATS_AVAILABLE
        vehicle.occupancy_percentage = 25
        return entity

    def add_trip_update(
        self,
        feed: gtfs_realtime_pb2.FeedMessage,
        entity_id: str = "trip-update-1",
    ) -> gtfs_realtime_pb2.FeedEntity:
        entity = feed.entity.add()
        entity.id = entity_id
        update = entity.trip_update
        update.trip.trip_id = "trip-update"
        update.trip.route_id = "route-20"
        update.trip.start_date = "20260802"
        update.trip.schedule_relationship = gtfs_realtime_pb2.TripDescriptor.SCHEDULED
        update.vehicle.id = "bus-84"
        update.vehicle.label = "Bus 84"
        update.timestamp = 1_754_138_110
        update.delay = 30
        first = update.stop_time_update.add()
        first.stop_sequence = 1
        first.stop_id = "stop-1"
        first.arrival.delay = 20
        first.arrival.time = 1_754_138_120
        first.arrival.uncertainty = 5
        first.departure.delay = 30
        first.departure.time = 1_754_138_130
        first.schedule_relationship = gtfs_realtime_pb2.TripUpdate.StopTimeUpdate.SCHEDULED
        second = update.stop_time_update.add()
        second.stop_sequence = 2
        second.stop_id = "stop-2"
        second.schedule_relationship = gtfs_realtime_pb2.TripUpdate.StopTimeUpdate.SKIPPED
        return entity

    def write_capture(
        self,
        feed: gtfs_realtime_pb2.FeedMessage | bytes,
        feed_type: str,
        *,
        metadata_updates: dict[str, object] | None = None,
        partial: bool = False,
    ) -> tuple[Path, Path, dict[str, object]]:
        directory = (
            self.project_root
            / "data"
            / "raw"
            / "gtfs_realtime"
            / "stm"
            / feed_type
            / "2026"
            / "08"
            / "02"
        )
        directory.mkdir(parents=True, exist_ok=True)
        stem = f"{self.CAPTURE_TIMESTAMP}_{self.CAPTURE_UUID}"
        payload_path = directory / f"{stem}.pb"
        metadata_path = directory / f"{stem}.json"
        if isinstance(feed, bytes):
            payload = feed
        elif partial:
            payload = feed.SerializePartialToString()
        else:
            payload = feed.SerializeToString()
        payload_path.write_bytes(payload)
        metadata: dict[str, object] = {
            "schema_version": 1,
            "provider": "stm",
            "feed_type": feed_type,
            "http_status": 200,
            "response_content_type": "application/x-protobuf",
            "response_size_bytes": len(payload),
            "sha256": hashlib.sha256(payload).hexdigest(),
            "capture_uuid": str(self.CAPTURE_UUID),
            "captured_at_utc": self.CAPTURED_AT,
            "filename_timestamp_utc": self.CAPTURE_TIMESTAMP,
            "payload_relative_path": payload_path.relative_to(
                self.project_root
            ).as_posix(),
            "metadata_relative_path": metadata_path.relative_to(
                self.project_root
            ).as_posix(),
        }
        if metadata_updates:
            metadata.update(metadata_updates)
        metadata_path.write_text(json.dumps(metadata), encoding="utf-8")
        return payload_path, metadata_path, metadata

    def parse(
        self,
        feed: gtfs_realtime_pb2.FeedMessage,
        feed_type: str,
    ):
        payload_path, _, _ = self.write_capture(feed, feed_type)
        return parse_capture(payload_path, environment=self.environment)

    def test_official_binding_import_and_header_parsing(self) -> None:
        self.assertTrue(hasattr(gtfs_realtime_pb2, "FeedMessage"))
        feed = self.feed()
        self.add_vehicle(feed)
        parsed = self.parse(feed, "vehicle_positions")
        self.assertEqual(parsed.header.gtfs_realtime_version, "2.0")
        self.assertEqual(parsed.header.incrementality, 0)
        self.assertEqual(parsed.header.incrementality_name, "FULL_DATASET")
        self.assertEqual(parsed.header.timestamp, 1_754_138_096)
        self.assertEqual(parsed.header.timestamp_utc.tzinfo, timezone.utc)

        feed.header.ClearField("timestamp")
        parsed = self.parse(feed, "vehicle_positions")
        self.assertIsNone(parsed.header.timestamp)
        self.assertIsNone(parsed.header.timestamp_utc)

    def test_vehicle_position_normalization(self) -> None:
        feed = self.feed()
        self.add_vehicle(feed)
        parsed = self.parse(feed, "vehicle_positions")
        vehicle = parsed.entities[0].vehicle_position
        self.assertEqual(parsed.summary.vehicle_position_count, 1)
        self.assertEqual(vehicle.trip.trip_id, "trip-vehicle")
        self.assertEqual(vehicle.trip.direction_id, 1)
        self.assertEqual(vehicle.trip.schedule_relationship_name, "SCHEDULED")
        self.assertEqual(vehicle.vehicle.vehicle_id, "bus-42")
        self.assertAlmostEqual(vehicle.position.latitude, 45.5017, places=4)
        self.assertAlmostEqual(vehicle.position.longitude, -73.5673, places=4)
        self.assertEqual(vehicle.position.bearing, 180.0)
        self.assertEqual(vehicle.position.odometer, 1234.5)
        self.assertEqual(vehicle.position.speed, 12.5)
        self.assertEqual(vehicle.current_stop_sequence, 7)
        self.assertEqual(vehicle.stop_id, "stop-7")
        self.assertEqual(vehicle.current_status_name, "STOPPED_AT")
        self.assertEqual(vehicle.timestamp_utc.tzinfo, timezone.utc)
        self.assertEqual(vehicle.congestion_level_name, "RUNNING_SMOOTHLY")
        self.assertEqual(vehicle.occupancy_status_name, "MANY_SEATS_AVAILABLE")
        self.assertEqual(vehicle.occupancy_percentage, 25)

    def test_vehicle_missing_optional_fields_remain_none(self) -> None:
        feed = self.feed()
        entity = feed.entity.add()
        entity.id = "minimal"
        entity.vehicle.trip.trip_id = "minimal-trip"
        parsed = self.parse(feed, "vehicle_positions")
        vehicle = parsed.entities[0].vehicle_position
        self.assertIsNone(vehicle.vehicle)
        self.assertIsNone(vehicle.position)
        self.assertIsNone(vehicle.timestamp)
        self.assertIsNone(vehicle.stop_id)

    def test_trip_update_and_stop_event_normalization_preserves_order(self) -> None:
        feed = self.feed()
        self.add_trip_update(feed)
        parsed = self.parse(feed, "trip_updates")
        update = parsed.entities[0].trip_update
        self.assertEqual(parsed.summary.trip_update_count, 1)
        self.assertEqual(update.trip.trip_id, "trip-update")
        self.assertEqual(update.vehicle.vehicle_id, "bus-84")
        self.assertEqual(update.timestamp, 1_754_138_110)
        self.assertEqual(update.timestamp_utc.tzinfo, timezone.utc)
        self.assertEqual(update.delay, 30)
        self.assertEqual(
            [item.stop_id for item in update.stop_time_updates],
            ["stop-1", "stop-2"],
        )
        first = update.stop_time_updates[0]
        self.assertEqual(first.arrival.delay, 20)
        self.assertEqual(first.arrival.time, 1_754_138_120)
        self.assertEqual(first.arrival.time_utc.tzinfo, timezone.utc)
        self.assertEqual(first.arrival.uncertainty, 5)
        self.assertEqual(first.departure.delay, 30)
        self.assertEqual(first.schedule_relationship_name, "SCHEDULED")
        self.assertEqual(update.stop_time_updates[1].schedule_relationship_name, "SKIPPED")

    def test_mixed_unsupported_deleted_and_entity_order(self) -> None:
        feed = self.feed()
        self.add_vehicle(feed, "first")
        self.add_trip_update(feed, "second")
        unsupported = feed.entity.add()
        unsupported.id = "third"
        unsupported.alert.header_text.translation.add().text = "Synthetic alert"
        deleted = feed.entity.add()
        deleted.id = "fourth"
        deleted.is_deleted = True
        parsed = self.parse(feed, "vehicle_positions")
        self.assertEqual([entity.entity_id for entity in parsed.entities], ["first", "second", "third", "fourth"])
        self.assertEqual(parsed.summary.trip_update_count, 1)
        self.assertEqual(parsed.summary.unsupported_entity_count, 2)
        self.assertEqual(parsed.summary.deleted_entity_count, 1)
        self.assertIn("FEED_TYPE_MISMATCH", {finding.code for finding in parsed.findings})
        self.assertTrue(parsed.entities[3].is_deleted)

    def test_entity_and_business_identifier_validation(self) -> None:
        cases = []
        missing = self.feed()
        missing.entity.add().vehicle.trip.trip_id = "trip"
        cases.append((missing, "missing or blank ID"))
        blank = self.feed()
        blank.entity.add().id = " "
        cases.append((blank, "missing or blank ID"))
        duplicate = self.feed()
        self.add_vehicle(duplicate, "duplicate")
        self.add_vehicle(duplicate, "duplicate")
        cases.append((duplicate, "duplicated"))
        for feed, message in cases:
            with self.subTest(message=message):
                payload_path, _, _ = self.write_capture(feed, "vehicle_positions", partial=True)
                with self.assertRaisesRegex(ParserError, message):
                    parse_capture(payload_path, environment=self.environment)

        no_trip_id = self.feed()
        entity = no_trip_id.entity.add()
        entity.id = "no-trip-id"
        entity.vehicle.position.latitude = 45.0
        entity.vehicle.position.longitude = -73.0
        parsed = self.parse(no_trip_id, "vehicle_positions")
        self.assertEqual(parsed.summary.entities_missing_business_identifiers, 1)

    def test_entity_level_validation_findings(self) -> None:
        feed = self.feed()
        entity = self.add_vehicle(feed)
        entity.vehicle.position.latitude = 91
        entity.vehicle.position.longitude = -181
        entity.vehicle.trip.start_date = "2026-08-02"
        parsed = self.parse(feed, "vehicle_positions")
        codes = {finding.code for finding in parsed.findings}
        self.assertTrue({"INVALID_LATITUDE", "INVALID_LONGITUDE", "INVALID_START_DATE"}.issubset(codes))

        synthetic = ParsedEntity(
            original_index=0,
            entity_id="synthetic",
            is_deleted=False,
            entity_type="vehicle_position",
            vehicle_position=ParsedVehiclePosition(
                trip=ParsedTripDescriptor("trip", None, None, None, None, None, None),
                vehicle=None,
                position=None,
                current_stop_sequence=-1,
                stop_id=None,
                current_status=None,
                current_status_name=None,
                timestamp=None,
                timestamp_utc=None,
                congestion_level=None,
                congestion_level_name=None,
                occupancy_status=None,
                occupancy_status_name=None,
                occupancy_percentage=None,
            ),
            trip_update=None,
        )
        self.assertIn("INVALID_STOP_SEQUENCE", {item.code for item in _business_findings(synthetic)})

    def test_header_and_timestamp_failures(self) -> None:
        missing_version = self.feed()
        missing_version.header.ClearField("gtfs_realtime_version")
        self.add_vehicle(missing_version)
        payload_path, _, _ = self.write_capture(missing_version, "vehicle_positions", partial=True)
        with self.assertRaisesRegex(ParserError, "header version"):
            parse_capture(payload_path, environment=self.environment)

        unsupported = self.feed()
        unsupported.header.gtfs_realtime_version = "1.0"
        self.add_vehicle(unsupported)
        payload_path, _, _ = self.write_capture(unsupported, "vehicle_positions")
        with self.assertRaisesRegex(ParserError, "Unsupported GTFS-Realtime version"):
            parse_capture(payload_path, environment=self.environment)

        invalid_timestamp = self.feed()
        invalid_timestamp.header.timestamp = (2**64) - 1
        self.add_vehicle(invalid_timestamp)
        payload_path, _, _ = self.write_capture(invalid_timestamp, "vehicle_positions")
        with self.assertRaisesRegex(ParserError, "Feed timestamp"):
            parse_capture(payload_path, environment=self.environment)

        timestamp_cases = []
        vehicle_timestamp = self.feed()
        self.add_vehicle(vehicle_timestamp).vehicle.timestamp = (2**64) - 1
        timestamp_cases.append((vehicle_timestamp, "vehicle_positions", "Vehicle timestamp"))
        trip_timestamp = self.feed()
        self.add_trip_update(trip_timestamp).trip_update.timestamp = (2**64) - 1
        timestamp_cases.append((trip_timestamp, "trip_updates", "Trip Update timestamp"))
        event_timestamp = self.feed()
        self.add_trip_update(event_timestamp).trip_update.stop_time_update[0].arrival.time = (2**63) - 1
        timestamp_cases.append((event_timestamp, "trip_updates", "arrival event time"))
        for timestamp_feed, feed_type, message in timestamp_cases:
            with self.subTest(timestamp=message):
                payload_path, _, _ = self.write_capture(timestamp_feed, feed_type)
                with self.assertRaisesRegex(ParserError, message):
                    parse_capture(payload_path, environment=self.environment)

    def test_missing_required_protobuf_field_is_rejected(self) -> None:
        feed = self.feed()
        entity = self.add_vehicle(feed)
        entity.vehicle.position.ClearField("latitude")
        payload_path, _, _ = self.write_capture(
            feed, "vehicle_positions", partial=True
        )
        with self.assertRaisesRegex(ParserError, "required protobuf fields"):
            parse_capture(payload_path, environment=self.environment)

    def test_metadata_integrity_failures(self) -> None:
        feed = self.feed()
        self.add_vehicle(feed)
        mutations = (
            ({"schema_version": 2}, "schema version"),
            ({"provider": "other"}, "provider"),
            ({"feed_type": "alerts"}, "feed type"),
            ({"response_size_bytes": 1}, "payload size"),
            ({"sha256": "0" * 64}, "SHA-256"),
            ({"payload_relative_path": "data/raw/other.pb"}, "payload path"),
            ({"metadata_relative_path": "data/raw/other.json"}, "metadata path"),
            ({"http_status": 500}, "HTTP status"),
            ({"response_content_type": "text/html"}, "content type"),
            ({"capture_uuid": "not-a-uuid"}, "UUID"),
            ({"captured_at_utc": "not-a-date"}, "valid datetime"),
            ({"captured_at_utc": "2026-08-02T08:34:56-04:00"}, "must be UTC"),
            ({"filename_timestamp_utc": "20260802T000000Z"}, "filename timestamp"),
            ({"payload_relative_path": "../outside.pb"}, "safe relative path"),
        )
        for mutation, message in mutations:
            with self.subTest(mutation=mutation):
                payload_path, _, _ = self.write_capture(feed, "vehicle_positions", metadata_updates=mutation)
                with self.assertRaisesRegex(ParserError, message):
                    parse_capture(payload_path, environment=self.environment)

    def test_missing_files_filename_uuid_and_outside_paths(self) -> None:
        missing = self.project_root / "data" / "raw" / "gtfs_realtime" / "missing.pb"
        with self.assertRaisesRegex(ParserError, "payload file"):
            parse_capture(missing, environment=self.environment)

        feed = self.feed()
        self.add_vehicle(feed)
        payload_path, metadata_path, _ = self.write_capture(feed, "vehicle_positions")
        metadata_path.unlink()
        with self.assertRaisesRegex(ParserError, "metadata file"):
            parse_capture(payload_path, environment=self.environment)

        outside = self.project_root / "outside.pb"
        outside.write_bytes(b"synthetic")
        with self.assertRaisesRegex(ParserError, "outside"):
            parse_capture(outside, environment=self.environment)

        payload_path, metadata_path, metadata = self.write_capture(feed, "vehicle_positions")
        renamed = payload_path.with_name(f"{self.CAPTURE_TIMESTAMP}_{uuid.uuid4()}.pb")
        payload_path.rename(renamed)
        metadata["payload_relative_path"] = renamed.relative_to(self.project_root).as_posix()
        metadata_path.write_text(json.dumps(metadata), encoding="utf-8")
        with self.assertRaisesRegex(ParserError, "filename UUID"):
            parse_capture(renamed, metadata_path, environment=self.environment)

        payload_path, metadata_path, metadata = self.write_capture(
            feed, "vehicle_positions"
        )
        wrong_timestamp = payload_path.with_name(
            f"20260802T000000Z_{self.CAPTURE_UUID}.pb"
        )
        payload_path.rename(wrong_timestamp)
        metadata["payload_relative_path"] = wrong_timestamp.relative_to(
            self.project_root
        ).as_posix()
        metadata_path.write_text(json.dumps(metadata), encoding="utf-8")
        with self.assertRaisesRegex(ParserError, "filename timestamp"):
            parse_capture(
                wrong_timestamp, metadata_path, environment=self.environment
            )

    def test_empty_malformed_and_truncated_protobufs_are_secret_safe(self) -> None:
        secret = "test-api-key-not-a-real-secret"
        feed = self.feed()
        self.add_vehicle(feed)
        valid = feed.SerializeToString()
        payloads = (b"", b"\x80", valid[:-1])
        for payload in payloads:
            with self.subTest(length=len(payload)):
                payload_path, _, _ = self.write_capture(payload, "vehicle_positions")
                with self.assertRaises(ParserError) as context:
                    parse_capture(payload_path, environment=self.environment)
                message = str(context.exception)
                self.assertNotIn(secret, message)
                self.assertNotIn("gtfs_realtime_version:", message)

    def test_cli_summary_for_both_feeds_is_concise_and_read_only(self) -> None:
        cases = []
        vehicle_feed = self.feed()
        self.add_vehicle(vehicle_feed)
        cases.append((vehicle_feed, "vehicle_positions", "Vehicle Positions: 1"))
        trip_feed = self.feed()
        self.add_trip_update(trip_feed)
        cases.append((trip_feed, "trip_updates", "Trip Updates: 1"))
        for feed, feed_type, expected in cases:
            with self.subTest(feed_type=feed_type):
                payload_path, metadata_path, _ = self.write_capture(feed, feed_type)
                payload_before = payload_path.read_bytes()
                metadata_before = metadata_path.read_bytes()
                stdout = io.StringIO()
                stderr = io.StringIO()
                with redirect_stdout(stdout), redirect_stderr(stderr):
                    exit_code = run_cli(
                        ["--payload", str(payload_path), "--summary"],
                        environment=self.environment,
                    )
                output = stdout.getvalue()
                self.assertEqual(exit_code, 0)
                self.assertEqual(stderr.getvalue(), "")
                self.assertIn(expected, output)
                self.assertLess(len(output.splitlines()), 20)
                self.assertNotIn("entity_id", output)
                self.assertNotIn(str(self.project_root), output)
                self.assertEqual(payload_path.read_bytes(), payload_before)
                self.assertEqual(metadata_path.read_bytes(), metadata_before)
                self.assertFalse((self.project_root / "data" / "parsed").exists())

    def test_cli_help_and_errors_require_no_credentials_or_network(self) -> None:
        with redirect_stdout(io.StringIO()), self.assertRaises(SystemExit) as context:
            build_argument_parser().parse_args(["--help"])
        self.assertEqual(context.exception.code, 0)

        stdout = io.StringIO()
        stderr = io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            exit_code = run_cli(
                ["--payload", str(self.project_root / "missing.pb")],
                environment=self.environment,
            )
        self.assertEqual(exit_code, 1)
        self.assertEqual(stdout.getvalue(), "")
        self.assertNotIn("test-api-key", stderr.getvalue())

    def test_repository_static_outputs_are_unchanged(self) -> None:
        warehouse = self.project_root / "data" / "warehouse" / "montreal_transit.duckdb"
        report = self.project_root / "docs" / "index.html"
        warehouse.parent.mkdir(parents=True)
        report.parent.mkdir(parents=True)
        warehouse.write_bytes(b"warehouse marker")
        report.write_text("report marker", encoding="utf-8")
        feed = self.feed()
        self.add_vehicle(feed)
        self.parse(feed, "vehicle_positions")
        self.assertEqual(warehouse.read_bytes(), b"warehouse marker")
        self.assertEqual(report.read_text(encoding="utf-8"), "report marker")
        self.assertEqual(
            (REPOSITORY_ROOT / "data" / "raw" / "gtfs_realtime").exists(),
            self.repository_raw_realtime_existed,
        )


if __name__ == "__main__":
    unittest.main()
