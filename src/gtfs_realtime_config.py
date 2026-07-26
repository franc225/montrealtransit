from __future__ import annotations

import argparse
import json
import os
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Mapping
from urllib.parse import urlparse


DEFAULT_PROJECT_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT_ENVIRONMENT_VARIABLE = "MONTREAL_TRANSIT_PROJECT_ROOT"
SUPPORTED_SCHEMA_VERSION = 1
SUPPORTED_PROVIDER = "stm"
SUPPORTED_TIMEZONE = "America/Montreal"
SUPPORTED_FEED_TYPES = frozenset({"vehicle_positions", "trip_updates"})
SUPPORTED_API_KEY_ENVIRONMENT_VARIABLE = "STM_GTFS_REALTIME_API_KEY"
SUPPORTED_AUTHENTICATION_HEADER = "apiKey"
SUPPORTED_ACCEPT_HEADER = "application/x-protobuf"
SUPPORTED_ENDPOINT_HOST = "api.stm.info"
ENVIRONMENT_VARIABLE_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


@dataclass(frozen=True)
class GtfsRealtimeConfig:
    project_root: Path
    schema_version: int
    provider: str
    timezone: str
    storage_root: Path
    allowed_feed_types: tuple[str, ...]
    api_key_environment_variable: str
    authentication_header: str
    accept_header: str
    request_timeout_seconds: int
    maximum_response_bytes: int
    endpoints: Mapping[str, str]
    api_key: str | None = field(repr=False)


@dataclass(frozen=True)
class CapturePaths:
    directory: Path
    payload_path: Path
    metadata_path: Path


def resolve_project_root(
    environment: Mapping[str, str] | None = None,
) -> Path:
    environment_values = os.environ if environment is None else environment
    configured_root = environment_values.get(PROJECT_ROOT_ENVIRONMENT_VARIABLE)
    return Path(configured_root or DEFAULT_PROJECT_ROOT).resolve()


