from __future__ import annotations

import json
import re
from dataclasses import dataclass, replace
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

import pytz
from pytz.exceptions import AmbiguousTimeError, NonExistentTimeError


MATCH_STATUSES = (
    "MATCHED", "UNMATCHED", "AMBIGUOUS", "CONFLICT", "NOT_APPLICABLE",
    "UNSUPPORTED", "NO_STATIC_COVERAGE", "INCOMPLETE_LINEAGE",
)
TIME_PATTERN = re.compile(r"^(\d+):([0-5]\d):([0-5]\d)$")


class MatchingError(RuntimeError):
    """A concise, secret-safe scheduled-service matching failure."""


@dataclass(frozen=True)
class MatchingConfig:
    schema_version: int
    matching_algorithm_version: str
    timezone_name: str
    required_persistence_schema_version: int
    service_date_lookback_days: int
    exact_trip_id_enabled: bool
    composite_fallback_enabled: bool
    require_route_consistency: bool
    require_direction_consistency: bool
    require_start_time_consistency: bool
    frequency_trip_policy: str


@dataclass(frozen=True)
class StaticSnapshot:
    snapshot_identifier: str
    ingestion_run_id: str
    feed_version: str | None
    service_start_date: date | None
    service_end_date: date | None


@dataclass(frozen=True)
class CalendarService:
    service_id: str
    weekdays: tuple[bool, bool, bool, bool, bool, bool, bool]
    start_date: date
    end_date: date


@dataclass(frozen=True)
class CalendarException:
    service_id: str
    service_date: date
    exception_type: int


@dataclass(frozen=True)
class StaticStopTime:
    trip_id: str
    stop_sequence: int
    stop_id: str
    arrival_time: str | None
    departure_time: str | None


@dataclass(frozen=True)
class StaticTripCandidate:
    trip_id: str
    route_id: str
    service_id: str
    direction_id: int | None
    shape_id: str | None
    start_time: str | None
    frequency_based: bool = False


@dataclass(frozen=True)
class RealtimeStopFact:
    update_index: int
    stop_sequence: int | None
    stop_id: str | None
    schedule_relationship: int | None
    schedule_relationship_name: str | None
    arrival_delay: int | None
    arrival_time_utc: datetime | None
    departure_delay: int | None
    departure_time_utc: datetime | None


@dataclass(frozen=True)
class RealtimeEntityFact:
    capture_uuid: str
    entity_index: int
    entity_id: str
    entity_type: str
    is_deleted: bool
    persistence_schema_version: int | None
    trip_id: str | None
    route_id: str | None
    direction_id: int | None
    start_time: str | None
    start_date: str | None
    schedule_relationship: int | None
    schedule_relationship_name: str | None
    vehicle_id: str | None
    trip_delay: int | None
    stop_updates: tuple[RealtimeStopFact, ...] = ()


@dataclass(frozen=True)
class ServiceDateResolution:
    service_date: date | None
    source: str
    candidate_count: int
    status: str
    details: str


@dataclass(frozen=True)
class ScheduledTime:
    original: str | None
    service_seconds: int | None
    service_day_offset: int | None
    local_datetime: datetime | None
    utc_datetime: datetime | None
    resolution_status: str


@dataclass(frozen=True)
class ScheduleComparison:
    reported_delay: int | None
    absolute_delta: int | None
    delta_source: str
    consistency_difference: int | None


@dataclass(frozen=True)
class StopTimeMatchResult:
    update_index: int
    realtime_stop_sequence: int | None
    realtime_stop_id: str | None
    static_stop_sequence: int | None
    static_stop_id: str | None
    status: str
    method: str
    conflict_code: str | None
    details: str
    arrival: ScheduledTime
    departure: ScheduledTime
    arrival_comparison: ScheduleComparison
    departure_comparison: ScheduleComparison
    realtime_schedule_relationship: int | None
    realtime_schedule_relationship_name: str | None
    realtime_arrival_utc: datetime | None
    realtime_departure_utc: datetime | None


