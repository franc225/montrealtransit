from __future__ import annotations

import json
import math
from collections import defaultdict
from dataclasses import dataclass, replace
from datetime import date, datetime
from pathlib import Path
from statistics import mean, median


CLASSIFICATIONS = ("EARLY", "ON_TIME", "LATE", "VERY_LATE", "UNCLASSIFIED")
INDICATOR_IDS = frozenset({
    "RTP001_EVENT_CLASSIFICATION", "RTP002_ON_TIME_RATIO",
    "RTD001_MEDIAN_DELAY_SECONDS", "RTD002_P95_DELAY_SECONDS",
    "RTR001_REPORTED_CANCELLATION_COUNT", "RTCV001_TRIP_MATCHING_RATIO",
    "RTCV002_STOP_MATCHING_RATIO", "RTCV003_COMPARISON_AVAILABILITY_RATIO",
    "RTCONS001_DELAY_SOURCE_DISAGREEMENT",
})


class ReliabilityError(RuntimeError):
    """A concise, secret-safe reliability calculation failure."""


@dataclass(frozen=True)
class ReliabilityConfiguration:
    schema_version: int
    reliability_algorithm_version: str
    required_matching_algorithm_version: str
    required_persistence_schema_version: int
    early_threshold_seconds: int
    on_time_upper_seconds: int
    very_late_threshold_seconds: int
    observation_selection_policy: str
    eligible_trip_match_statuses: tuple[str, ...]
    eligible_stop_match_statuses: tuple[str, ...]
    eligible_schedule_relationships: tuple[str, ...]
    enabled_indicators: tuple[str, ...]
    minimum_aggregate_denominator: int
    minimum_matching_coverage_ratio: float
    delay_consistency_tolerance_seconds: int
    incomplete_data_status: str


@dataclass(frozen=True)
class MatchRunLineage:
    match_run_id: str
    static_snapshot_identifier: str
    matching_algorithm_version: str
    matching_config_schema_version: int
    realtime_persistence_schema_version: int
    payload_sha256: str


@dataclass(frozen=True)
class ReliabilityObservation:
    match_run_id: str
    capture_uuid: str
    captured_at_utc: datetime
    entity_index: int
    stop_time_update_index: int
    provider: str
    static_snapshot_identifier: str
    service_date: date | None
    static_trip_id: str | None
    static_route_id: str | None
    direction_id: int | None
    static_stop_sequence: int | None
    static_stop_id: str | None
    event_type: str
    scheduled_utc: datetime | None
    scheduled_service_seconds: int | None
    reported_delay_seconds: int | None
    calculated_delta_seconds: int | None
    consistency_difference_seconds: int | None
    trip_match_status: str
    trip_match_method: str
    stop_match_status: str
    relationship_treatment: str
    schedule_relationship_name: str | None
    time_resolution_status: str


@dataclass(frozen=True)
class EventReliabilityResult:
    observation: ReliabilityObservation
    selected_delta_seconds: int | None
    selected_delta_source: str
    eligibility_status: str
    exclusion_reason: str | None
    punctuality_classification: str
    consistency_finding: bool
    candidate_observation_count: int = 1
    first_observed_at_utc: datetime | None = None
    selected_observed_at_utc: datetime | None = None
    delta_changed_across_observations: bool = False


@dataclass(frozen=True)
class TripRelationshipFact:
    capture_uuid: str
    entity_index: int
    static_route_id: str | None
    direction_id: int | None
    service_date: date | None
    static_trip_id: str | None
    match_status: str
    relationship_treatment: str
    schedule_relationship_name: str | None


@dataclass(frozen=True)
class CoverageMetrics:
    realtime_entity_count: int
    eligible_realtime_entity_count: int
    matched_trip_count: int
    unmatched_trip_count: int
    ambiguous_trip_count: int
    conflict_trip_count: int
    unsupported_trip_count: int
    stop_time_update_count: int
    matched_stop_event_count: int
    unmatched_stop_event_count: int
    ambiguous_stop_event_count: int
    comparable_event_count: int
    classified_event_count: int
    canonical_observation_count: int
    candidate_observation_count: int
    trip_matching_ratio: float | None
    stop_matching_ratio: float | None
    comparison_availability_ratio: float | None
    classification_ratio: float | None
    canonical_observation_ratio: float | None
    candidates_per_selected_event: float | None