def _require_mapping(value: object, field_name: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise ValueError(f"Configuration field '{field_name}' must be an object.")
    return value


def _require_positive_integer(value: object, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(
            f"Configuration field '{field_name}' must be a positive integer."
        )
    return value


def _validate_storage_root(value: object, project_root: Path) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("Configuration field 'storage_root' must be nonblank.")

    storage_text = value.strip()
    windows_path = PureWindowsPath(storage_text)
    posix_path = PurePosixPath(storage_text)

    if windows_path.is_absolute() or posix_path.is_absolute():
        raise ValueError("Configuration field 'storage_root' must be relative.")

    if ".." in windows_path.parts or ".." in posix_path.parts:
        raise ValueError(
            "Configuration field 'storage_root' must not contain path traversal."
        )

    storage_root = Path(storage_text)
    resolved_storage_root = (project_root / storage_root).resolve()

    if not resolved_storage_root.is_relative_to(project_root):
        raise ValueError(
            "Configuration field 'storage_root' must remain under the project root."
        )

    return storage_root


def _validate_feed_types(value: object) -> tuple[str, ...]:
    if not isinstance(value, list) or not value:
        raise ValueError(
            "Configuration field 'allowed_feed_types' must be a nonempty list."
        )

    if any(not isinstance(feed_type, str) or not feed_type for feed_type in value):
        raise ValueError(
            "Configuration field 'allowed_feed_types' must contain nonblank strings."
        )

    if len(value) != len(set(value)):
        raise ValueError(
            "Configuration field 'allowed_feed_types' must contain unique values."
        )

    unsupported = sorted(set(value) - SUPPORTED_FEED_TYPES)

    if unsupported:
        raise ValueError(
            "Unsupported GTFS-Realtime feed type: " + ", ".join(unsupported)
        )

    return tuple(value)


def _validate_endpoints(
    value: object,
    allowed_feed_types: tuple[str, ...],
) -> dict[str, str]:
    endpoints = _require_mapping(value, "endpoints")
    endpoint_feed_types = set(endpoints)
    allowed_feed_type_set = set(allowed_feed_types)
    missing = sorted(allowed_feed_type_set - endpoint_feed_types)
    unknown = sorted(endpoint_feed_types - allowed_feed_type_set)

    if missing:
        raise ValueError("Missing endpoint for feed type: " + ", ".join(missing))

    if unknown:
        raise ValueError("Unknown endpoint feed type: " + ", ".join(unknown))

    validated_endpoints = {}

    for feed_type, endpoint in endpoints.items():
        if not isinstance(endpoint, str) or not endpoint:
            raise ValueError(f"Endpoint for '{feed_type}' must be a nonblank URL.")

        if any(character.isspace() for character in endpoint):
            raise ValueError(f"Endpoint for '{feed_type}' must not contain whitespace.")

        parsed_endpoint = urlparse(endpoint)

        if parsed_endpoint.scheme.lower() != "https" or not parsed_endpoint.netloc:
            raise ValueError(f"Endpoint for '{feed_type}' must use HTTPS.")

        if parsed_endpoint.hostname != SUPPORTED_ENDPOINT_HOST:
            raise ValueError(
                f"Endpoint for '{feed_type}' must use host "
                f"'{SUPPORTED_ENDPOINT_HOST}'."
            )

        if parsed_endpoint.username is not None or parsed_endpoint.password is not None:
            raise ValueError(
                f"Endpoint for '{feed_type}' must not contain credentials."
            )

        if not parsed_endpoint.path or parsed_endpoint.path == "/":
            raise ValueError(f"Endpoint for '{feed_type}' must contain a path.")

        if parsed_endpoint.query:
            raise ValueError(
                f"Endpoint for '{feed_type}' must not contain query parameters."
            )

        if parsed_endpoint.fragment:
            raise ValueError(f"Endpoint for '{feed_type}' must not contain a fragment.")

        validated_endpoints[feed_type] = endpoint

    return validated_endpoints


def load_gtfs_realtime_config(
    config_path: Path | None = None,
    environment: Mapping[str, str] | None = None,
    validate_credentials: bool = True,
) -> GtfsRealtimeConfig:
    environment_values = os.environ if environment is None else environment
    project_root = resolve_project_root(environment_values)
    selected_config_path = (
        config_path
        if config_path is not None
        else project_root / "config" / "gtfs_realtime.json"
    )

    try:
        config_data = json.loads(selected_config_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise ValueError(
            f"GTFS-Realtime configuration is not valid JSON: {selected_config_path}"
        ) from error
    except OSError as error:
        raise ValueError(
            f"Unable to read GTFS-Realtime configuration: {selected_config_path}"
        ) from error

    config = _require_mapping(config_data, "root")
    schema_version = config.get("schema_version")

    if schema_version != SUPPORTED_SCHEMA_VERSION:
        raise ValueError(
            "Unsupported GTFS-Realtime configuration schema version: "
            f"{schema_version!r}."
        )

    provider = config.get("provider")

    if provider != SUPPORTED_PROVIDER:
        raise ValueError(f"Configuration field 'provider' must be '{SUPPORTED_PROVIDER}'.")

    configured_timezone = config.get("timezone")

    if configured_timezone != SUPPORTED_TIMEZONE:
        raise ValueError(
            f"Configuration field 'timezone' must be '{SUPPORTED_TIMEZONE}'."
        )

    storage_root = _validate_storage_root(config.get("storage_root"), project_root)
    allowed_feed_types = _validate_feed_types(config.get("allowed_feed_types"))
    endpoints = _validate_endpoints(config.get("endpoints"), allowed_feed_types)
    api_key_environment_variable = config.get("api_key_environment_variable")

    if (
        not isinstance(api_key_environment_variable, str)
        or not ENVIRONMENT_VARIABLE_PATTERN.fullmatch(api_key_environment_variable)
    ):
        raise ValueError(
            "Configuration field 'api_key_environment_variable' must be a valid "
            "environment-variable name."
        )

    if api_key_environment_variable != SUPPORTED_API_KEY_ENVIRONMENT_VARIABLE:
        raise ValueError(
            "Configuration field 'api_key_environment_variable' must be "
            f"'{SUPPORTED_API_KEY_ENVIRONMENT_VARIABLE}'."
        )

    authentication_header = config.get("authentication_header")

    if authentication_header != SUPPORTED_AUTHENTICATION_HEADER:
        raise ValueError(
            "Configuration field 'authentication_header' must be "
            f"'{SUPPORTED_AUTHENTICATION_HEADER}'."
        )

    accept_header = config.get("accept_header")

    if accept_header != SUPPORTED_ACCEPT_HEADER:
        raise ValueError(
            f"Configuration field 'accept_header' must be '{SUPPORTED_ACCEPT_HEADER}'."
        )

    api_key = environment_values.get(api_key_environment_variable)

    if validate_credentials and (api_key is None or not api_key.strip()):
        raise ValueError(
            f"Required environment variable '{api_key_environment_variable}' "
            "is missing or blank."
        )

    return GtfsRealtimeConfig(
        project_root=project_root,
        schema_version=schema_version,
        provider=provider,
        timezone=configured_timezone,
        storage_root=storage_root,
        allowed_feed_types=allowed_feed_types,
        api_key_environment_variable=api_key_environment_variable,
        authentication_header=authentication_header,
        accept_header=accept_header,
        request_timeout_seconds=_require_positive_integer(
            config.get("request_timeout_seconds"),
            "request_timeout_seconds",
        ),
        maximum_response_bytes=_require_positive_integer(
            config.get("maximum_response_bytes"),
            "maximum_response_bytes",
        ),
        endpoints=endpoints,
        api_key=api_key,
    )


def derive_capture_paths(
    config: GtfsRealtimeConfig,
    feed_type: str,
    captured_at: datetime,
    capture_id: uuid.UUID,
) -> CapturePaths:
    if feed_type not in config.allowed_feed_types:
        raise ValueError(f"Unsupported GTFS-Realtime feed type: {feed_type}")

    if not isinstance(capture_id, uuid.UUID):
        raise ValueError("Capture identifier must be a UUID.")

    if captured_at.tzinfo is None or captured_at.utcoffset() is None:
        raise ValueError("Capture timestamp must include timezone information.")

    captured_at_utc = captured_at.astimezone(timezone.utc)
    timestamp = captured_at_utc.strftime("%Y%m%dT%H%M%SZ")
    filename_stem = f"{timestamp}_{capture_id}"
    directory = (
        config.project_root
        / config.storage_root
        / config.provider
        / feed_type
        / captured_at_utc.strftime("%Y")
        / captured_at_utc.strftime("%m")
        / captured_at_utc.strftime("%d")
    ).resolve()
    configured_storage_root = (
        config.project_root / config.storage_root
    ).resolve()

    if not directory.is_relative_to(configured_storage_root):
        raise ValueError("Derived capture path is outside the configured storage root.")

    return CapturePaths(
        directory=directory,
        payload_path=directory / f"{filename_stem}.pb",
        metadata_path=directory / f"{filename_stem}.json",
    )


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Validate local STM GTFS-Realtime configuration without making "
            "network requests or creating storage paths."
        )
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        help="Optional path to the nonsecret GTFS-Realtime JSON configuration.",
    )
    parser.add_argument(
        "--skip-credential-validation",
        action="store_true",
        help=(
            "Validate nonsecret configuration without requiring the API-key "
            "environment variable."
        ),
    )
    return parser.parse_args()


def main() -> None:
    arguments = parse_arguments()
    config = load_gtfs_realtime_config(
        arguments.config,
        validate_credentials=not arguments.skip_credential_validation,
    )
    print(
        "GTFS-Realtime configuration is valid for "
        f"{config.provider} ({', '.join(config.allowed_feed_types)})."
    )
    print("No network request was made and no storage path was created.")


if __name__ == "__main__":
    main()
