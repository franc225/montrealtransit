from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Mapping

from google.protobuf.message import DecodeError
from google.transit import gtfs_realtime_pb2

from gtfs_realtime_config import GtfsRealtimeConfig, load_gtfs_realtime_config


SUPPORTED_METADATA_SCHEMA_VERSION = 1
SUPPORTED_PROVIDER = "stm"
SUPPORTED_FEED_TYPES = frozenset({"vehicle_positions", "trip_updates"})
SUPPORTED_GTFS_REALTIME_VERSIONS = frozenset({"2.0"})
PROTOBUF_CONTENT_TYPES = frozenset(
    {
        "application/x-protobuf",
        "application/protobuf",
        "application/vnd.google.protobuf",
    }
)
CAPTURE_FILENAME_PATTERN = re.compile(
    r"^(?P<timestamp>\d{8}T\d{6}Z)_"
    r"(?P<uuid>[0-9a-fA-F-]{36})\.(?P<extension>pb|json)$"
)
START_DATE_PATTERN = re.compile(r"^\d{8}$")


class ParserError(RuntimeError):
    """A concise, secret-safe GTFS-Realtime parsing failure."""


@dataclass(frozen=True)
class ValidationFinding:
    code: str
    message: str
    entity_index: int | None = None


@dataclass(frozen=True)
class ParsedFeedHeader:
    gtfs_realtime_version: str
    incrementality: int | None
    incrementality_name: str | None
    timestamp: int | None
    timestamp_utc: datetime | None


@dataclass(frozen=True)
class ParsedTripDescriptor:
    trip_id: str | None
    route_id: str | None
    direction_id: int | None
    start_time: str | None
    start_date: str | None
    schedule_relationship: int | None
    schedule_relationship_name: str | None


@dataclass(frozen=True)
class ParsedVehicleDescriptor:
    vehicle_id: str | None
    label: str | None
    license_plate: str | None


@dataclass(frozen=True)
class ParsedPosition:
    latitude: float | None
    longitude: float | None
    bearing: float | None
    odometer: float | None
    speed: float | None


@dataclass(frozen=True)
class ParsedStopTimeEvent:
    delay: int | None
    time: int | None
    time_utc: datetime | None
    uncertainty: int | None
    scheduled_time: int | None
    scheduled_time_utc: datetime | None


@dataclass(frozen=True)
class ParsedStopTimeUpdate:
    stop_sequence: int | None
    stop_id: str | None
    arrival: ParsedStopTimeEvent | None
    departure: ParsedStopTimeEvent | None
    schedule_relationship: int | None
    schedule_relationship_name: str | None


@dataclass(frozen=True)
class ParsedVehiclePosition:
    trip: ParsedTripDescriptor | None
    vehicle: ParsedVehicleDescriptor | None
    position: ParsedPosition | None
    current_stop_sequence: int | None
    stop_id: str | None
    current_status: int | None
    current_status_name: str | None
    timestamp: int | None
    timestamp_utc: datetime | None
    congestion_level: int | None
    congestion_level_name: str | None
    occupancy_status: int | None
    occupancy_status_name: str | None
    occupancy_percentage: int | None


@dataclass(frozen=True)
class ParsedTripUpdate:
    trip: ParsedTripDescriptor | None
    vehicle: ParsedVehicleDescriptor | None
    timestamp: int | None
    timestamp_utc: datetime | None
    delay: int | None
    stop_time_updates: tuple[ParsedStopTimeUpdate, ...]


@dataclass(frozen=True)
class ParsedEntity:
    original_index: int
    entity_id: str
    is_deleted: bool
    entity_type: str
    vehicle_position: ParsedVehiclePosition | None
    trip_update: ParsedTripUpdate | None


@dataclass(frozen=True)
class ParseSummary:
    entity_count: int
    deleted_entity_count: int
    vehicle_position_count: int
    trip_update_count: int
    unsupported_entity_count: int
    entities_missing_business_identifiers: int
    validation_finding_count: int


@dataclass(frozen=True)
class ParsedFeed:
    capture_uuid: str
    provider: str
    feed_type: str
    payload_relative_path: str
    captured_at_utc: datetime
    payload_size_bytes: int
    payload_sha256: str
    sha256_verified: bool
    header: ParsedFeedHeader
    entities: tuple[ParsedEntity, ...]
    findings: tuple[ValidationFinding, ...]
    summary: ParseSummary


