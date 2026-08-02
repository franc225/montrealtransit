from __future__ import annotations

import sys
import unittest
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SOURCE_DIRECTORY = REPOSITORY_ROOT / "src"
if str(SOURCE_DIRECTORY) not in sys.path:
    sys.path.insert(0, str(SOURCE_DIRECTORY))

from gtfs_realtime_matching import (  # noqa: E402
    CalendarException, CalendarService, MatchingConfig, MatchingError,
    RealtimeEntityFact, RealtimeStopFact, StaticSnapshot, StaticStopTime,
    StaticTripCandidate, assess_matches, match_entity, parse_gtfs_time,
    service_is_active,
)


class GtfsRealtimeMatchingTest(unittest.TestCase):
    def setUp(self) -> None:
        self.config = MatchingConfig(1, "1.0", "America/Montreal", 2, 1,
                                     True, True, True, True, True, "UNSUPPORTED")
        self.day = date(2026, 8, 3)
        self.capture = datetime(2026, 8, 3, 16, 0, tzinfo=timezone.utc)
        self.snapshot = StaticSnapshot("snapshot-1", "run-static", "v1",
                                       date(2026, 8, 1), date(2026, 8, 31))
        self.calendar = (CalendarService("weekday", (True, True, True, True, True, False, False),
                                         date(2026, 8, 1), date(2026, 8, 31)),)
        self.trip = StaticTripCandidate("trip-1", "route-1", "weekday", 1, "shape-1", "12:00:00")
        self.stops = (
            StaticStopTime("trip-1", 1, "stop-a", "12:00:00", "12:01:00"),
            StaticStopTime("trip-1", 2, "stop-b", "25:10:00", "25:11:00"),
        )

    def entity(self, **changes: object) -> RealtimeEntityFact:
        base = RealtimeEntityFact(
            "capture-1", 0, "entity-1", "trip_update", False, 2,
            "trip-1", "route-1", 1, "12:00:00", "20260803", 0,
            "SCHEDULED", "vehicle-1", 15, (),
        )
        return replace(base, **changes)

    def match(self, entity: RealtimeEntityFact, trips: tuple[StaticTripCandidate, ...] | None = None,
              exceptions: tuple[CalendarException, ...] = (), config: MatchingConfig | None = None):
        return match_entity(entity, self.capture, self.snapshot, trips or (self.trip,),
                            self.calendar, exceptions, self.stops, config or self.config)

    def test_complete_and_incomplete_persistence_lineage(self) -> None:
        self.assertEqual(self.match(self.entity()).status, "MATCHED")
        for version in (None, 1):
            result = self.match(self.entity(persistence_schema_version=version))
            self.assertEqual((result.status, result.method),
                             ("INCOMPLETE_LINEAGE", "INCOMPLETE_PERSISTENCE_SCHEMA"))

    def test_exact_trip_and_explicit_service_date(self) -> None:
        result = self.match(self.entity())
        self.assertEqual((result.method, result.service_date, result.service_date_source),
                         ("EXACT_TRIP_ID_AND_SERVICE_DATE", self.day, "EXPLICIT_START_DATE"))

    def test_trip_not_found_inactive_and_static_coverage(self) -> None:
        missing = self.match(self.entity(trip_id="missing"))
        self.assertEqual((missing.status, missing.conflict_code), ("UNMATCHED", "TRIP_ID_NOT_FOUND"))
        inactive = self.match(self.entity(start_date="20260802"))
        self.assertEqual((inactive.status, inactive.conflict_code), ("UNMATCHED", "INACTIVE_SERVICE"))
        outside = self.match(self.entity(start_date="20260901"))
        self.assertEqual(outside.status, "NO_STATIC_COVERAGE")

    def test_route_direction_and_start_time_consistency(self) -> None:
        cases = (("route_id", "other", "ROUTE_CONFLICT"),
                 ("direction_id", 0, "DIRECTION_CONFLICT"),
                 ("start_time", "12:30:00", "START_TIME_CONFLICT"))
        for field, value, code in cases:
            with self.subTest(field=field):
                result = self.match(self.entity(**{field: value}))
                self.assertEqual((result.status, result.conflict_code), ("CONFLICT", code))

    def test_composite_fallback_unique_zero_multiple_and_disabled(self) -> None:
        entity = self.entity(trip_id=None)
        self.assertEqual(self.match(entity).method, "UNIQUE_COMPOSITE")
        self.assertEqual(self.match(replace(entity, route_id="missing")).status, "UNMATCHED")
        duplicate = replace(self.trip, trip_id="trip-2")
        self.assertEqual(self.match(entity, (self.trip, duplicate)).status, "AMBIGUOUS")
        disabled = replace(self.config, composite_fallback_enabled=False)
        self.assertEqual(self.match(entity, config=disabled).conflict_code, "FALLBACK_DISABLED")
        self.assertEqual(self.match(replace(entity, start_time=None)).conflict_code, "MISSING_COMPOSITE_IDENTITY")

    def test_vehicle_trip_update_deleted_and_relationship_treatment(self) -> None:
        self.assertEqual(self.match(self.entity(entity_type="vehicle_position")).status, "MATCHED")
        self.assertEqual(self.match(self.entity(entity_type="trip_update")).status, "MATCHED")
        self.assertEqual(self.match(self.entity(is_deleted=True)).status, "NOT_APPLICABLE")
        self.assertEqual(self.match(self.entity(schedule_relationship_name="CANCELED")).status, "MATCHED")
        for relationship in ("ADDED", "UNSCHEDULED"):
            result = self.match(self.entity(schedule_relationship_name=relationship, trip_id=None))
            self.assertEqual((result.status, result.method), ("NOT_APPLICABLE", "ADDED_TRIP"))
        self.assertEqual(self.match(self.entity(schedule_relationship_name="UNKNOWN_99")).status, "UNSUPPORTED")

    def test_service_calendar_weekdays_boundaries_and_exceptions(self) -> None:
        self.assertTrue(service_is_active("weekday", self.day, self.calendar, ()))
        boundary = date(2026, 8, 31)
        self.assertTrue(service_is_active("weekday", boundary, self.calendar, ()))
        removed = (CalendarException("weekday", self.day, 2),)
        self.assertFalse(service_is_active("weekday", self.day, self.calendar, removed))
        added = (CalendarException("exception-only", self.day, 1),)
        self.assertTrue(service_is_active("exception-only", self.day, (), added))

    def test_inferred_montreal_dates_previous_day_and_ambiguity(self) -> None:
        entity = self.entity(start_date=None)
        result = self.match(entity)
        self.assertEqual((result.status, result.service_date_source), ("MATCHED", "INFERRED_LOCAL_DATE"))
        previous_calendar = (CalendarService("weekday", (False,) * 7,
                                             date(2026, 8, 1), date(2026, 8, 31)),)
        exception = (CalendarException("weekday", self.day - timedelta(days=1), 1),)
        previous = match_entity(entity, self.capture, self.snapshot, (self.trip,), previous_calendar,
                                exception, self.stops, self.config)
        self.assertEqual(previous.service_date, self.day - timedelta(days=1))
        both = (CalendarException("weekday", self.day, 1),
                CalendarException("weekday", self.day - timedelta(days=1), 1))
        ambiguous = match_entity(entity, self.capture, self.snapshot, (self.trip,), (), both,
                                 self.stops, self.config)
        self.assertEqual(ambiguous.status, "AMBIGUOUS")

    def test_utc_date_is_not_used_as_montreal_service_date(self) -> None:
        captured = datetime(2026, 8, 4, 2, 0, tzinfo=timezone.utc)
        result = match_entity(self.entity(start_date=None), captured, self.snapshot, (self.trip,),
                              self.calendar, (), self.stops, self.config)
        self.assertEqual(result.service_date, date(2026, 8, 3))

    def test_frequency_trip_is_explicitly_unsupported(self) -> None:
        result = self.match(self.entity(), (replace(self.trip, frequency_based=True),))
        self.assertEqual((result.status, result.method), ("UNSUPPORTED", "FREQUENCY_UNSUPPORTED"))

    def test_stop_sequence_agreement_conflict_and_order(self) -> None:
        updates = (
            RealtimeStopFact(0, 2, "stop-b", 0, "SCHEDULED", 10, None, 20, None),
            RealtimeStopFact(1, 1, "wrong", 0, "SCHEDULED", None, None, None, None),
        )
        result = self.match(self.entity(stop_updates=updates))
        self.assertEqual([item.update_index for item in result.stop_matches], [0, 1])
        self.assertEqual(result.stop_matches[0].status, "MATCHED")
        self.assertEqual((result.stop_matches[1].status, result.stop_matches[1].conflict_code),
                         ("CONFLICT", "STOP_ID_CONFLICT"))

    def test_stop_id_fallback_ambiguity_unknown_missing_and_parent_unmatched(self) -> None:
        repeated = self.stops + (StaticStopTime("trip-1", 3, "stop-b", "26:00:00", "26:01:00"),)
        updates = (
            RealtimeStopFact(0, None, "stop-a", None, None, None, None, None, None),
            RealtimeStopFact(1, None, "stop-b", None, None, None, None, None, None),
            RealtimeStopFact(2, None, "unknown", None, None, None, None, None, None),
            RealtimeStopFact(3, None, None, None, None, None, None, None, None),
        )
        matched = match_entity(self.entity(stop_updates=updates), self.capture, self.snapshot,
                               (self.trip,), self.calendar, (), repeated, self.config)
        self.assertEqual([item.status for item in matched.stop_matches],
                         ["MATCHED", "AMBIGUOUS", "UNMATCHED", "UNMATCHED"])
        unmatched = self.match(self.entity(trip_id="missing", stop_updates=updates[:1]))
        self.assertEqual(unmatched.stop_matches[0].status, "NOT_APPLICABLE")

    def test_scheduled_times_arbitrary_hours_and_malformed(self) -> None:
        cases = (("12:00:00", 43200, 0), ("24:00:00", 86400, 1),
                 ("25:10:00", 90600, 1), ("49:00:00", 176400, 2))
        for original, seconds, offset in cases:
            with self.subTest(original=original):
                parsed = parse_gtfs_time(original, self.day)
                self.assertEqual((parsed.original, parsed.service_seconds, parsed.service_day_offset),
                                 (original, seconds, offset))
                self.assertEqual(parsed.resolution_status, "RESOLVED")
                self.assertIsNotNone(parsed.utc_datetime)
        with self.assertRaises(MatchingError):
            parse_gtfs_time("25:99:00", self.day)

    def test_dst_ambiguity_and_nonexistence_are_explicit(self) -> None:
        ambiguous = parse_gtfs_time("01:30:00", date(2026, 11, 1))
        nonexistent = parse_gtfs_time("02:30:00", date(2026, 3, 8))
        self.assertEqual((ambiguous.resolution_status, ambiguous.utc_datetime), ("DST_AMBIGUOUS", None))
        self.assertEqual((nonexistent.resolution_status, nonexistent.utc_datetime), ("DST_NONEXISTENT", None))

    def test_comparison_facts_preserve_reported_absolute_and_disagreement(self) -> None:
        scheduled = parse_gtfs_time("12:00:00", self.day)
        actual = scheduled.utc_datetime + timedelta(seconds=40)
        update = RealtimeStopFact(0, 1, "stop-a", None, None, 30, actual, None, None)
        result = self.match(self.entity(stop_updates=(update,))).stop_matches[0]
        self.assertEqual(result.arrival_comparison.reported_delay, 30)
        self.assertEqual(result.arrival_comparison.absolute_delta, 40)
        self.assertEqual(result.arrival_comparison.consistency_difference, -10)
        self.assertEqual(result.arrival_comparison.delta_source, "REPORTED_DELAY")
        self.assertFalse(hasattr(result, "punctuality"))

    def test_assessment_preserves_entity_order(self) -> None:
        entities = (self.entity(entity_index=2, entity_id="second"),
                    self.entity(entity_index=5, entity_id="fifth"))
        assessment = assess_matches("capture-1", self.capture, self.snapshot, entities,
                                    (self.trip,), self.calendar, (), self.stops, self.config)
        self.assertEqual([item.entity_index for item in assessment.results], [2, 5])


if __name__ == "__main__":
    unittest.main()
