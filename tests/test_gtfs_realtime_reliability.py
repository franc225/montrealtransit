from __future__ import annotations

import json
import sys
import tempfile
import unittest
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from gtfs_realtime_reliability import (  # noqa: E402
    MatchRunLineage, ReliabilityConfiguration, ReliabilityError,
    ReliabilityObservation, TripRelationshipFact, assess_reliability,
    classify_delta, evaluate_observation, load_reliability_config, percentile,
    safe_ratio, select_canonical_observations,
)


class GtfsRealtimeReliabilityTest(unittest.TestCase):
    def setUp(self) -> None:
        self.config = ReliabilityConfiguration(
            1, "1.0", "1.0", 2, -60, 300, 600, "LATEST_ELIGIBLE",
            ("MATCHED",), ("MATCHED",),
            ("ABSENT", "SCHEDULED", "CANCELED", "DUPLICATED"),
            ("RTP001_EVENT_CLASSIFICATION",), 2, .8, 30, "INSUFFICIENT_DATA",
        )
        self.lineage = MatchRunLineage("match-1", "snapshot-1", "1.0", 1, 2, "a" * 64)
        self.now = datetime(2026, 8, 3, 16, tzinfo=timezone.utc)

    def observation(self, **changes: object) -> ReliabilityObservation:
        values = dict(
            match_run_id="match-1", capture_uuid="capture-1", captured_at_utc=self.now,
            entity_index=0, stop_time_update_index=0, provider="stm",
            static_snapshot_identifier="snapshot-1", service_date=date(2026, 8, 3),
            static_trip_id="trip-1", static_route_id="route-1", direction_id=1,
            static_stop_sequence=1, static_stop_id="stop-1", event_type="ARRIVAL",
            scheduled_utc=self.now, scheduled_service_seconds=43200,
            reported_delay_seconds=20, calculated_delta_seconds=25,
            consistency_difference_seconds=-5, trip_match_status="MATCHED",
            trip_match_method="EXACT_TRIP_ID", stop_match_status="MATCHED",
            relationship_treatment="SCHEDULED", schedule_relationship_name="SCHEDULED",
            time_resolution_status="RESOLVED",
        )
        values.update(changes)
        return ReliabilityObservation(**values)

    def relationship(self, **changes: object) -> TripRelationshipFact:
        values = dict(capture_uuid="capture-1", entity_index=0, static_route_id="route-1",
                      direction_id=1, service_date=date(2026, 8, 3), static_trip_id="trip-1",
                      match_status="MATCHED",
                      relationship_treatment="SCHEDULED", schedule_relationship_name="SCHEDULED")
        values.update(changes)
        return TripRelationshipFact(**values)

    def test_configuration_loads_and_is_secret_free(self) -> None:
        loaded = load_reliability_config(ROOT)
        self.assertEqual((loaded.early_threshold_seconds, loaded.on_time_upper_seconds,
                          loaded.very_late_threshold_seconds), (-60, 300, 600))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "config").mkdir()
            raw = json.loads((ROOT / "config/gtfs_realtime_reliability.json").read_text())
            cases = (
                ({"early_threshold_seconds": 0}, "strictly ordered"),
                ({"on_time_upper_seconds": -61}, "strictly ordered"),
                ({"minimum_aggregate_denominator": 0}, "positive"),
                ({"minimum_matching_coverage_ratio": 1.1}, "between zero and one"),
                ({"delay_consistency_tolerance_seconds": -1}, "cannot be negative"),
                ({"enabled_indicators": ["UNKNOWN"]}, "unknown indicator"),
            )
            for updates, message in cases:
                with self.subTest(updates=updates):
                    candidate = {**raw, **updates}
                    (root / "config/gtfs_realtime_reliability.json").write_text(json.dumps(candidate))
                    with self.assertRaisesRegex(ReliabilityError, message) as raised:
                        load_reliability_config(root)
                    self.assertNotIn("STM_GTFS_REALTIME_API_KEY", str(raised.exception))

    def test_punctuality_boundaries_and_unavailable(self) -> None:
        cases = ((-61, "EARLY"), (-60, "ON_TIME"), (0, "ON_TIME"),
                 (300, "ON_TIME"), (301, "LATE"), (600, "LATE"),
                 (601, "VERY_LATE"), (None, "UNCLASSIFIED"))
        for value, expected in cases:
            with self.subTest(value=value):
                self.assertEqual(classify_delta(value, self.config), expected)

    def test_arrival_departure_and_selected_source(self) -> None:
        absolute = evaluate_observation(self.observation(), self.config)
        fallback = evaluate_observation(self.observation(
            event_type="DEPARTURE", calculated_delta_seconds=None,
            reported_delay_seconds=35), self.config)
        unavailable = evaluate_observation(self.observation(
            calculated_delta_seconds=None, reported_delay_seconds=None), self.config)
        self.assertEqual((absolute.selected_delta_seconds, absolute.selected_delta_source),
                         (25, "ABSOLUTE_EVENT_TIME"))
        self.assertEqual((fallback.selected_delta_seconds, fallback.selected_delta_source,
                          fallback.observation.event_type), (35, "REPORTED_DELAY", "DEPARTURE"))
        self.assertEqual((unavailable.punctuality_classification, unavailable.exclusion_reason),
                         ("UNCLASSIFIED", "INELIGIBLE_NO_COMPARABLE_EVENT"))

    def test_eligibility_exclusion_matrix(self) -> None:
        cases = (
            ({"trip_match_status": "UNMATCHED"}, "INELIGIBLE_UNMATCHED_TRIP"),
            ({"trip_match_status": "AMBIGUOUS"}, "INELIGIBLE_AMBIGUOUS_TRIP"),
            ({"trip_match_status": "CONFLICT"}, "INELIGIBLE_CONFLICTING_TRIP"),
            ({"stop_match_status": "UNMATCHED"}, "INELIGIBLE_STOP_MATCH"),
            ({"stop_match_status": "AMBIGUOUS"}, "INELIGIBLE_AMBIGUOUS_STOP"),
            ({"trip_match_status": "NO_STATIC_COVERAGE"}, "INELIGIBLE_NO_STATIC_COVERAGE"),
            ({"trip_match_status": "INCOMPLETE_LINEAGE"}, "INELIGIBLE_INCOMPLETE_LINEAGE"),
            ({"trip_match_status": "UNSUPPORTED", "trip_match_method": "FREQUENCY_UNSUPPORTED"},
             "INELIGIBLE_FREQUENCY_UNSUPPORTED"),
            ({"relationship_treatment": "DELETED"}, "INELIGIBLE_DELETED"),
            ({"relationship_treatment": "ADDED_OR_UNSCHEDULED"}, "INELIGIBLE_UNSUPPORTED_RELATIONSHIP"),
            ({"scheduled_utc": None, "time_resolution_status": "DST_AMBIGUOUS"},
             "INELIGIBLE_UNRESOLVED_SCHEDULE"),
        )
        for changes, expected in cases:
            with self.subTest(expected=expected):
                result = evaluate_observation(self.observation(**changes), self.config)
                self.assertEqual((result.eligibility_status, result.exclusion_reason), (expected, expected))

    def test_consistency_threshold_and_values_retained(self) -> None:
        boundary = evaluate_observation(self.observation(consistency_difference_seconds=30), self.config)
        exceeded = evaluate_observation(self.observation(consistency_difference_seconds=-31), self.config)
        self.assertFalse(boundary.consistency_finding)
        self.assertTrue(exceeded.consistency_finding)
        self.assertEqual((exceeded.observation.reported_delay_seconds,
                          exceeded.observation.calculated_delta_seconds), (20, 25))

    def test_latest_eligible_selection_and_tie_breakers(self) -> None:
        first = evaluate_observation(self.observation(capture_uuid="a", calculated_delta_seconds=10), self.config)
        later_ineligible = evaluate_observation(self.observation(
            capture_uuid="z", captured_at_utc=self.now + timedelta(minutes=1),
            stop_match_status="UNMATCHED", calculated_delta_seconds=50), self.config)
        tie_winner = evaluate_observation(self.observation(
            capture_uuid="b", entity_index=2, calculated_delta_seconds=20), self.config)
        selected = select_canonical_observations((first, later_ineligible, tie_winner))[0]
        self.assertEqual(selected.observation.capture_uuid, "b")
        self.assertEqual(selected.candidate_observation_count, 3)
        self.assertEqual(selected.first_observed_at_utc, self.now)
        self.assertTrue(selected.delta_changed_across_observations)

    def test_different_instances_snapshots_and_unresolved_are_not_merged(self) -> None:
        base = evaluate_observation(self.observation(), self.config)
        other_trip = evaluate_observation(self.observation(static_trip_id="trip-2"), self.config)
        other_snapshot = evaluate_observation(self.observation(static_snapshot_identifier="snapshot-2"), self.config)
        unresolved_a = evaluate_observation(self.observation(
            entity_index=4, service_date=None, static_trip_id=None, static_stop_sequence=None), self.config)
        unresolved_b = evaluate_observation(self.observation(
            entity_index=5, service_date=None, static_trip_id=None, static_stop_sequence=None), self.config)
        self.assertEqual(len(select_canonical_observations(
            (base, other_trip, other_snapshot, unresolved_a, unresolved_b))), 5)

    def test_aggregate_statistics_percentiles_and_separate_event_types(self) -> None:
        observations = tuple(self.observation(
            stop_time_update_index=index, static_stop_sequence=index + 1,
            event_type="ARRIVAL" if index < 4 else "DEPARTURE",
            calculated_delta_seconds=value, reported_delay_seconds=value)
            for index, value in enumerate((-120, -60, 300, 601, 10)))
        assessment = assess_reliability(self.lineage, observations, (self.relationship(),), self.config)
        arrival = next(item for item in assessment.aggregates
                       if item.dimension_type == "SERVICE_DATE" and item.event_type == "ARRIVAL")
        departure = next(item for item in assessment.aggregates
                         if item.dimension_type == "SERVICE_DATE" and item.event_type == "DEPARTURE")
        self.assertEqual((arrival.early_count, arrival.on_time_count, arrival.very_late_count), (1, 2, 1))
        self.assertEqual(departure.classified_event_count, 1)
        self.assertEqual(arrival.minimum_delay_seconds, -120)
        self.assertEqual(arrival.maximum_delay_seconds, 601)
        self.assertEqual(percentile([10], .95), 10.0)

    def test_zero_denominators_and_low_sample(self) -> None:
        self.assertIsNone(safe_ratio(0, 0))
        observation = self.observation(calculated_delta_seconds=None, reported_delay_seconds=None)
        assessment = assess_reliability(self.lineage, (observation,), (self.relationship(),), self.config)
        aggregate = next(item for item in assessment.aggregates if item.dimension_type == "SERVICE_DATE")
        self.assertIsNone(aggregate.on_time_ratio)
        self.assertEqual(aggregate.interpretation_status, "NOT_APPLICABLE")

    def test_trip_summary_delay_change_and_no_hidden_trip_classification(self) -> None:
        observations = (
            self.observation(static_stop_sequence=1, calculated_delta_seconds=10),
            self.observation(stop_time_update_index=1, static_stop_sequence=2,
                             calculated_delta_seconds=700),
        )
        trip = assess_reliability(self.lineage, observations, (self.relationship(),), self.config).trips[0]
        self.assertEqual((trip.start_delay_seconds, trip.end_delay_seconds, trip.delay_change_seconds),
                         (10, 700, 690))
        self.assertTrue(trip.any_very_late)
        self.assertFalse(hasattr(trip, "trip_punctuality_classification"))

    def test_cancellation_is_only_explicit_and_denominator_documented(self) -> None:
        relationships = (self.relationship(schedule_relationship_name="CANCELED"),
                         self.relationship(entity_index=1, schedule_relationship_name="SCHEDULED"),
                         self.relationship(entity_index=2, match_status="UNMATCHED",
                                           schedule_relationship_name=None))
        assessment = assess_reliability(self.lineage, (self.observation(),), relationships, self.config)
        self.assertEqual((assessment.reported_cancellation_count,
                          assessment.reported_cancellation_denominator,
                          assessment.reported_cancellation_ratio), (1, 3, 1 / 3))

    def test_trip_cancellation_uses_exact_trip_identity(self) -> None:
        relationships = (self.relationship(static_trip_id="other-trip",
                                           schedule_relationship_name="CANCELED"),)
        trip = assess_reliability(self.lineage, (self.observation(),),
                                  relationships, self.config).trips[0]
        self.assertFalse(trip.reported_cancellation)

    def test_coverage_counts_unmatched_and_undefined_ratios(self) -> None:
        relationships = (self.relationship(), self.relationship(entity_index=1, match_status="UNMATCHED"),
                         self.relationship(entity_index=2, match_status="AMBIGUOUS"))
        assessment = assess_reliability(self.lineage, (self.observation(),), relationships, self.config)
        coverage = assessment.coverage
        self.assertEqual((coverage.matched_trip_count, coverage.unmatched_trip_count,
                          coverage.ambiguous_trip_count), (1, 1, 1))
        self.assertEqual(coverage.trip_matching_ratio, 1 / 3)

    def test_lineage_prerequisites(self) -> None:
        with self.assertRaisesRegex(ReliabilityError, "incomplete"):
            assess_reliability(replace(self.lineage, realtime_persistence_schema_version=1), (), (), self.config)
        with self.assertRaisesRegex(ReliabilityError, "algorithm version"):
            assess_reliability(replace(self.lineage, matching_algorithm_version="0.9"), (), (), self.config)


if __name__ == "__main__":
    unittest.main()