@dataclass(frozen=True)
class AggregateReliabilityResult:
    dimension_type: str
    service_date: date | None
    route_id: str | None
    direction_id: int | None
    stop_id: str | None
    event_type: str
    eligible_event_count: int
    classified_event_count: int
    early_count: int
    on_time_count: int
    late_count: int
    very_late_count: int
    unclassified_count: int
    early_ratio: float | None
    on_time_ratio: float | None
    late_ratio: float | None
    very_late_ratio: float | None
    minimum_delay_seconds: int | None
    maximum_delay_seconds: int | None
    mean_delay_seconds: float | None
    median_delay_seconds: float | None
    p90_delay_seconds: float | None
    p95_delay_seconds: float | None
    eligible_trip_instance_count: int
    reported_cancellation_count: int
    interpretation_status: str


@dataclass(frozen=True)
class TripReliabilityResult:
    static_snapshot_identifier: str
    service_date: date
    static_trip_id: str
    static_route_id: str | None
    direction_id: int | None
    event_type: str
    eligible_event_count: int
    classified_event_count: int
    on_time_count: int
    on_time_ratio: float | None
    maximum_lateness_seconds: int | None
    median_delay_seconds: float | None
    p95_delay_seconds: float | None
    first_stop_sequence: int | None
    last_stop_sequence: int | None
    start_delay_seconds: int | None
    end_delay_seconds: int | None
    delay_change_seconds: int | None
    any_very_late: bool
    reported_cancellation: bool
    coverage_status: str


@dataclass(frozen=True)
class ReliabilityFinding:
    indicator_id: str
    category: str
    status: str
    entity_index: int | None
    stop_time_update_index: int | None
    metric_value: float | None
    threshold: float | None
    numerator: int | None
    denominator: int | None
    unit: str | None
    details: str


@dataclass(frozen=True)
class ReliabilityAssessment:
    lineage: MatchRunLineage
    source_observation_count: int
    events: tuple[EventReliabilityResult, ...]
    trips: tuple[TripReliabilityResult, ...]
    aggregates: tuple[AggregateReliabilityResult, ...]
    findings: tuple[ReliabilityFinding, ...]
    coverage: CoverageMetrics
    relationship_counts: tuple[tuple[str, int], ...]
    reported_cancellation_count: int
    reported_cancellation_denominator: int
    reported_cancellation_ratio: float | None
    overall_status: str


def load_reliability_config(project_root: Path) -> ReliabilityConfiguration:
    path = project_root / "config" / "gtfs_realtime_reliability.json"
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise ReliabilityError("GTFS-Realtime reliability configuration is invalid.") from None
    required = {
        "schema_version": int, "reliability_algorithm_version": str,
        "required_matching_algorithm_version": str,
        "required_persistence_schema_version": int, "early_threshold_seconds": int,
        "on_time_upper_seconds": int, "very_late_threshold_seconds": int,
        "observation_selection_policy": str, "eligible_trip_match_statuses": list,
        "eligible_stop_match_statuses": list, "eligible_schedule_relationships": list,
        "enabled_indicators": list, "minimum_aggregate_denominator": int,
        "minimum_matching_coverage_ratio": (int, float),
        "delay_consistency_tolerance_seconds": int, "incomplete_data_status": str,
    }
    if any(key not in raw or not isinstance(raw[key], kind) for key, kind in required.items()):
        raise ReliabilityError("GTFS-Realtime reliability configuration fields are invalid.")
    if raw["schema_version"] != 1 or raw["observation_selection_policy"] != "LATEST_ELIGIBLE":
        raise ReliabilityError("Unsupported GTFS-Realtime reliability configuration.")
    early, on_time, very_late = (raw["early_threshold_seconds"],
                                  raw["on_time_upper_seconds"],
                                  raw["very_late_threshold_seconds"])
    if early >= 0 or not early < on_time < very_late:
        raise ReliabilityError("Reliability punctuality thresholds must be strictly ordered.")
    if raw["minimum_aggregate_denominator"] < 1:
        raise ReliabilityError("Minimum aggregate denominator must be positive.")
    coverage = float(raw["minimum_matching_coverage_ratio"])
    if not 0 <= coverage <= 1:
        raise ReliabilityError("Minimum matching coverage ratio must be between zero and one.")
    if raw["delay_consistency_tolerance_seconds"] < 0:
        raise ReliabilityError("Delay consistency tolerance cannot be negative.")
    if not all(isinstance(value, str) and value for key in (
            "eligible_trip_match_statuses", "eligible_stop_match_statuses",
            "eligible_schedule_relationships", "enabled_indicators") for value in raw[key]):
        raise ReliabilityError("Reliability configuration lists contain invalid values.")
    unknown = set(raw["enabled_indicators"]) - INDICATOR_IDS
    if unknown:
        raise ReliabilityError("Reliability configuration contains an unknown indicator identifier.")
    return ReliabilityConfiguration(
        raw["schema_version"], raw["reliability_algorithm_version"],
        raw["required_matching_algorithm_version"], raw["required_persistence_schema_version"],
        early, on_time, very_late, raw["observation_selection_policy"],
        tuple(raw["eligible_trip_match_statuses"]), tuple(raw["eligible_stop_match_statuses"]),
        tuple(raw["eligible_schedule_relationships"]), tuple(raw["enabled_indicators"]),
        raw["minimum_aggregate_denominator"], coverage,
        raw["delay_consistency_tolerance_seconds"], raw["incomplete_data_status"],
    )


