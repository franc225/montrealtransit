from __future__ import annotations

import argparse
import hashlib
import http.client
import json
import os
import re
import socket
import sys
import urllib.error
import urllib.request
import uuid
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Mapping

from gtfs_realtime_config import (
    GtfsRealtimeConfig,
    derive_capture_paths,
    load_gtfs_realtime_config,
)


HTTP_METHOD = "GET"
METADATA_SCHEMA_VERSION = 1
STREAM_CHUNK_SIZE = 64 * 1024
ACCEPTED_CONTENT_TYPES = frozenset({"application/x-protobuf"})
REDIRECT_STATUS_CODES = frozenset({301, 302, 303, 307, 308})
CONTENT_LENGTH_PATTERN = re.compile(r"^[0-9]+$")


class CaptureError(RuntimeError):
    """A secret-safe GTFS-Realtime capture failure."""


@dataclass(frozen=True)
class CapturePlan:
    provider: str
    feed_type: str
    endpoint: str
    captured_at_utc: datetime
    capture_uuid: uuid.UUID
    destination_directory: Path
    payload_path: Path
    metadata_path: Path
    request_timeout_seconds: int
    maximum_response_bytes: int

    @property
    def captured_at_iso_utc(self) -> str:
        return self.captured_at_utc.strftime("%Y-%m-%dT%H:%M:%SZ")

    @property
    def filename_timestamp_utc(self) -> str:
        return self.captured_at_utc.strftime("%Y%m%dT%H%M%SZ")


@dataclass(frozen=True)
class CaptureResult:
    provider: str
    feed_type: str
    endpoint: str
    http_status: int
    response_content_type: str
    response_size_bytes: int
    sha256: str
    capture_uuid: str
    captured_at_utc: str
    payload_path: Path
    metadata_path: Path


class NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Reject redirects so credentials are never resent elsewhere."""

    def redirect_request(
        self,
        request: urllib.request.Request,
        file_pointer: object,
        code: int,
        message: str,
        headers: object,
        new_url: str,
    ) -> None:
        return None


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def generate_capture_uuid() -> uuid.UUID:
    return uuid.uuid4()


def open_https_request(
    request: urllib.request.Request,
    timeout: int,
) -> object:
    opener = urllib.request.build_opener(NoRedirectHandler())
    return opener.open(request, timeout=timeout)


def plan_capture(
    config: GtfsRealtimeConfig,
    feed_type: str,
    captured_at: datetime,
    capture_uuid: uuid.UUID,
) -> CapturePlan:
    if captured_at.tzinfo is None or captured_at.utcoffset() is None:
        raise CaptureError("Capture timestamp must include timezone information.")

    captured_at_utc = captured_at.astimezone(timezone.utc).replace(microsecond=0)
    paths = derive_capture_paths(
        config,
        feed_type,
        captured_at_utc,
        capture_uuid,
    )
    configured_storage_root = (
        config.project_root / config.storage_root
    ).resolve()

    for final_path in (paths.payload_path, paths.metadata_path):
        resolved_path = final_path.resolve()

        if not resolved_path.is_relative_to(config.project_root):
            raise CaptureError("Capture destination is outside the project root.")

        if not resolved_path.is_relative_to(configured_storage_root):
            raise CaptureError(
                "Capture destination is outside the configured storage root."
            )

    return CapturePlan(
        provider=config.provider,
        feed_type=feed_type,
        endpoint=config.endpoints[feed_type],
        captured_at_utc=captured_at_utc,
        capture_uuid=capture_uuid,
        destination_directory=paths.directory,
        payload_path=paths.payload_path,
        metadata_path=paths.metadata_path,
        request_timeout_seconds=config.request_timeout_seconds,
        maximum_response_bytes=config.maximum_response_bytes,
    )


def build_request(
    config: GtfsRealtimeConfig,
    plan: CapturePlan,
) -> urllib.request.Request:
    if config.api_key is None:
        raise CaptureError(
            f"Required environment variable "
            f"'{config.api_key_environment_variable}' is missing or blank."
        )

    return urllib.request.Request(
        plan.endpoint,
        headers={
            "Accept": config.accept_header,
            config.authentication_header: config.api_key,
        },
        method=HTTP_METHOD,
    )


def normalize_content_type(content_type: str | None) -> str:
    if content_type is None or not content_type.strip():
        raise CaptureError("Response Content-Type is missing.")

    normalized = content_type.split(";", maxsplit=1)[0].strip().lower()

    if normalized not in ACCEPTED_CONTENT_TYPES:
        raise CaptureError(
            f"Unsupported response Content-Type: {normalized or 'missing'}."
        )

    return normalized


def parse_content_length(
    content_length_values: tuple[str, ...],
    maximum_response_bytes: int,
) -> int | None:
    if not content_length_values:
        return None

    normalized_values = content_length_values

    if any(
        not value or CONTENT_LENGTH_PATTERN.fullmatch(value) is None
        for value in normalized_values
    ):
        raise CaptureError("Response Content-Length is invalid.")

    parsed_values = tuple(int(value) for value in normalized_values)

    if len(set(parsed_values)) != 1:
        raise CaptureError("Response Content-Length values conflict.")

    parsed_length = parsed_values[0]

    if parsed_length == 0:
        raise CaptureError("Response Content-Length must be greater than zero.")

    if parsed_length > maximum_response_bytes:
        raise CaptureError(
            "Response Content-Length exceeds maximum_response_bytes."
        )

    return parsed_length


def write_json_file(path: Path, metadata: Mapping[str, object]) -> None:
    with path.open("x", encoding="utf-8", newline="\n") as file_handle:
        json.dump(
            metadata,
            file_handle,
            indent=2,
            sort_keys=True,
            ensure_ascii=False,
        )
        file_handle.write("\n")
        file_handle.flush()
        os.fsync(file_handle.fileno())


def finalize_without_overwrite(temporary_path: Path, final_path: Path) -> None:
    try:
        os.link(temporary_path, final_path)
    except FileExistsError as error:
        raise CaptureError(f"Capture file already exists: {final_path.name}") from error
    temporary_path.unlink()


def _response_status(response: object) -> int:
    status = getattr(response, "status", None)

    if status is None and hasattr(response, "getcode"):
        status = response.getcode()

    if not isinstance(status, int):
        raise CaptureError("HTTP response status is unavailable.")

    return status


def _header_value(response: object, name: str) -> str | None:
    headers = getattr(response, "headers", None)

    if headers is None or not hasattr(headers, "get"):
        return None

    value = headers.get(name)
    return None if value is None else str(value)


def _header_values(response: object, name: str) -> tuple[str, ...]:
    headers = getattr(response, "headers", None)

    if headers is None:
        return ()

    if hasattr(headers, "get_all"):
        values = headers.get_all(name)

        if values is not None:
            return tuple(str(value) for value in values)

    if not hasattr(headers, "get"):
        return ()

    value = headers.get(name)
    return () if value is None else (str(value),)


def _handle_http_status(status: int) -> None:
    if status == 200:
        return

    if status in REDIRECT_STATUS_CODES:
        raise CaptureError(f"HTTP redirect {status} was rejected.")

    raise CaptureError(f"STM GTFS-Realtime request failed with HTTP status {status}.")


def _project_relative(path: Path, project_root: Path) -> str:
    try:
        return path.resolve().relative_to(project_root).as_posix()
    except ValueError as error:
        raise CaptureError("Metadata path is outside the project root.") from error


def _build_metadata(
    config: GtfsRealtimeConfig,
    plan: CapturePlan,
    response_content_type: str,
    response_size_bytes: int,
    payload_sha256: str,
    content_length: int | None,
) -> dict[str, object]:
    metadata: dict[str, object] = {
        "schema_version": METADATA_SCHEMA_VERSION,
        "provider": plan.provider,
        "feed_type": plan.feed_type,
        "endpoint": plan.endpoint,
        "http_method": HTTP_METHOD,
        "http_status": 200,
        "response_content_type": response_content_type,
        "response_size_bytes": response_size_bytes,
        "sha256": payload_sha256,
        "capture_uuid": str(plan.capture_uuid),
        "captured_at_utc": plan.captured_at_iso_utc,
        "filename_timestamp_utc": plan.filename_timestamp_utc,
        "payload_relative_path": _project_relative(
            plan.payload_path,
            config.project_root,
        ),
        "metadata_relative_path": _project_relative(
            plan.metadata_path,
            config.project_root,
        ),
        "request_timeout_seconds": plan.request_timeout_seconds,
        "maximum_response_bytes": plan.maximum_response_bytes,
    }

    if content_length is not None:
        metadata["response_content_length_header"] = content_length

    return metadata


def capture_once(
    config: GtfsRealtimeConfig,
    feed_type: str,
    *,
    now_provider: Callable[[], datetime] = utc_now,
    uuid_provider: Callable[[], uuid.UUID] = generate_capture_uuid,
    opener: Callable[[urllib.request.Request, int], object] = open_https_request,
    metadata_writer: Callable[[Path, Mapping[str, object]], None] = write_json_file,
    finalizer: Callable[[Path, Path], None] = finalize_without_overwrite,
) -> CaptureResult:
    plan = plan_capture(
        config,
        feed_type,
        now_provider(),
        uuid_provider(),
    )
    request = build_request(config, plan)
    payload_temporary_path = plan.payload_path.with_name(
        f"{plan.payload_path.name}.part-{plan.capture_uuid}"
    )
    metadata_temporary_path = plan.metadata_path.with_name(
        f"{plan.metadata_path.name}.part-{plan.capture_uuid}"
    )

    for path in (
        plan.payload_path,
        plan.metadata_path,
        payload_temporary_path,
        metadata_temporary_path,
    ):
        if path.exists():
            raise CaptureError(f"Capture file already exists: {path.name}")

    try:
        response = opener(request, plan.request_timeout_seconds)
    except urllib.error.HTTPError as error:
        _handle_http_status(error.code)
        raise CaptureError("Unexpected HTTP error.") from None
    except (TimeoutError, socket.timeout):
        raise CaptureError("STM GTFS-Realtime request timed out.") from None
    except (urllib.error.URLError, OSError):
        raise CaptureError(
            "Unable to connect to the STM GTFS-Realtime endpoint."
        ) from None

    payload_finalized = False
    metadata_finalized = False

    try:
        with closing(response):
            status = _response_status(response)
            _handle_http_status(status)
            response_content_type = normalize_content_type(
                _header_value(response, "Content-Type")
            )
            content_length = parse_content_length(
                _header_values(response, "Content-Length"),
                plan.maximum_response_bytes,
            )

            plan.destination_directory.mkdir(parents=True, exist_ok=True)

            for path in (
                plan.payload_path,
                plan.metadata_path,
                payload_temporary_path,
                metadata_temporary_path,
            ):
                if path.exists():
                    raise CaptureError(f"Capture file already exists: {path.name}")

            payload_sha256 = hashlib.sha256()
            response_size_bytes = 0

            with payload_temporary_path.open("xb") as payload_file:
                while True:
                    chunk = response.read(STREAM_CHUNK_SIZE)

                    if not chunk:
                        break

                    if not isinstance(chunk, bytes):
                        raise CaptureError("Response body returned invalid binary data.")

                    response_size_bytes += len(chunk)

                    if response_size_bytes > plan.maximum_response_bytes:
                        raise CaptureError(
                            "Response body exceeds maximum_response_bytes."
                        )

                    payload_file.write(chunk)
                    payload_sha256.update(chunk)

                payload_file.flush()
                os.fsync(payload_file.fileno())

            if response_size_bytes == 0:
                raise CaptureError("Response body is empty.")

            if (
                content_length is not None
                and response_size_bytes != content_length
            ):
                raise CaptureError(
                    "Response body size does not match Content-Length."
                )

            metadata = _build_metadata(
                config=config,
                plan=plan,
                response_content_type=response_content_type,
                response_size_bytes=response_size_bytes,
                payload_sha256=payload_sha256.hexdigest(),
                content_length=content_length,
            )
            metadata_writer(metadata_temporary_path, metadata)

            finalizer(payload_temporary_path, plan.payload_path)
            payload_finalized = True
            finalizer(metadata_temporary_path, plan.metadata_path)
            metadata_finalized = True

        return CaptureResult(
            provider=plan.provider,
            feed_type=plan.feed_type,
            endpoint=plan.endpoint,
            http_status=200,
            response_content_type=response_content_type,
            response_size_bytes=response_size_bytes,
            sha256=payload_sha256.hexdigest(),
            capture_uuid=str(plan.capture_uuid),
            captured_at_utc=plan.captured_at_iso_utc,
            payload_path=plan.payload_path,
            metadata_path=plan.metadata_path,
        )
    except CaptureError:
        raise
    except (TimeoutError, socket.timeout):
        raise CaptureError("STM GTFS-Realtime response timed out.") from None
    except http.client.IncompleteRead:
        raise CaptureError(
            "STM GTFS-Realtime response ended before capture completed."
        ) from None
    except http.client.HTTPException:
        raise CaptureError(
            "STM GTFS-Realtime response failed HTTP protocol validation."
        ) from None
    except OSError:
        raise CaptureError("Unable to persist GTFS-Realtime capture.") from None
    finally:
        for temporary_path in (payload_temporary_path, metadata_temporary_path):
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError:
                pass

        if payload_finalized and not metadata_finalized:
            try:
                plan.payload_path.unlink(missing_ok=True)
            except OSError:
                pass


def print_dry_run(plan: CapturePlan) -> None:
    print("GTFS-Realtime capture dry run")
    print(f"Provider: {plan.provider}")
    print(f"Feed type: {plan.feed_type}")
    print(f"Endpoint: {plan.endpoint}")
    print(f"Destination directory: {plan.destination_directory}")
    print(f"Planned payload: {plan.payload_path.name}")
    print(f"Planned metadata: {plan.metadata_path.name}")
    print(f"Request timeout: {plan.request_timeout_seconds} seconds")
    print(f"Maximum response size: {plan.maximum_response_bytes} bytes")
    print("No network request was made and no files were created.")


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Capture one raw STM GTFS-Realtime feed response."
    )
    parser.add_argument(
        "--feed",
        required=True,
        help="Configured feed type: vehicle_positions or trip_updates.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate and preview one capture without network or filesystem writes.",
    )
    return parser


def run_cli(
    arguments: list[str] | None = None,
    *,
    environment: Mapping[str, str] | None = None,
    now_provider: Callable[[], datetime] = utc_now,
    uuid_provider: Callable[[], uuid.UUID] = generate_capture_uuid,
    opener: Callable[[urllib.request.Request, int], object] = open_https_request,
) -> int:
    parser = build_argument_parser()
    parsed_arguments = parser.parse_args(arguments)

    try:
        config = load_gtfs_realtime_config(environment=environment)
        plan = plan_capture(
            config,
            parsed_arguments.feed,
            now_provider(),
            uuid_provider(),
        )

        if parsed_arguments.dry_run:
            print_dry_run(plan)
            return 0

        result = capture_once(
            config,
            parsed_arguments.feed,
            now_provider=lambda: plan.captured_at_utc,
            uuid_provider=lambda: plan.capture_uuid,
            opener=opener,
        )
        print(
            f"Captured {result.feed_type}: {result.response_size_bytes} bytes "
            f"to {result.payload_path}"
        )
        print(f"Metadata: {result.metadata_path}")
        return 0
    except (CaptureError, ValueError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1


def main() -> None:
    raise SystemExit(run_cli())


if __name__ == "__main__":
    main()
