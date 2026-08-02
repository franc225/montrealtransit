from __future__ import annotations

import json
import math
import statistics
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable

from parse_gtfs_realtime import ParsedEntity, ParsedFeed


QUALITY_CONFIG_RELATIVE_PATH = Path("config/gtfs_realtime_quality.json")
QUALITY_STATUSES = ("PASS", "WARN", "FAIL", "INFO", "NOT_APPLICABLE")
EXPECTED_ENTITY_TYPE = {
    "vehicle_positions": "vehicle_position",
    "trip_updates": "trip_update",
}
COMPLETENESS_RULE_IDS = {
    "total_entities": "RTC001", "deleted_entities": "RTC002",
    "nondeleted_entities": "RTC003", "vehicle_position_entities": "RTC004",
    "trip_update_entities": "RTC005", "unsupported_entities": "RTC006",
    "expected_entities": "RTC007", "unexpected_entities": "RTC008",
}


class QualityError(RuntimeError):
    """A concise GTFS-Realtime quality-analysis failure."""


@dataclass(frozen=True)
class QualityConfig:
    schema_version: int
    expected_feed_refresh_seconds: float
    maximum_vehicle_position_age_seconds: float
    maximum_trip_update_age_seconds: float
    future_clock_skew_tolerance_seconds: float
    optional_field_thresholds_enabled: bool
    sequence_checks_enabled: bool
    entity_type_expectations: tuple[tuple[str, str], ...]
    enabled_rules: frozenset[str]


@dataclass(frozen=True)
class PreviousCapture:
    capture_uuid: str
    captured_at_utc: datetime
    feed_timestamp_unix: int | None
    payload_sha256: str


@dataclass(frozen=True)
class FreshnessMetrics:
    feed_header_age_seconds: float | None
    entity_ages_seconds: tuple[float, ...]
    minimum_entity_age_seconds: float | None
    maximum_entity_age_seconds: float | None
    mean_entity_age_seconds: float | None
    median_entity_age_seconds: float | None
    p95_entity_age_seconds: float | None
    timestamped_entity_count: int
    eligible_entity_count: int
    timestamped_entity_ratio: float | None
    future_dated_timestamp_count: int
    maximum_future_skew_seconds: float | None
    feed_timestamp_delta_seconds: int | None
    local_capture_interval_seconds: float | None
    repeated_feed_timestamp: bool | None
    payload_repeated: bool | None


@dataclass(frozen=True)
class CompletenessMetric:
    metric_name: str
    numerator: int
    denominator: int
    ratio: float | None


@dataclass(frozen=True)
class QualityResult:
    rule_id: str
    rule_name: str
    category: str
    status: str
    metric_name: str
    value: float | None = None
    numerator: int | None = None
    denominator: int | None = None
    ratio: float | None = None
    threshold: float | None = None
    threshold_operator: str | None = None
    unit: str | None = None
    details: str = ""
    enabled: bool = True
    informational: bool = False


@dataclass(frozen=True)
class QualityAssessment:
    freshness: FreshnessMetrics
    completeness: tuple[CompletenessMetric, ...]
    results: tuple[QualityResult, ...]
    overall_status: str

    @property
    def status_counts(self) -> dict[str, int]:
        return {status: sum(r.status == status for r in self.results) for status in QUALITY_STATUSES}