@dataclass(frozen=True)
class TripMatchResult:
    entity_index: int
    entity_id: str
    entity_type: str
    status: str
    method: str
    service_date: date | None
    service_date_source: str
    service_date_candidate_count: int
    candidate_count: int
    static_trip: StaticTripCandidate | None
    conflict_code: str | None
    details: str
    relationship_treatment: str
    stop_matches: tuple[StopTimeMatchResult, ...]


@dataclass(frozen=True)
class MatchAssessment:
    capture_uuid: str
    snapshot: StaticSnapshot
    results: tuple[TripMatchResult, ...]
    overall_status: str

    @property
    def status_counts(self) -> dict[str, int]:
        return {status: sum(item.status == status for item in self.results) for status in MATCH_STATUSES}


def load_matching_config(project_root: Path) -> MatchingConfig:
    path = project_root / "config" / "gtfs_realtime_matching.json"
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise MatchingError("GTFS-Realtime matching configuration is invalid.") from None
    expected = {
        "schema_version": int, "matching_algorithm_version": str, "timezone": str,
        "required_persistence_schema_version": int, "service_date_lookback_days": int,
        "exact_trip_id_enabled": bool, "composite_fallback_enabled": bool,
        "require_route_consistency": bool, "require_direction_consistency": bool,
        "require_start_time_consistency": bool, "frequency_trip_policy": str,
    }
    if any(key not in raw or not isinstance(raw[key], kind) for key, kind in expected.items()):
        raise MatchingError("GTFS-Realtime matching configuration fields are invalid.")
    if raw["schema_version"] != 1 or raw["timezone"] != "America/Montreal":
        raise MatchingError("Unsupported GTFS-Realtime matching configuration.")
    if raw["service_date_lookback_days"] < 0 or raw["frequency_trip_policy"] != "UNSUPPORTED":
        raise MatchingError("GTFS-Realtime matching policy is invalid.")
    return MatchingConfig(
        raw["schema_version"], raw["matching_algorithm_version"], raw["timezone"],
        raw["required_persistence_schema_version"], raw["service_date_lookback_days"],
        raw["exact_trip_id_enabled"], raw["composite_fallback_enabled"],
        raw["require_route_consistency"], raw["require_direction_consistency"],
        raw["require_start_time_consistency"], raw["frequency_trip_policy"],
    )


def parse_service_date(value: str) -> date:
    if not re.fullmatch(r"\d{8}", value):
        raise MatchingError("Realtime start_date must use YYYYMMDD.")
    try:
        return datetime.strptime(value, "%Y%m%d").date()
    except ValueError:
        raise MatchingError("Realtime start_date is not a valid calendar date.") from None


def parse_gtfs_time(value: str | None, service_date: date | None,
                    timezone_name: str = "America/Montreal") -> ScheduledTime:
    if value is None:
        return ScheduledTime(None, None, None, None, None, "UNAVAILABLE")
    match = TIME_PATTERN.fullmatch(value)
    if match is None:
        raise MatchingError(f"Malformed scheduled GTFS time: {value!r}.")
    hours, minutes, seconds = (int(part) for part in match.groups())
    service_seconds = hours * 3600 + minutes * 60 + seconds
    offset, clock_seconds = divmod(service_seconds, 86400)
    if service_date is None:
        return ScheduledTime(value, service_seconds, offset, None, None, "NO_SERVICE_DATE")
    local_naive = datetime.combine(service_date + timedelta(days=offset), time()) + timedelta(seconds=clock_seconds)
    zone = pytz.timezone(timezone_name)
    try:
        local = zone.localize(local_naive, is_dst=None)
    except AmbiguousTimeError:
        return ScheduledTime(value, service_seconds, offset, None, None, "DST_AMBIGUOUS")
    except NonExistentTimeError:
        return ScheduledTime(value, service_seconds, offset, None, None, "DST_NONEXISTENT")
    return ScheduledTime(value, service_seconds, offset, local, local.astimezone(timezone.utc), "RESOLVED")


def service_is_active(service_id: str, service_date: date,
                      calendars: tuple[CalendarService, ...],
                      exceptions: tuple[CalendarException, ...]) -> bool:
    exception = next((item for item in exceptions if item.service_id == service_id and item.service_date == service_date), None)
    if exception is not None:
        return exception.exception_type == 1
    calendar = next((item for item in calendars if item.service_id == service_id), None)
    return bool(calendar and calendar.start_date <= service_date <= calendar.end_date and calendar.weekdays[service_date.weekday()])


