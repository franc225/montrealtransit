from __future__ import annotations

import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SOURCE_DIRECTORY = REPOSITORY_ROOT / "src"
if str(SOURCE_DIRECTORY) not in sys.path:
    sys.path.insert(0, str(SOURCE_DIRECTORY))

from gtfs_realtime_quality import (  # noqa: E402
    PreviousCapture,
    assess_quality,
    calculate_completeness,
    load_quality_config,
)
from parse_gtfs_realtime import parse_capture  # noqa: E402
from tests import test_gtfs_realtime_parser as parser_tests  # noqa: E402


class GtfsRealtimeQualityTest(unittest.TestCase):
    CAPTURE_UUID = parser_tests.GtfsRealtimeParserTest.CAPTURE_UUID
    CAPTURE_TIMESTAMP = parser_tests.GtfsRealtimeParserTest.CAPTURE_TIMESTAMP
    CAPTURED_AT = parser_tests.GtfsRealtimeParserTest.CAPTURED_AT

    def setUp(self) -> None:
        parser_tests.GtfsRealtimeParserTest.setUp(self)
        quality_path = self.project_root / "config" / "gtfs_realtime_quality.json"
        quality_path.write_text(
            (REPOSITORY_ROOT / "config" / "gtfs_realtime_quality.json").read_text(encoding="utf-8"),
            encoding="utf-8",
        )
        self.config = load_quality_config(self.project_root)

    def tearDown(self) -> None:
        parser_tests.GtfsRealtimeParserTest.tearDown(self)

    feed = parser_tests.GtfsRealtimeParserTest.feed
    add_vehicle = parser_tests.GtfsRealtimeParserTest.add_vehicle
    add_trip_update = parser_tests.GtfsRealtimeParserTest.add_trip_update
    write_capture = parser_tests.GtfsRealtimeParserTest.write_capture

    def parsed(self, feed_type: str, configure: object) -> object:
        message = self.feed()
        captured = datetime.fromisoformat(self.CAPTURED_AT.replace("Z", "+00:00"))
        message.header.timestamp = int(captured.timestamp())
        configure(message, captured)
        payload, metadata, _ = self.write_capture(message, feed_type)
        with patch.dict("os.environ", self.environment, clear=True):
            return parse_capture(payload, metadata)

    def result(self, assessment: object, rule_id: str) -> object:
        return next(result for result in assessment.results if result.rule_id == rule_id)

    def test_feed_freshness_thresholds_and_archived_observation_time(self) -> None:
        for age, expected in ((0, "PASS"), (30, "PASS"), (31, "WARN"), (-5, "PASS"), (-6, "FAIL")):
            with self.subTest(age=age):
                feed = self.parsed("vehicle_positions", lambda message, captured: (
                    setattr(message.header, "timestamp", int((captured - timedelta(seconds=age)).timestamp())),
                    self.add_vehicle(message),
                    setattr(message.entity[0].vehicle, "timestamp", int(captured.timestamp())),
                ))
                assessment = assess_quality(feed, self.config)
                self.assertEqual(self.result(assessment, "RTF001").status, expected)
        self.assertEqual(feed.captured_at_utc.year, 2026)

    def test_entity_age_statistics_timestamp_ratio_and_future_skew(self) -> None:
        ages = (10, 20, 30, 40, 100, -6)
        def configure(message: object, captured: datetime) -> None:
            for index, age in enumerate(ages):
                entity = self.add_vehicle(message, f"v-{index}")
                entity.vehicle.timestamp = int((captured - timedelta(seconds=age)).timestamp())
            entity = self.add_vehicle(message, "missing-time")
            entity.vehicle.ClearField("timestamp")
        feed = self.parsed("vehicle_positions", configure)
        metrics = assess_quality(feed, self.config).freshness
        self.assertEqual(metrics.minimum_entity_age_seconds, -6)
        self.assertEqual(metrics.maximum_entity_age_seconds, 100)
        self.assertAlmostEqual(metrics.mean_entity_age_seconds, 194 / 6)
        self.assertEqual(metrics.median_entity_age_seconds, 25)
        self.assertEqual(metrics.p95_entity_age_seconds, 100)
        self.assertEqual((metrics.timestamped_entity_count, metrics.eligible_entity_count), (6, 7))
        self.assertEqual(metrics.timestamped_entity_ratio, 6 / 7)
        self.assertEqual((metrics.future_dated_timestamp_count, metrics.maximum_future_skew_seconds), (1, 6))

    def test_trip_update_ages_and_missing_timestamps(self) -> None:
        def configure(message: object, captured: datetime) -> None:
            first = self.add_trip_update(message, "tu-1")
            first.trip_update.timestamp = int((captured - timedelta(seconds=12)).timestamp())
            second = self.add_trip_update(message, "tu-2")
            second.trip_update.ClearField("timestamp")
        metrics = assess_quality(self.parsed("trip_updates", configure), self.config).freshness
        self.assertEqual(metrics.entity_ages_seconds, (12.0,))
        self.assertEqual(metrics.timestamped_entity_ratio, 0.5)

    def test_sequence_metrics_first_increasing_repeated_decreasing_and_hash(self) -> None:
        feed = self.parsed("vehicle_positions", lambda message, captured: self.add_vehicle(message))
        first = assess_quality(feed, self.config)
        self.assertEqual(self.result(first, "RTS001").status, "NOT_APPLICABLE")
        cases = ((-10, "PASS", False), (0, "PASS", True), (10, "FAIL", False))
        for prior_delta, status, repeated in cases:
            previous = PreviousCapture("prior", feed.captured_at_utc - timedelta(seconds=60),
                                       feed.header.timestamp + prior_delta,
                                       feed.payload_sha256 if repeated else "0" * 64)
            assessment = assess_quality(feed, self.config, previous)
            self.assertEqual(self.result(assessment, "RTS001").status, status)
            self.assertEqual(assessment.freshness.payload_repeated, repeated)
            self.assertEqual(assessment.freshness.local_capture_interval_seconds, 60)
            self.assertEqual(self.result(assessment, "RTS004").status, "INFO")

    def test_vehicle_completeness_complete_missing_and_expected_ratio(self) -> None:
        def configure(message: object, captured: datetime) -> None:
            self.add_vehicle(message, "complete")
            missing = self.add_vehicle(message, "missing")
            missing.vehicle.ClearField("vehicle")
            missing.vehicle.ClearField("position")
            missing.vehicle.ClearField("timestamp")
            unsupported = message.entity.add()
            unsupported.id = "alert"
            unsupported.alert.header_text.translation.add().text = "synthetic"
            deleted = message.entity.add()
            deleted.id = "deleted"
            deleted.is_deleted = True
        metrics = {m.metric_name: m for m in calculate_completeness(self.parsed("vehicle_positions", configure))}
        self.assertEqual((metrics["expected_entities"].numerator, metrics["expected_entities"].denominator), (2, 3))
        self.assertEqual(metrics["vehicle_id_present"].ratio, 0.5)
        self.assertEqual(metrics["position_present"].ratio, 0.5)
        self.assertEqual(metrics["vehicle_timestamp_present"].ratio, 0.5)
        self.assertEqual(metrics["deleted_entities"].numerator, 1)

    def test_trip_and_stop_time_completeness(self) -> None:
        def configure(message: object, captured: datetime) -> None:
            self.add_trip_update(message, "complete")
            missing = self.add_trip_update(message, "missing")
            missing.trip_update.ClearField("vehicle")
            missing.trip_update.ClearField("timestamp")
            missing.trip_update.ClearField("delay")
            missing.trip_update.ClearField("stop_time_update")
        metrics = {m.metric_name: m for m in calculate_completeness(self.parsed("trip_updates", configure))}
        self.assertEqual(metrics["trip_id_present"].ratio, 1.0)
        self.assertEqual(metrics["vehicle_id_present"].ratio, 0.5)
        self.assertEqual(metrics["stop_time_update_present"].ratio, 0.5)
        self.assertEqual(metrics["stop_time_reference_present"].ratio, 1.0)
        self.assertEqual(metrics["stop_time_event_present"].ratio, 0.5)
        self.assertEqual(metrics["stop_time_arrival_delay_present"].ratio, 0.5)
        self.assertEqual(metrics["stop_time_departure_time_present"].ratio, 0.5)

    def test_empty_feed_undefined_ratios_and_informational_fields(self) -> None:
        feed = self.parsed("vehicle_positions", lambda message, captured: None)
        assessment = assess_quality(feed, self.config)
        expected = next(m for m in assessment.completeness if m.metric_name == "expected_entities")
        self.assertIsNone(expected.ratio)
        completeness_results = [r for r in assessment.results if r.category == "completeness"]
        self.assertTrue(any(r.status == "NOT_APPLICABLE" for r in completeness_results))
        self.assertTrue(all(r.informational for r in completeness_results))

    def test_statuses_overall_enabled_policy_and_unique_rule_ids(self) -> None:
        feed = self.parsed("vehicle_positions", lambda message, captured: self.add_vehicle(message))
        assessment = assess_quality(feed, self.config)
        statuses = {r.status for r in assessment.results}
        self.assertTrue({"PASS", "INFO", "NOT_APPLICABLE"}.issubset(statuses))
        self.assertNotEqual(assessment.overall_status, "FAIL")
        identifiers = [r.rule_id for r in assessment.results]
        self.assertEqual(len(identifiers), len(set(identifiers)))


if __name__ == "__main__":
    unittest.main()