def safe_ratio(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def classify_delta(delta: int | None, config: ReliabilityConfiguration) -> str:
    if delta is None:
        return "UNCLASSIFIED"
    if delta < config.early_threshold_seconds:
        return "EARLY"
    if delta <= config.on_time_upper_seconds:
        return "ON_TIME"
    if delta <= config.very_late_threshold_seconds:
        return "LATE"
    return "VERY_LATE"


def evaluate_observation(observation: ReliabilityObservation,
                         config: ReliabilityConfiguration) -> EventReliabilityResult:
    selected = observation.calculated_delta_seconds
    source = "ABSOLUTE_EVENT_TIME" if selected is not None else "REPORTED_DELAY"
    if selected is None:
        selected = observation.reported_delay_seconds
    reason = None
    if observation.trip_match_status == "INCOMPLETE_LINEAGE":
        reason = "INELIGIBLE_INCOMPLETE_LINEAGE"
    elif observation.trip_match_status == "NO_STATIC_COVERAGE":
        reason = "INELIGIBLE_NO_STATIC_COVERAGE"
    elif observation.trip_match_status == "AMBIGUOUS":
        reason = "INELIGIBLE_AMBIGUOUS_TRIP"
    elif observation.trip_match_status == "CONFLICT":
        reason = "INELIGIBLE_CONFLICTING_TRIP"
    elif observation.trip_match_method == "FREQUENCY_UNSUPPORTED":
        reason = "INELIGIBLE_FREQUENCY_UNSUPPORTED"
    elif observation.relationship_treatment == "DELETED":
        reason = "INELIGIBLE_DELETED"
    elif observation.relationship_treatment in {"ADDED_OR_UNSCHEDULED", "UNSUPPORTED"}:
        reason = "INELIGIBLE_UNSUPPORTED_RELATIONSHIP"
    elif observation.trip_match_status not in config.eligible_trip_match_statuses:
        reason = "INELIGIBLE_UNMATCHED_TRIP"
    elif observation.stop_match_status not in config.eligible_stop_match_statuses:
        reason = "INELIGIBLE_AMBIGUOUS_STOP" if observation.stop_match_status == "AMBIGUOUS" else "INELIGIBLE_STOP_MATCH"
    elif (observation.schedule_relationship_name or "ABSENT") not in config.eligible_schedule_relationships:
        reason = "INELIGIBLE_UNSUPPORTED_RELATIONSHIP"
    elif observation.service_date is None:
        reason = "INELIGIBLE_NO_SERVICE_DATE"
    elif observation.scheduled_utc is None or observation.time_resolution_status != "RESOLVED":
        reason = "INELIGIBLE_UNRESOLVED_SCHEDULE"
    elif selected is None:
        reason = "INELIGIBLE_NO_COMPARABLE_EVENT"
        source = "UNAVAILABLE"
    classification = classify_delta(selected, config) if reason is None else "UNCLASSIFIED"
    finding = (observation.consistency_difference_seconds is not None and
               abs(observation.consistency_difference_seconds) > config.delay_consistency_tolerance_seconds)
    return EventReliabilityResult(
        observation, selected, source, "ELIGIBLE" if reason is None else reason,
        reason, classification, finding, first_observed_at_utc=observation.captured_at_utc,
        selected_observed_at_utc=observation.captured_at_utc,
    )


def _event_key(result: EventReliabilityResult) -> tuple[object, ...]:
    item = result.observation
    if item.service_date is None or item.static_trip_id is None or item.static_stop_sequence is None:
        return ("UNRESOLVED", item.capture_uuid, item.entity_index,
                item.stop_time_update_index, item.event_type)
    return (item.provider, item.static_snapshot_identifier, item.service_date,
            item.static_trip_id, item.static_stop_sequence, item.event_type)


def select_canonical_observations(results: tuple[EventReliabilityResult, ...]) -> tuple[EventReliabilityResult, ...]:
    grouped: dict[tuple[object, ...], list[EventReliabilityResult]] = defaultdict(list)
    for result in results:
        grouped[_event_key(result)].append(result)
    selected: list[EventReliabilityResult] = []
    for candidates in grouped.values():
        eligible = [item for item in candidates if item.eligibility_status == "ELIGIBLE"]
        pool = eligible or candidates
        winner = max(pool, key=lambda item: (
            item.observation.captured_at_utc, item.observation.capture_uuid,
            item.observation.entity_index, item.observation.stop_time_update_index,
        ))
        deltas = {item.selected_delta_seconds for item in candidates}
        selected.append(replace(
            winner, candidate_observation_count=len(candidates),
            first_observed_at_utc=min(item.observation.captured_at_utc for item in candidates),
            selected_observed_at_utc=winner.observation.captured_at_utc,
            delta_changed_across_observations=len(deltas) > 1,
        ))
    return tuple(sorted(selected, key=lambda item: (
        item.observation.service_date or date.min, item.observation.static_trip_id or "",
        item.observation.static_stop_sequence or -1, item.observation.event_type,
    )))


def percentile(values: list[int], percentile_value: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    rank = (len(ordered) - 1) * percentile_value
    lower, upper = math.floor(rank), math.ceil(rank)
    if lower == upper:
        return float(ordered[lower])
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (rank - lower)


def _aggregate(dimension_type: str, key: tuple[object, ...],
               events: list[EventReliabilityResult], config: ReliabilityConfiguration,
               cancellation_count: int = 0) -> AggregateReliabilityResult:
    eligible = [item for item in events if item.eligibility_status == "ELIGIBLE"]
    classified = [item for item in eligible if item.punctuality_classification != "UNCLASSIFIED"]
    counts = {name: sum(item.punctuality_classification == name for item in eligible)
              for name in CLASSIFICATIONS}
    values = [int(item.selected_delta_seconds) for item in classified if item.selected_delta_seconds is not None]
    service_date, route_id, direction_id, stop_id, event_type = key
    status = "SUFFICIENT_DATA" if len(classified) >= config.minimum_aggregate_denominator else (
        "NOT_APPLICABLE" if not classified else "LOW_SAMPLE")
    return AggregateReliabilityResult(
        dimension_type, service_date, route_id, direction_id, stop_id, str(event_type),
        len(eligible), len(classified), counts["EARLY"], counts["ON_TIME"], counts["LATE"],
        counts["VERY_LATE"], counts["UNCLASSIFIED"], safe_ratio(counts["EARLY"], len(classified)),
        safe_ratio(counts["ON_TIME"], len(classified)), safe_ratio(counts["LATE"], len(classified)),
        safe_ratio(counts["VERY_LATE"], len(classified)), min(values) if values else None,
        max(values) if values else None, mean(values) if values else None,
        median(values) if values else None, percentile(values, .90), percentile(values, .95),
        len({(item.observation.service_date, item.observation.static_trip_id) for item in eligible}),
        cancellation_count, status,
    )


def build_aggregates(events: tuple[EventReliabilityResult, ...], config: ReliabilityConfiguration,
                     relationships: tuple[TripRelationshipFact, ...]) -> tuple[AggregateReliabilityResult, ...]:
    definitions = {
        "SERVICE_DATE": lambda o: (o.service_date, None, None, None, o.event_type),
        "ROUTE": lambda o: (o.service_date, o.static_route_id, None, None, o.event_type),
        "ROUTE_DIRECTION": lambda o: (o.service_date, o.static_route_id, o.direction_id, None, o.event_type),
        "STOP": lambda o: (o.service_date, None, None, o.static_stop_id, o.event_type),
        "STOP_ROUTE_DIRECTION": lambda o: (o.service_date, o.static_route_id, o.direction_id, o.static_stop_id, o.event_type),
        "SYSTEM_CAPTURE_SCOPE": lambda o: (None, None, None, None, o.event_type),
    }
    output: list[AggregateReliabilityResult] = []
    for dimension, key_function in definitions.items():
        groups: dict[tuple[object, ...], list[EventReliabilityResult]] = defaultdict(list)
        for event in events:
            groups[key_function(event.observation)].append(event)
        for key, values in groups.items():
            cancellations = sum(
                fact.schedule_relationship_name == "CANCELED" and
                (key[0] is None or fact.service_date == key[0]) and
                (key[1] is None or fact.static_route_id == key[1]) and
                (key[2] is None or fact.direction_id == key[2])
                for fact in relationships
            )
            output.append(_aggregate(dimension, key, values, config, cancellations))
    return tuple(output)


def build_trip_summaries(events: tuple[EventReliabilityResult, ...], config: ReliabilityConfiguration,
                         relationships: tuple[TripRelationshipFact, ...]) -> tuple[TripReliabilityResult, ...]:
    groups: dict[tuple[object, ...], list[EventReliabilityResult]] = defaultdict(list)
    for event in events:
        item = event.observation
        if item.static_snapshot_identifier and item.service_date and item.static_trip_id:
            groups[(item.static_snapshot_identifier, item.service_date, item.static_trip_id,
                    item.static_route_id, item.direction_id, item.event_type)].append(event)
    output = []
    for key, candidates in groups.items():
        eligible = [item for item in candidates if item.eligibility_status == "ELIGIBLE"]
        classified = [item for item in eligible if item.punctuality_classification != "UNCLASSIFIED"]
        ordered = sorted(eligible, key=lambda item: item.observation.static_stop_sequence or -1)
        values = [int(item.selected_delta_seconds) for item in classified if item.selected_delta_seconds is not None]
        canceled = any(fact.service_date == key[1] and fact.static_trip_id == key[2] and
                       fact.schedule_relationship_name == "CANCELED" for fact in relationships)
        output.append(TripReliabilityResult(
            *key, len(eligible), len(classified),
            sum(item.punctuality_classification == "ON_TIME" for item in classified),
            safe_ratio(sum(item.punctuality_classification == "ON_TIME" for item in classified), len(classified)),
            max(values) if values else None, median(values) if values else None,
            percentile(values, .95), ordered[0].observation.static_stop_sequence if ordered else None,
            ordered[-1].observation.static_stop_sequence if ordered else None,
            ordered[0].selected_delta_seconds if ordered else None,
            ordered[-1].selected_delta_seconds if ordered else None,
            (ordered[-1].selected_delta_seconds - ordered[0].selected_delta_seconds)
            if ordered and ordered[0].selected_delta_seconds is not None and ordered[-1].selected_delta_seconds is not None else None,
            any(item.punctuality_classification == "VERY_LATE" for item in classified), canceled,
            "SUFFICIENT_DATA" if len(classified) >= config.minimum_aggregate_denominator else
            ("NOT_APPLICABLE" if not classified else "LOW_SAMPLE"),
        ))
    return tuple(output)


def calculate_coverage(relationships: tuple[TripRelationshipFact, ...],
                       raw_events: tuple[EventReliabilityResult, ...],
                       canonical: tuple[EventReliabilityResult, ...]) -> CoverageMetrics:
    entities = {(item.capture_uuid, item.entity_index) for item in relationships}
    trip_counts = {status: sum(item.match_status == status for item in relationships)
                   for status in ("MATCHED", "UNMATCHED", "AMBIGUOUS", "CONFLICT", "UNSUPPORTED")}
    stop_rows = {(item.observation.capture_uuid, item.observation.entity_index,
                  item.observation.stop_time_update_index) for item in raw_events}
    stop_status = {status: len({(item.observation.capture_uuid, item.observation.entity_index,
                                item.observation.stop_time_update_index) for item in raw_events
                               if item.observation.stop_match_status == status})
                   for status in ("MATCHED", "UNMATCHED", "AMBIGUOUS")}
    comparable = sum(item.selected_delta_seconds is not None for item in canonical)
    classified = sum(item.punctuality_classification != "UNCLASSIFIED" for item in canonical)
    candidates = len(raw_events)
    return CoverageMetrics(
        len(entities), trip_counts["MATCHED"], trip_counts["MATCHED"], trip_counts["UNMATCHED"],
        trip_counts["AMBIGUOUS"], trip_counts["CONFLICT"], trip_counts["UNSUPPORTED"], len(stop_rows),
        stop_status["MATCHED"], stop_status["UNMATCHED"], stop_status["AMBIGUOUS"], comparable,
        classified, len(canonical), candidates, safe_ratio(trip_counts["MATCHED"], len(entities)),
        safe_ratio(stop_status["MATCHED"], len(stop_rows)), safe_ratio(comparable, len(canonical)),
        safe_ratio(classified, comparable), safe_ratio(len(canonical), candidates),
        safe_ratio(candidates, len(canonical)),
    )


def assess_reliability(lineage: MatchRunLineage,
                       observations: tuple[ReliabilityObservation, ...],
                       relationships: tuple[TripRelationshipFact, ...],
                       config: ReliabilityConfiguration) -> ReliabilityAssessment:
    if lineage.realtime_persistence_schema_version != config.required_persistence_schema_version:
        raise ReliabilityError("Matching run has incomplete realtime persistence lineage.")
    if lineage.matching_algorithm_version != config.required_matching_algorithm_version:
        raise ReliabilityError("Matching algorithm version is incompatible with reliability policy.")
    evaluated = tuple(evaluate_observation(item, config) for item in observations)
    canonical = select_canonical_observations(evaluated)
    coverage = calculate_coverage(relationships, evaluated, canonical)
    relationship_counts = tuple(sorted((name, sum((item.schedule_relationship_name or "ABSENT") == name
                                                  for item in relationships))
                                       for name in {item.schedule_relationship_name or "ABSENT" for item in relationships}))
    canceled = sum(item.schedule_relationship_name == "CANCELED" for item in relationships)
    cancellation_denominator = sum((item.schedule_relationship_name or "ABSENT") in
                                   {"ABSENT", "SCHEDULED", "CANCELED", "DUPLICATED"}
                                   for item in relationships)
    findings = tuple(
        ReliabilityFinding("RTCONS001_DELAY_SOURCE_DISAGREEMENT", "CONSISTENCY", "WARN",
                           item.observation.entity_index, item.observation.stop_time_update_index,
                           float(abs(item.observation.consistency_difference_seconds or 0)),
                           float(config.delay_consistency_tolerance_seconds), None, None, "seconds",
                           "Reported and calculated delays exceed the configured tolerance.")
        for item in canonical if item.consistency_finding
    )
    overall = "WARN" if findings else (
        config.incomplete_data_status if coverage.classified_event_count < config.minimum_aggregate_denominator or
        (coverage.trip_matching_ratio is not None and coverage.trip_matching_ratio < config.minimum_matching_coverage_ratio)
        else "INFO")
    return ReliabilityAssessment(
        lineage, len(observations), canonical,
        build_trip_summaries(canonical, config, relationships),
        build_aggregates(canonical, config, relationships), findings, coverage,
        relationship_counts, canceled, cancellation_denominator,
        safe_ratio(canceled, cancellation_denominator), overall,
    )