def load_quality_config(project_root: Path) -> QualityConfig:
    path = project_root / QUALITY_CONFIG_RELATIVE_PATH
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        freshness = raw["freshness"]
        completeness = raw["completeness"]
        sequence = raw["sequence_checks"]
        expectations = raw["entity_type_expectations"]
        enabled_rules = raw["enabled_rules"]
    except (OSError, UnicodeError, json.JSONDecodeError, KeyError, TypeError):
        raise QualityError("GTFS-Realtime quality configuration is invalid.") from None
    if raw.get("schema_version") != 1:
        raise QualityError("Unsupported GTFS-Realtime quality configuration schema version.")
    values = (
        freshness.get("expected_feed_refresh_seconds"),
        freshness.get("maximum_vehicle_position_age_seconds"),
        freshness.get("maximum_trip_update_age_seconds"),
        freshness.get("future_clock_skew_tolerance_seconds"),
    )
    if any(isinstance(v, bool) or not isinstance(v, (int, float)) or v < 0 for v in values):
        raise QualityError("GTFS-Realtime quality freshness thresholds must be nonnegative numbers.")
    if not isinstance(completeness.get("optional_field_thresholds_enabled"), bool):
        raise QualityError("Optional-field threshold policy must be boolean.")
    if not isinstance(sequence.get("enabled"), bool):
        raise QualityError("Sequence-check policy must be boolean.")
    if expectations != EXPECTED_ENTITY_TYPE:
        raise QualityError("GTFS-Realtime entity-type expectations are invalid.")
    if (not isinstance(enabled_rules, list) or not enabled_rules or
            any(not isinstance(rule, str) for rule in enabled_rules)):
        raise QualityError("Enabled GTFS-Realtime quality rules are invalid.")
    return QualityConfig(1, *(float(v) for v in values),
                         completeness["optional_field_thresholds_enabled"], sequence["enabled"],
                         tuple(expectations.items()), frozenset(enabled_rules))