def _snapshot_covers(snapshot: StaticSnapshot, service_date: date) -> bool:
    return not ((snapshot.service_start_date and service_date < snapshot.service_start_date) or
                (snapshot.service_end_date and service_date > snapshot.service_end_date))


def _candidate_dates(entity: RealtimeEntityFact, captured_at_utc: datetime,
                     config: MatchingConfig) -> tuple[date, ...]:
    if entity.start_date:
        return (parse_service_date(entity.start_date),)
    local_date = captured_at_utc.astimezone(pytz.timezone(config.timezone_name)).date()
    return tuple(local_date - timedelta(days=days) for days in range(config.service_date_lookback_days + 1))


def _comparison(reported: int | None, actual: datetime | None,
                scheduled: ScheduledTime) -> ScheduleComparison:
    calculated = None
    if actual is not None and scheduled.utc_datetime is not None:
        calculated = round((actual - scheduled.utc_datetime).total_seconds())
    source = "REPORTED_DELAY" if reported is not None else "ABSOLUTE_EVENT_TIME" if calculated is not None else "UNAVAILABLE"
    difference = reported - calculated if reported is not None and calculated is not None else None
    return ScheduleComparison(reported, calculated, source, difference)


def match_stop_updates(updates: tuple[RealtimeStopFact, ...], parent: TripMatchResult,
                       stop_times: tuple[StaticStopTime, ...], config: MatchingConfig) -> tuple[StopTimeMatchResult, ...]:
    results: list[StopTimeMatchResult] = []
    trip_stops = tuple(row for row in stop_times if parent.static_trip and row.trip_id == parent.static_trip.trip_id)
    for update in updates:
        static = None
        status, method, conflict, details = "UNMATCHED", "NO_CANDIDATE", None, "No usable stop reference."
        if parent.status != "MATCHED":
            status, method, details = "NOT_APPLICABLE", "PARENT_NOT_MATCHED", "Parent trip is not matched."
        elif update.stop_sequence is not None:
            candidates = tuple(row for row in trip_stops if row.stop_sequence == update.stop_sequence)
            if len(candidates) == 1:
                static = candidates[0]
                if update.stop_id and update.stop_id != static.stop_id:
                    status, method, conflict, details = "CONFLICT", "STOP_SEQUENCE", "STOP_ID_CONFLICT", "Stop sequence and stop ID disagree."
                else:
                    status, method, details = "MATCHED", "STOP_SEQUENCE", "Matched by stop sequence."
            else:
                details = "Stop sequence is absent from the static trip."
        elif update.stop_id:
            candidates = tuple(row for row in trip_stops if row.stop_id == update.stop_id)
            if len(candidates) == 1:
                static, status, method, details = candidates[0], "MATCHED", "UNIQUE_STOP_ID", "Matched by unique stop ID."
            elif len(candidates) > 1:
                status, method, details = "AMBIGUOUS", "MULTIPLE_CANDIDATES", "Stop ID occurs more than once in the static trip."
            else:
                details = "Stop ID is absent from the static trip."
        arrival = parse_gtfs_time(static.arrival_time if static else None, parent.service_date, config.timezone_name)
        departure = parse_gtfs_time(static.departure_time if static else None, parent.service_date, config.timezone_name)
        results.append(StopTimeMatchResult(
            update.update_index, update.stop_sequence, update.stop_id,
            static.stop_sequence if static else None, static.stop_id if static else None,
            status, method, conflict, details, arrival, departure,
            _comparison(update.arrival_delay, update.arrival_time_utc, arrival),
            _comparison(update.departure_delay, update.departure_time_utc, departure),
            update.schedule_relationship, update.schedule_relationship_name,
            update.arrival_time_utc, update.departure_time_utc,
        ))
    return tuple(results)