@dataclass(frozen=True)
class ValidatedCapture:
    capture_uuid: str
    provider: str
    feed_type: str
    payload_path: Path
    metadata_path: Path
    payload_relative_path: str
    metadata_relative_path: str
    captured_at_utc: datetime
    payload_size_bytes: int
    payload_sha256: str


def _require_metadata_mapping(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        raise ParserError("Capture metadata root must be a JSON object.")
    return value


def _metadata_string(metadata: Mapping[str, object], field: str) -> str:
    value = metadata.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ParserError(f"Capture metadata field '{field}' must be nonblank.")
    return value


def _safe_relative_path(value: str, field: str) -> Path:
    posix_path = PurePosixPath(value)
    windows_path = PureWindowsPath(value)
    if (
        posix_path.is_absolute()
        or windows_path.is_absolute()
        or ".." in posix_path.parts
        or ".." in windows_path.parts
    ):
        raise ParserError(f"Capture metadata field '{field}' is not a safe relative path.")
    return Path(*posix_path.parts)


def _ensure_inside(path: Path, root: Path, label: str) -> Path:
    resolved = path.resolve()
    if not resolved.is_relative_to(root.resolve()):
        raise ParserError(f"{label} is outside the configured raw storage root.")
    return resolved


def _parse_utc_datetime(value: str, field: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise ParserError(f"Capture metadata field '{field}' is not a valid datetime.") from None
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise ParserError(f"Capture metadata field '{field}' must be UTC.")
    return parsed.astimezone(timezone.utc)


def _filename_parts(path: Path, expected_extension: str) -> tuple[str, uuid.UUID]:
    match = CAPTURE_FILENAME_PATTERN.fullmatch(path.name)
    if match is None or match.group("extension") != expected_extension:
        raise ParserError(f"Capture {expected_extension} filename convention is invalid.")
    try:
        capture_uuid = uuid.UUID(match.group("uuid"))
    except ValueError:
        raise ParserError("Capture filename UUID is invalid.") from None
    return match.group("timestamp"), capture_uuid


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file_handle:
        for chunk in iter(lambda: file_handle.read(64 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate_capture(
    config: GtfsRealtimeConfig,
    payload_path: Path,
    metadata_path: Path | None = None,
) -> ValidatedCapture:
    selected_payload = payload_path.resolve()
    selected_metadata = (metadata_path or payload_path.with_suffix(".json")).resolve()
    storage_root = (config.project_root / config.storage_root).resolve()
    selected_payload = _ensure_inside(selected_payload, storage_root, "Payload path")
    selected_metadata = _ensure_inside(selected_metadata, storage_root, "Metadata path")

    if not selected_payload.is_file():
        raise ParserError("GTFS-Realtime payload file does not exist or is not a regular file.")
    if not selected_metadata.is_file():
        raise ParserError("GTFS-Realtime metadata file does not exist or is not a regular file.")

    try:
        metadata = _require_metadata_mapping(
            json.loads(selected_metadata.read_text(encoding="utf-8"))
        )
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise ParserError("GTFS-Realtime metadata is not valid UTF-8 JSON.") from None

    if metadata.get("schema_version") != SUPPORTED_METADATA_SCHEMA_VERSION:
        raise ParserError("Unsupported capture metadata schema version.")
    provider = _metadata_string(metadata, "provider")
    if provider != SUPPORTED_PROVIDER:
        raise ParserError("Capture metadata provider is unsupported.")
    feed_type = _metadata_string(metadata, "feed_type")
    if feed_type not in SUPPORTED_FEED_TYPES:
        raise ParserError("Capture metadata feed type is unsupported.")
    if metadata.get("http_status") != 200:
        raise ParserError("Capture metadata HTTP status must be 200.")
    content_type = _metadata_string(metadata, "response_content_type").split(";", 1)[0].lower()
    if content_type not in PROTOBUF_CONTENT_TYPES:
        raise ParserError("Capture metadata response content type is not protobuf-compatible.")

    payload_relative_text = _metadata_string(metadata, "payload_relative_path")
    metadata_relative_text = _metadata_string(metadata, "metadata_relative_path")
    expected_payload = (config.project_root / _safe_relative_path(
        payload_relative_text, "payload_relative_path"
    )).resolve()
    expected_metadata = (config.project_root / _safe_relative_path(
        metadata_relative_text, "metadata_relative_path"
    )).resolve()
    if expected_payload != selected_payload:
        raise ParserError("Capture metadata payload path does not match the selected payload.")
    if expected_metadata != selected_metadata:
        raise ParserError("Capture metadata path does not match the selected metadata file.")

    payload_size = selected_payload.stat().st_size
    size_value = metadata.get("response_size_bytes")
    if isinstance(size_value, bool) or not isinstance(size_value, int) or size_value != payload_size:
        raise ParserError("Capture metadata payload size does not match the payload file.")
    expected_sha256 = _metadata_string(metadata, "sha256").lower()
    if not re.fullmatch(r"[0-9a-f]{64}", expected_sha256):
        raise ParserError("Capture metadata SHA-256 is invalid.")
    actual_sha256 = _sha256_file(selected_payload)
    if actual_sha256 != expected_sha256:
        raise ParserError("Capture metadata SHA-256 does not match the payload file.")

    try:
        metadata_uuid = uuid.UUID(_metadata_string(metadata, "capture_uuid"))
    except ValueError:
        raise ParserError("Capture metadata UUID is invalid.") from None
    captured_at = _parse_utc_datetime(
        _metadata_string(metadata, "captured_at_utc"), "captured_at_utc"
    )
    filename_timestamp = _metadata_string(metadata, "filename_timestamp_utc")
    expected_timestamp = captured_at.strftime("%Y%m%dT%H%M%SZ")
    if filename_timestamp != expected_timestamp:
        raise ParserError("Capture metadata filename timestamp does not match captured_at_utc.")
    payload_timestamp, payload_uuid = _filename_parts(selected_payload, "pb")
    metadata_timestamp, filename_metadata_uuid = _filename_parts(selected_metadata, "json")
    if payload_timestamp != expected_timestamp or metadata_timestamp != expected_timestamp:
        raise ParserError("Capture filename timestamp does not match capture metadata.")
    if payload_uuid != metadata_uuid or filename_metadata_uuid != metadata_uuid:
        raise ParserError("Capture filename UUID does not match capture metadata.")

    return ValidatedCapture(
        capture_uuid=str(metadata_uuid),
        provider=provider,
        feed_type=feed_type,
        payload_path=selected_payload,
        metadata_path=selected_metadata,
        payload_relative_path=selected_payload.relative_to(config.project_root).as_posix(),
        metadata_relative_path=selected_metadata.relative_to(config.project_root).as_posix(),
        captured_at_utc=captured_at,
        payload_size_bytes=payload_size,
        payload_sha256=actual_sha256,
    )


def _optional(message: object, field: str) -> object | None:
    return getattr(message, field) if message.HasField(field) else None


def _enum_name(message: object, field: str, value: int | None) -> str | None:
    if value is None:
        return None
    enum = message.DESCRIPTOR.fields_by_name[field].enum_type
    enum_value = enum.values_by_number.get(value)
    return enum_value.name if enum_value is not None else f"UNKNOWN_{value}"


def _unix_utc(value: int, label: str) -> datetime:
    try:
        return datetime.fromtimestamp(value, timezone.utc)
    except (OverflowError, OSError, ValueError):
        raise ParserError(f"{label} is outside the supported Unix timestamp range.") from None


def _timestamp(message: object, field: str, label: str) -> tuple[int | None, datetime | None]:
    value = _optional(message, field)
    if value is None:
        return None, None
    integer_value = int(value)
    return integer_value, _unix_utc(integer_value, label)


def _trip_descriptor(message: object) -> ParsedTripDescriptor | None:
    if message is None:
        return None
    relationship = _optional(message, "schedule_relationship")
    relationship_value = int(relationship) if relationship is not None else None
    return ParsedTripDescriptor(
        trip_id=_optional(message, "trip_id"),
        route_id=_optional(message, "route_id"),
        direction_id=_optional(message, "direction_id"),
        start_time=_optional(message, "start_time"),
        start_date=_optional(message, "start_date"),
        schedule_relationship=relationship_value,
        schedule_relationship_name=_enum_name(
            message, "schedule_relationship", relationship_value
        ),
    )


def _vehicle_descriptor(message: object) -> ParsedVehicleDescriptor | None:
    if message is None:
        return None
    return ParsedVehicleDescriptor(
        vehicle_id=_optional(message, "id"),
        label=_optional(message, "label"),
        license_plate=_optional(message, "license_plate"),
    )


def _position(message: object) -> ParsedPosition | None:
    if message is None:
        return None
    return ParsedPosition(
        latitude=_optional(message, "latitude"),
        longitude=_optional(message, "longitude"),
        bearing=_optional(message, "bearing"),
        odometer=_optional(message, "odometer"),
        speed=_optional(message, "speed"),
    )


def _stop_event(message: object, label: str) -> ParsedStopTimeEvent | None:
    if message is None:
        return None
    event_time, event_time_utc = _timestamp(message, "time", f"{label} time")
    scheduled_time, scheduled_time_utc = _timestamp(
        message, "scheduled_time", f"{label} scheduled_time"
    )
    return ParsedStopTimeEvent(
        delay=_optional(message, "delay"),
        time=event_time,
        time_utc=event_time_utc,
        uncertainty=_optional(message, "uncertainty"),
        scheduled_time=scheduled_time,
        scheduled_time_utc=scheduled_time_utc,
    )


def _stop_update(message: object) -> ParsedStopTimeUpdate:
    relationship = _optional(message, "schedule_relationship")
    relationship_value = int(relationship) if relationship is not None else None
    return ParsedStopTimeUpdate(
        stop_sequence=_optional(message, "stop_sequence"),
        stop_id=_optional(message, "stop_id"),
        arrival=_stop_event(
            message.arrival if message.HasField("arrival") else None, "arrival event"
        ),
        departure=_stop_event(
            message.departure if message.HasField("departure") else None,
            "departure event",
        ),
        schedule_relationship=relationship_value,
        schedule_relationship_name=_enum_name(
            message, "schedule_relationship", relationship_value
        ),
    )


def _vehicle_position(message: object) -> ParsedVehiclePosition:
    timestamp, timestamp_utc = _timestamp(message, "timestamp", "Vehicle timestamp")
    current_status = _optional(message, "current_status")
    congestion = _optional(message, "congestion_level")
    occupancy = _optional(message, "occupancy_status")
    return ParsedVehiclePosition(
        trip=_trip_descriptor(message.trip if message.HasField("trip") else None),
        vehicle=_vehicle_descriptor(
            message.vehicle if message.HasField("vehicle") else None
        ),
        position=_position(message.position if message.HasField("position") else None),
        current_stop_sequence=_optional(message, "current_stop_sequence"),
        stop_id=_optional(message, "stop_id"),
        current_status=int(current_status) if current_status is not None else None,
        current_status_name=_enum_name(message, "current_status", current_status),
        timestamp=timestamp,
        timestamp_utc=timestamp_utc,
        congestion_level=int(congestion) if congestion is not None else None,
        congestion_level_name=_enum_name(message, "congestion_level", congestion),
        occupancy_status=int(occupancy) if occupancy is not None else None,
        occupancy_status_name=_enum_name(message, "occupancy_status", occupancy),
        occupancy_percentage=_optional(message, "occupancy_percentage"),
    )


def _trip_update(message: object) -> ParsedTripUpdate:
    timestamp, timestamp_utc = _timestamp(message, "timestamp", "Trip Update timestamp")
    return ParsedTripUpdate(
        trip=_trip_descriptor(message.trip if message.HasField("trip") else None),
        vehicle=_vehicle_descriptor(
            message.vehicle if message.HasField("vehicle") else None
        ),
        timestamp=timestamp,
        timestamp_utc=timestamp_utc,
        delay=_optional(message, "delay"),
        stop_time_updates=tuple(_stop_update(item) for item in message.stop_time_update),
    )


def _business_findings(entity: ParsedEntity) -> list[ValidationFinding]:
    findings = []
    supported = entity.vehicle_position or entity.trip_update
    if supported is not None and (supported.trip is None or not supported.trip.trip_id):
        findings.append(
            ValidationFinding(
                "MISSING_TRIP_ID",
                "Supported entity is missing a trip identifier.",
                entity.original_index,
            )
        )
    trip = supported.trip if supported is not None else None
    if trip is not None and trip.start_date is not None and not START_DATE_PATTERN.fullmatch(trip.start_date):
        findings.append(
            ValidationFinding(
                "INVALID_START_DATE",
                "Trip start_date must use YYYYMMDD format.",
                entity.original_index,
            )
        )
    if entity.vehicle_position is not None:
        position = entity.vehicle_position.position
        if position is not None and position.latitude is not None and not -90 <= position.latitude <= 90:
            findings.append(ValidationFinding("INVALID_LATITUDE", "Latitude is outside -90 to 90.", entity.original_index))
        if position is not None and position.longitude is not None and not -180 <= position.longitude <= 180:
            findings.append(ValidationFinding("INVALID_LONGITUDE", "Longitude is outside -180 to 180.", entity.original_index))
        sequence = entity.vehicle_position.current_stop_sequence
        if sequence is not None and sequence < 0:
            findings.append(ValidationFinding("INVALID_STOP_SEQUENCE", "Stop sequence must be nonnegative.", entity.original_index))
    if entity.trip_update is not None:
        for update in entity.trip_update.stop_time_updates:
            if update.stop_sequence is not None and update.stop_sequence < 0:
                findings.append(ValidationFinding("INVALID_STOP_SEQUENCE", "Stop sequence must be nonnegative.", entity.original_index))
    return findings


def decode_feed(payload: bytes, capture: ValidatedCapture) -> ParsedFeed:
    if not payload:
        raise ParserError("GTFS-Realtime protobuf payload is empty.")
    message = gtfs_realtime_pb2.FeedMessage()
    try:
        message.ParseFromString(payload)
    except DecodeError:
        raise ParserError("GTFS-Realtime protobuf payload is malformed or truncated.") from None

    if not message.header.HasField("gtfs_realtime_version") or not message.header.gtfs_realtime_version.strip():
        raise ParserError("GTFS-Realtime header version is missing or blank.")
    version = message.header.gtfs_realtime_version
    if version not in SUPPORTED_GTFS_REALTIME_VERSIONS:
        raise ParserError(f"Unsupported GTFS-Realtime version: {version}.")
    incrementality = _optional(message.header, "incrementality")
    header_timestamp, header_timestamp_utc = _timestamp(
        message.header, "timestamp", "Feed timestamp"
    )
    header = ParsedFeedHeader(
        gtfs_realtime_version=version,
        incrementality=int(incrementality) if incrementality is not None else None,
        incrementality_name=_enum_name(
            message.header, "incrementality", incrementality
        ),
        timestamp=header_timestamp,
        timestamp_utc=header_timestamp_utc,
    )

    identifiers: set[str] = set()
    for index, source in enumerate(message.entity):
        if not source.HasField("id") or not source.id.strip():
            raise ParserError(f"GTFS-Realtime entity at index {index} has a missing or blank ID.")
        if source.id in identifiers:
            raise ParserError(f"GTFS-Realtime entity ID is duplicated: {source.id}.")
        identifiers.add(source.id)
    if not message.IsInitialized():
        raise ParserError("GTFS-Realtime payload is missing required protobuf fields.")

    identifiers.clear()
    entities: list[ParsedEntity] = []
    findings: list[ValidationFinding] = []
    for index, source in enumerate(message.entity):
        if not source.HasField("id") or not source.id.strip():
            raise ParserError(f"GTFS-Realtime entity at index {index} has a missing or blank ID.")
        if source.id in identifiers:
            raise ParserError(f"GTFS-Realtime entity ID is duplicated: {source.id}.")
        identifiers.add(source.id)
        vehicle = _vehicle_position(source.vehicle) if source.HasField("vehicle") else None
        trip_update = _trip_update(source.trip_update) if source.HasField("trip_update") else None
        entity_types = []
        if vehicle is not None:
            entity_types.append("vehicle_position")
        if trip_update is not None:
            entity_types.append("trip_update")
        entity_type = "+".join(entity_types) if entity_types else "unsupported"
        entity = ParsedEntity(
            original_index=index,
            entity_id=source.id,
            is_deleted=bool(source.is_deleted) if source.HasField("is_deleted") else False,
            entity_type=entity_type,
            vehicle_position=vehicle,
            trip_update=trip_update,
        )
        entities.append(entity)
        findings.extend(_business_findings(entity))

    expected_type = "vehicle_position" if capture.feed_type == "vehicle_positions" else "trip_update"
    for entity in entities:
        if entity.entity_type != "unsupported" and expected_type not in entity.entity_type:
            findings.append(
                ValidationFinding(
                    "FEED_TYPE_MISMATCH",
                    f"Entity type {entity.entity_type} differs from metadata feed type {capture.feed_type}.",
                    entity.original_index,
                )
            )

    missing_identifier_entities = {
        finding.entity_index
        for finding in findings
        if finding.code == "MISSING_TRIP_ID"
    }
    summary = ParseSummary(
        entity_count=len(entities),
        deleted_entity_count=sum(entity.is_deleted for entity in entities),
        vehicle_position_count=sum(entity.vehicle_position is not None for entity in entities),
        trip_update_count=sum(entity.trip_update is not None for entity in entities),
        unsupported_entity_count=sum(entity.entity_type == "unsupported" for entity in entities),
        entities_missing_business_identifiers=len(missing_identifier_entities),
        validation_finding_count=len(findings),
    )
    return ParsedFeed(
        capture_uuid=capture.capture_uuid,
        provider=capture.provider,
        feed_type=capture.feed_type,
        payload_relative_path=capture.payload_relative_path,
        captured_at_utc=capture.captured_at_utc,
        payload_size_bytes=capture.payload_size_bytes,
        payload_sha256=capture.payload_sha256,
        sha256_verified=True,
        header=header,
        entities=tuple(entities),
        findings=tuple(findings),
        summary=summary,
    )


def parse_capture(
    payload_path: Path,
    metadata_path: Path | None = None,
    *,
    environment: Mapping[str, str] | None = None,
) -> ParsedFeed:
    config = load_gtfs_realtime_config(
        environment=environment, validate_credentials=False
    )
    capture = validate_capture(config, payload_path, metadata_path)
    try:
        payload = capture.payload_path.read_bytes()
    except OSError:
        raise ParserError("Unable to read GTFS-Realtime payload.") from None
    return decode_feed(payload, capture)


def print_summary(feed: ParsedFeed) -> None:
    values = (
        ("Provider", feed.provider),
        ("Feed type", feed.feed_type),
        ("Payload", feed.payload_relative_path),
        ("Captured at UTC", feed.captured_at_utc.isoformat().replace("+00:00", "Z")),
        ("GTFS-Realtime version", feed.header.gtfs_realtime_version),
        ("Feed timestamp", feed.header.timestamp),
        ("Incrementality", feed.header.incrementality_name),
        ("Entities", feed.summary.entity_count),
        ("Deleted entities", feed.summary.deleted_entity_count),
        ("Vehicle Positions", feed.summary.vehicle_position_count),
        ("Trip Updates", feed.summary.trip_update_count),
        ("Unsupported entities", feed.summary.unsupported_entity_count),
        ("Entities missing business identifiers", feed.summary.entities_missing_business_identifiers),
        ("Validation findings", feed.summary.validation_finding_count),
        ("Payload size", feed.payload_size_bytes),
        ("SHA-256 verified", "yes" if feed.sha256_verified else "no"),
    )
    print("GTFS-Realtime parse summary")
    for label, value in values:
        print(f"{label}: {value if value is not None else 'not provided'}")


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Validate and parse one captured GTFS-Realtime protobuf payload."
    )
    parser.add_argument("--payload", required=True, type=Path, help="Path to a captured .pb payload.")
    parser.add_argument("--metadata", type=Path, default=None, help="Optional matching metadata JSON path.")
    parser.add_argument("--summary", action="store_true", help="Print the concise summary (default behavior).")
    return parser


def run_cli(
    arguments: list[str] | None = None,
    *,
    environment: Mapping[str, str] | None = None,
) -> int:
    parsed_arguments = build_argument_parser().parse_args(arguments)
    try:
        feed = parse_capture(
            parsed_arguments.payload,
            parsed_arguments.metadata,
            environment=environment,
        )
        print_summary(feed)
        return 0
    except (ParserError, ValueError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1


def main() -> None:
    raise SystemExit(run_cli())


if __name__ == "__main__":
    main()