def _percentile(values: tuple[float, ...], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    rank = max(0, math.ceil(percentile * len(ordered)) - 1)
    return ordered[rank]


def calculate_freshness(
    feed: ParsedFeed, config: QualityConfig, previous: PreviousCapture | None = None
) -> FreshnessMetrics:
    captured = feed.captured_at_utc
    header_age = None
    if feed.header.timestamp_utc is not None:
        header_age = (captured - feed.header.timestamp_utc).total_seconds()
    eligible = [entity for entity in feed.entities if not entity.is_deleted]
    ages: list[float] = []
    for entity in eligible:
        timestamp = None
        if entity.vehicle_position is not None:
            timestamp = entity.vehicle_position.timestamp_utc
        elif entity.trip_update is not None:
            timestamp = entity.trip_update.timestamp_utc
        if timestamp is not None:
            ages.append((captured - timestamp).total_seconds())
    age_values = tuple(ages)
    future_skews = tuple(-age for age in age_values if age < 0)
    feed_delta = None
    interval = None
    repeated = None
    payload_repeated = None
    if previous is not None:
        interval = (captured - previous.captured_at_utc).total_seconds()
        payload_repeated = previous.payload_sha256 == feed.payload_sha256
        if previous.feed_timestamp_unix is not None and feed.header.timestamp is not None:
            feed_delta = feed.header.timestamp - previous.feed_timestamp_unix
            repeated = feed_delta == 0
    return FreshnessMetrics(
        header_age, age_values, min(age_values) if age_values else None,
        max(age_values) if age_values else None,
        statistics.fmean(age_values) if age_values else None,
        statistics.median(age_values) if age_values else None,
        _percentile(age_values, 0.95), len(age_values), len(eligible),
        len(age_values) / len(eligible) if eligible else None,
        len(future_skews), max(future_skews) if future_skews else None,
        feed_delta, interval, repeated, payload_repeated,
    )


def _metric(name: str, entities: Iterable[object], predicate: object) -> CompletenessMetric:
    values = tuple(entities)
    numerator = sum(bool(predicate(value)) for value in values)  # type: ignore[operator]
    denominator = len(values)
    return CompletenessMetric(name, numerator, denominator,
                              numerator / denominator if denominator else None)


def calculate_completeness(feed: ParsedFeed) -> tuple[CompletenessMetric, ...]:
    nondeleted = tuple(e for e in feed.entities if not e.is_deleted)
    expected_type = EXPECTED_ENTITY_TYPE[feed.feed_type]
    expected = tuple(e for e in nondeleted if e.entity_type == expected_type)
    metrics = [
        CompletenessMetric("total_entities", len(feed.entities), len(feed.entities), 1.0 if feed.entities else None),
        CompletenessMetric("deleted_entities", sum(e.is_deleted for e in feed.entities), len(feed.entities),
                           sum(e.is_deleted for e in feed.entities) / len(feed.entities) if feed.entities else None),
        CompletenessMetric("nondeleted_entities", len(nondeleted), len(feed.entities),
                           len(nondeleted) / len(feed.entities) if feed.entities else None),
        CompletenessMetric("vehicle_position_entities", sum(e.entity_type == "vehicle_position" for e in nondeleted), len(nondeleted),
                           sum(e.entity_type == "vehicle_position" for e in nondeleted) / len(nondeleted) if nondeleted else None),
        CompletenessMetric("trip_update_entities", sum(e.entity_type == "trip_update" for e in nondeleted), len(nondeleted),
                           sum(e.entity_type == "trip_update" for e in nondeleted) / len(nondeleted) if nondeleted else None),
        CompletenessMetric("unsupported_entities", sum(e.entity_type == "unsupported" for e in nondeleted), len(nondeleted),
                           sum(e.entity_type == "unsupported" for e in nondeleted) / len(nondeleted) if nondeleted else None),
        CompletenessMetric("expected_entities", len(expected), len(nondeleted), len(expected) / len(nondeleted) if nondeleted else None),
        CompletenessMetric("unexpected_entities", len(nondeleted) - len(expected), len(nondeleted),
                           (len(nondeleted) - len(expected)) / len(nondeleted) if nondeleted else None),
    ]
    if feed.feed_type == "vehicle_positions":
        rows = tuple(e for e in expected if e.vehicle_position is not None)
        checks = {
            "entity_id_present": lambda e: bool(e.entity_id),
            "trip_descriptor_present": lambda e: e.vehicle_position.trip is not None,
            "trip_id_present": lambda e: e.vehicle_position.trip is not None and bool(e.vehicle_position.trip.trip_id),
            "route_id_present": lambda e: e.vehicle_position.trip is not None and bool(e.vehicle_position.trip.route_id),
            "direction_id_present": lambda e: e.vehicle_position.trip is not None and e.vehicle_position.trip.direction_id is not None,
            "vehicle_descriptor_present": lambda e: e.vehicle_position.vehicle is not None,
            "vehicle_id_present": lambda e: e.vehicle_position.vehicle is not None and bool(e.vehicle_position.vehicle.vehicle_id),
            "position_present": lambda e: e.vehicle_position.position is not None,
            "valid_coordinates_present": lambda e: e.vehicle_position.position is not None and e.vehicle_position.position.latitude is not None and e.vehicle_position.position.longitude is not None and -90 <= e.vehicle_position.position.latitude <= 90 and -180 <= e.vehicle_position.position.longitude <= 180,
            "bearing_present": lambda e: e.vehicle_position.position is not None and e.vehicle_position.position.bearing is not None,
            "speed_present": lambda e: e.vehicle_position.position is not None and e.vehicle_position.position.speed is not None,
            "current_stop_sequence_present": lambda e: e.vehicle_position.current_stop_sequence is not None,
            "stop_id_present": lambda e: bool(e.vehicle_position.stop_id),
            "vehicle_timestamp_present": lambda e: e.vehicle_position.timestamp is not None,
            "current_status_present": lambda e: e.vehicle_position.current_status is not None,
            "occupancy_status_present": lambda e: e.vehicle_position.occupancy_status is not None,
            "occupancy_percentage_present": lambda e: e.vehicle_position.occupancy_percentage is not None,
        }
    else:
        rows = tuple(e for e in expected if e.trip_update is not None)
        checks = {
            "entity_id_present": lambda e: bool(e.entity_id),
            "trip_descriptor_present": lambda e: e.trip_update.trip is not None,
            "trip_id_present": lambda e: e.trip_update.trip is not None and bool(e.trip_update.trip.trip_id),
            "route_id_present": lambda e: e.trip_update.trip is not None and bool(e.trip_update.trip.route_id),
            "direction_id_present": lambda e: e.trip_update.trip is not None and e.trip_update.trip.direction_id is not None,
            "start_date_present": lambda e: e.trip_update.trip is not None and bool(e.trip_update.trip.start_date),
            "start_time_present": lambda e: e.trip_update.trip is not None and bool(e.trip_update.trip.start_time),
            "vehicle_descriptor_present": lambda e: e.trip_update.vehicle is not None,
            "vehicle_id_present": lambda e: e.trip_update.vehicle is not None and bool(e.trip_update.vehicle.vehicle_id),
            "trip_update_timestamp_present": lambda e: e.trip_update.timestamp is not None,
            "trip_level_delay_present": lambda e: e.trip_update.delay is not None,
            "stop_time_update_present": lambda e: bool(e.trip_update.stop_time_updates),
        }
    metrics.extend(_metric(name, rows, predicate) for name, predicate in checks.items())
    updates = tuple(update for entity in rows for update in entity.trip_update.stop_time_updates) if feed.feed_type == "trip_updates" else ()
    if feed.feed_type == "trip_updates":
        update_checks = {
            "stop_time_stop_sequence_present": lambda u: u.stop_sequence is not None,
            "stop_time_stop_id_present": lambda u: bool(u.stop_id),
            "stop_time_reference_present": lambda u: u.stop_sequence is not None or bool(u.stop_id),
            "stop_time_arrival_present": lambda u: u.arrival is not None,
            "stop_time_departure_present": lambda u: u.departure is not None,
            "stop_time_event_present": lambda u: u.arrival is not None or u.departure is not None,
            "stop_time_arrival_delay_present": lambda u: u.arrival is not None and u.arrival.delay is not None,
            "stop_time_arrival_time_present": lambda u: u.arrival is not None and u.arrival.time is not None,
            "stop_time_departure_delay_present": lambda u: u.departure is not None and u.departure.delay is not None,
            "stop_time_departure_time_present": lambda u: u.departure is not None and u.departure.time is not None,
            "stop_time_schedule_relationship_present": lambda u: u.schedule_relationship is not None,
        }
        metrics.extend(_metric(name, updates, predicate) for name, predicate in update_checks.items())
    return tuple(metrics)


def _result(rule_id: str, name: str, category: str, status: str, metric: str, **kwargs: object) -> QualityResult:
    return QualityResult(rule_id, name, category, status, metric, **kwargs)  # type: ignore[arg-type]


def assess_quality(feed: ParsedFeed, config: QualityConfig, previous: PreviousCapture | None = None) -> QualityAssessment:
    freshness = calculate_freshness(feed, config, previous)
    completeness = calculate_completeness(feed)
    results: list[QualityResult] = []
    header_threshold = config.expected_feed_refresh_seconds
    if freshness.feed_header_age_seconds is None:
        results.append(_result("RTF001", "Feed header timestamp availability", "freshness", "FAIL", "feed_header_age_seconds", details="FeedHeader.timestamp is missing.", enabled="RTF001" in config.enabled_rules))
    else:
        age = freshness.feed_header_age_seconds
        if age < -config.future_clock_skew_tolerance_seconds:
            status = "FAIL"
        elif age > header_threshold:
            status = "WARN"
        else:
            status = "PASS"
        results.append(_result("RTF001", "Feed header age", "freshness", status, "feed_header_age_seconds", value=age, threshold=header_threshold, threshold_operator="<=", unit="seconds", enabled="RTF001" in config.enabled_rules))
    entity_threshold = config.maximum_vehicle_position_age_seconds if feed.feed_type == "vehicle_positions" else config.maximum_trip_update_age_seconds
    max_age = freshness.maximum_entity_age_seconds
    results.append(_result("RTF002", "Maximum timestamped entity age", "freshness", "NOT_APPLICABLE" if max_age is None else ("WARN" if max_age > entity_threshold else "PASS"), "maximum_entity_age_seconds", value=max_age, threshold=entity_threshold, threshold_operator="<=", unit="seconds", enabled="RTF002" in config.enabled_rules))
    max_skew = freshness.maximum_future_skew_seconds
    results.append(_result("RTF003", "Future timestamp tolerance", "freshness", "NOT_APPLICABLE" if max_skew is None else ("FAIL" if max_skew > config.future_clock_skew_tolerance_seconds else "PASS"), "maximum_future_skew_seconds", value=max_skew, threshold=config.future_clock_skew_tolerance_seconds, threshold_operator="<=", unit="seconds", enabled="RTF003" in config.enabled_rules))
    for rule_id, name, value in (("RTS001", "Feed timestamp monotonicity", freshness.feed_timestamp_delta_seconds),):
        status = "NOT_APPLICABLE" if previous is None or value is None else ("FAIL" if value < 0 else "PASS")
        results.append(_result(rule_id, name, "sequence", status, "feed_timestamp_delta_seconds", value=value, unit="seconds", enabled=config.sequence_checks_enabled and rule_id in config.enabled_rules))
    repeated_status = "NOT_APPLICABLE" if previous is None or freshness.repeated_feed_timestamp is None else ("WARN" if freshness.repeated_feed_timestamp else "PASS")
    results.append(_result("RTS002", "Repeated feed timestamp", "sequence", repeated_status, "repeated_feed_timestamp", value=float(freshness.repeated_feed_timestamp) if freshness.repeated_feed_timestamp is not None else None, enabled=config.sequence_checks_enabled and "RTS002" in config.enabled_rules))
    payload_status = "NOT_APPLICABLE" if previous is None or freshness.payload_repeated is None else ("WARN" if freshness.payload_repeated else "PASS")
    results.append(_result("RTS003", "Repeated payload", "sequence", payload_status, "payload_sha256_repeated", value=float(freshness.payload_repeated) if freshness.payload_repeated is not None else None, enabled=config.sequence_checks_enabled and "RTS003" in config.enabled_rules))
    results.append(_result("RTS004", "Local capture interval", "collector", "NOT_APPLICABLE" if previous is None else "INFO", "local_capture_interval_seconds", value=freshness.local_capture_interval_seconds, unit="seconds", informational=True))
    vehicle_index = trip_index = stop_index = 0
    for metric in completeness:
        if metric.metric_name in COMPLETENESS_RULE_IDS:
            rule_id = COMPLETENESS_RULE_IDS[metric.metric_name]
        elif metric.metric_name.startswith("stop_time_"):
            stop_index += 1
            rule_id = f"RTC{300 + stop_index:03d}"
        elif feed.feed_type == "vehicle_positions":
            vehicle_index += 1
            rule_id = f"RTC{100 + vehicle_index:03d}"
        else:
            trip_index += 1
            rule_id = f"RTC{200 + trip_index:03d}"
        status = "NOT_APPLICABLE" if metric.denominator == 0 else "INFO"
        results.append(_result(rule_id, metric.metric_name.replace("_", " ").title(), "completeness", status, metric.metric_name, numerator=metric.numerator, denominator=metric.denominator, ratio=metric.ratio, unit="ratio", enabled=config.optional_field_thresholds_enabled, informational=True))
    enabled_statuses = [r.status for r in results if r.enabled and not r.informational]
    overall = "FAIL" if "FAIL" in enabled_statuses else "WARN" if "WARN" in enabled_statuses else "PASS"
    return QualityAssessment(freshness, completeness, tuple(results), overall)