def match_entity(entity: RealtimeEntityFact, captured_at_utc: datetime,
                 snapshot: StaticSnapshot, trips: tuple[StaticTripCandidate, ...],
                 calendars: tuple[CalendarService, ...], exceptions: tuple[CalendarException, ...],
                 stop_times: tuple[StaticStopTime, ...], config: MatchingConfig) -> TripMatchResult:
    base = dict(entity_index=entity.entity_index, entity_id=entity.entity_id,
                entity_type=entity.entity_type, static_trip=None, stop_matches=())
    def with_unmatched_stops(result: TripMatchResult) -> TripMatchResult:
        return replace(result, stop_matches=match_stop_updates(
            entity.stop_updates, result, stop_times, config
        ))
    if entity.persistence_schema_version != config.required_persistence_schema_version:
        return TripMatchResult(**base, status="INCOMPLETE_LINEAGE", method="INCOMPLETE_PERSISTENCE_SCHEMA", service_date=None, service_date_source="UNRESOLVED", service_date_candidate_count=0, candidate_count=0, conflict_code="INCOMPLETE_PERSISTENCE_SCHEMA", details="Complete persistence schema is required.", relationship_treatment="INCOMPLETE_LINEAGE")
    if entity.is_deleted:
        return with_unmatched_stops(TripMatchResult(**base, status="NOT_APPLICABLE", method="DELETED_ENTITY", service_date=None, service_date_source="NOT_APPLICABLE", service_date_candidate_count=0, candidate_count=0, conflict_code=None, details="Deleted entities are not business-matched.", relationship_treatment="DELETED"))
    relationship = entity.schedule_relationship_name or "ABSENT"
    if relationship in {"ADDED", "UNSCHEDULED", "NEW"}:
        return with_unmatched_stops(TripMatchResult(**base, status="NOT_APPLICABLE", method="ADDED_TRIP", service_date=None, service_date_source="NOT_APPLICABLE", service_date_candidate_count=0, candidate_count=0, conflict_code=None, details="Added or unscheduled trip may lack a static counterpart.", relationship_treatment="ADDED_OR_UNSCHEDULED"))
    supported_relationships = {"ABSENT", "SCHEDULED", "CANCELED", "DUPLICATED"}
    if relationship not in supported_relationships:
        return with_unmatched_stops(TripMatchResult(**base, status="UNSUPPORTED", method="UNSUPPORTED_RELATIONSHIP", service_date=None, service_date_source="UNRESOLVED", service_date_candidate_count=0, candidate_count=0, conflict_code="UNSUPPORTED_RELATIONSHIP", details="Schedule relationship is unsupported.", relationship_treatment="UNSUPPORTED"))
    dates = _candidate_dates(entity, captured_at_utc, config)
    if entity.start_date and not _snapshot_covers(snapshot, dates[0]):
        return with_unmatched_stops(TripMatchResult(**base, status="NO_STATIC_COVERAGE", method="NO_CANDIDATE", service_date=dates[0], service_date_source="EXPLICIT_START_DATE", service_date_candidate_count=1, candidate_count=0, conflict_code="NO_STATIC_COVERAGE", details="Static snapshot does not cover the service date.", relationship_treatment=relationship))
    pool = tuple(trip for trip in trips if entity.trip_id and trip.trip_id == entity.trip_id)
    method = "EXACT_TRIP_ID_AND_SERVICE_DATE" if entity.start_date else "EXACT_TRIP_ID"
    if not entity.trip_id:
        if not config.composite_fallback_enabled:
            return with_unmatched_stops(TripMatchResult(**base, status="UNMATCHED", method="NO_CANDIDATE", service_date=None, service_date_source="UNRESOLVED", service_date_candidate_count=len(dates), candidate_count=0, conflict_code="FALLBACK_DISABLED", details="Composite fallback is disabled.", relationship_treatment=relationship))
        if entity.route_id is None or entity.direction_id is None or entity.start_time is None:
            return with_unmatched_stops(TripMatchResult(**base, status="UNMATCHED", method="NO_CANDIDATE", service_date=None, service_date_source="UNRESOLVED", service_date_candidate_count=len(dates), candidate_count=0, conflict_code="MISSING_COMPOSITE_IDENTITY", details="Composite identity is incomplete.", relationship_treatment=relationship))
        pool = tuple(trip for trip in trips if trip.route_id == entity.route_id and trip.direction_id == entity.direction_id and trip.start_time == entity.start_time)
        method = "UNIQUE_COMPOSITE"
    elif not pool:
        return with_unmatched_stops(TripMatchResult(**base, status="UNMATCHED", method="NO_CANDIDATE", service_date=None, service_date_source="UNRESOLVED", service_date_candidate_count=len(dates), candidate_count=0, conflict_code="TRIP_ID_NOT_FOUND", details="Realtime trip ID is absent from static data.", relationship_treatment=relationship))
    active = tuple((trip, day) for trip in pool for day in dates if _snapshot_covers(snapshot, day) and service_is_active(trip.service_id, day, calendars, exceptions))
    if not active:
        return with_unmatched_stops(TripMatchResult(**base, status="UNMATCHED", method="NO_CANDIDATE", service_date=dates[0] if len(dates) == 1 else None, service_date_source="EXPLICIT_START_DATE" if entity.start_date else "INFERRED", service_date_candidate_count=0, candidate_count=len(pool), conflict_code="INACTIVE_SERVICE", details="Static trip is not active on a candidate service date.", relationship_treatment=relationship))
    if len(active) > 1:
        return with_unmatched_stops(TripMatchResult(**base, status="AMBIGUOUS", method="MULTIPLE_CANDIDATES", service_date=None, service_date_source="AMBIGUOUS", service_date_candidate_count=len({day for _, day in active}), candidate_count=len(active), conflict_code="MULTIPLE_SERVICE_INSTANCES", details="More than one active trip instance remains.", relationship_treatment=relationship))
    trip, service_date = active[0]
    if trip.frequency_based:
        return with_unmatched_stops(TripMatchResult(**base, status="UNSUPPORTED", method="FREQUENCY_UNSUPPORTED", service_date=service_date, service_date_source="EXPLICIT_START_DATE" if entity.start_date else "INFERRED", service_date_candidate_count=1, candidate_count=1, conflict_code="FREQUENCY_UNSUPPORTED", details="Frequency-instance matching is not implemented.", relationship_treatment=relationship))
    conflicts = []
    if config.require_route_consistency and entity.route_id and entity.route_id != trip.route_id:
        conflicts.append("ROUTE_CONFLICT")
    if config.require_direction_consistency and entity.direction_id is not None and entity.direction_id != trip.direction_id:
        conflicts.append("DIRECTION_CONFLICT")
    if config.require_start_time_consistency and entity.start_time and trip.start_time and entity.start_time != trip.start_time:
        conflicts.append("START_TIME_CONFLICT")
    if conflicts:
        return with_unmatched_stops(TripMatchResult(**base, status="CONFLICT", method=method, service_date=service_date, service_date_source="EXPLICIT_START_DATE" if entity.start_date else "INFERRED", service_date_candidate_count=1, candidate_count=1, conflict_code="+".join(conflicts), details="Trip descriptor conflicts with static data.", relationship_treatment=relationship))
    parent = TripMatchResult(**{**base, "static_trip": trip}, status="MATCHED", method=method, service_date=service_date, service_date_source="EXPLICIT_START_DATE" if entity.start_date else "INFERRED_LOCAL_DATE", service_date_candidate_count=1, candidate_count=1, conflict_code=None, details="Matched to active scheduled service.", relationship_treatment=relationship)
    return replace(parent, stop_matches=match_stop_updates(entity.stop_updates, parent, stop_times, config))


def assess_matches(capture_uuid: str, captured_at_utc: datetime, snapshot: StaticSnapshot,
                   entities: tuple[RealtimeEntityFact, ...], trips: tuple[StaticTripCandidate, ...],
                   calendars: tuple[CalendarService, ...], exceptions: tuple[CalendarException, ...],
                   stop_times: tuple[StaticStopTime, ...], config: MatchingConfig) -> MatchAssessment:
    results = tuple(match_entity(entity, captured_at_utc, snapshot, trips, calendars, exceptions, stop_times, config) for entity in entities)
    statuses = {item.status for item in results}
    overall = "CONFLICT" if "CONFLICT" in statuses or "INCOMPLETE_LINEAGE" in statuses else "AMBIGUOUS" if "AMBIGUOUS" in statuses else "COMPLETE"
    return MatchAssessment(capture_uuid, snapshot, results, overall)
