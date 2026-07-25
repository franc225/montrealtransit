from __future__ import annotations

import copy
import json
import sys
import tempfile
import unittest
import uuid
from datetime import datetime, timezone
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SOURCE_DIRECTORY = REPOSITORY_ROOT / "src"

if str(SOURCE_DIRECTORY) not in sys.path:
    sys.path.insert(0, str(SOURCE_DIRECTORY))

from gtfs_realtime_config import (  # noqa: E402
    derive_capture_paths,
    load_gtfs_realtime_config,
)


class GtfsRealtimeConfigTest(unittest.TestCase):
    API_KEY = "test-secret-that-must-never-appear"

    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.project_root = Path(self.temporary_directory.name)
        self.config_directory = self.project_root / "config"
        self.config_directory.mkdir()
        self.config_path = self.config_directory / "gtfs_realtime.json"
        self.environment = {
            "MONTREAL_TRANSIT_PROJECT_ROOT": str(self.project_root),
            "STM_GTFS_REALTIME_API_KEY": self.API_KEY,
        }
        self.valid_config = {
            "schema_version": 1,
            "provider": "stm",
            "timezone": "America/Montreal",
            "storage_root": "data/raw/gtfs_realtime",
            "allowed_feed_types": [
                "vehicle_positions",
                "trip_updates",
            ],
            "api_key_environment_variable": "STM_GTFS_REALTIME_API_KEY",
            "request_timeout_seconds": 30,
            "maximum_response_bytes": 52428800,
            "endpoints": {
                "vehicle_positions": (
                    "https://placeholder.invalid/vehicle-positions"
                ),
                "trip_updates": "https://placeholder.invalid/trip-updates",
            },
        }
        self.write_config(self.valid_config)

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def write_config(self, config: dict[str, object]) -> None:
        self.config_path.write_text(
            json.dumps(config, indent=2),
            encoding="utf-8",
        )

    def load_config(self):
        return load_gtfs_realtime_config(
            config_path=self.config_path,
            environment=self.environment,
        )

    def assert_config_error(
        self,
        config: dict[str, object],
        expected_message: str,
    ) -> None:
        self.write_config(config)

        with self.assertRaisesRegex(ValueError, expected_message) as context:
            self.load_config()

        self.assertNotIn(self.API_KEY, str(context.exception))

    def test_valid_configuration_loading(self) -> None:
        config = self.load_config()

        self.assertEqual(config.provider, "stm")
        self.assertEqual(config.api_key, self.API_KEY)
        self.assertEqual(
            config.allowed_feed_types,
            ("vehicle_positions", "trip_updates"),
        )

    def test_missing_api_key(self) -> None:
        self.environment.pop("STM_GTFS_REALTIME_API_KEY")

        with self.assertRaisesRegex(
            ValueError,
            "STM_GTFS_REALTIME_API_KEY.*missing or blank",
        ):
            self.load_config()

    def test_blank_api_key(self) -> None:
        self.environment["STM_GTFS_REALTIME_API_KEY"] = "   "

        with self.assertRaisesRegex(
            ValueError,
            "STM_GTFS_REALTIME_API_KEY.*missing or blank",
        ):
            self.load_config()

    def test_api_key_is_redacted_from_repr_and_errors(self) -> None:
        config = self.load_config()
        self.assertNotIn(self.API_KEY, repr(config))

        invalid_config = copy.deepcopy(self.valid_config)
        invalid_config["request_timeout_seconds"] = 0
        self.assert_config_error(
            invalid_config,
            "request_timeout_seconds.*positive integer",
        )

    def test_unsupported_schema_version(self) -> None:
        invalid_config = copy.deepcopy(self.valid_config)
        invalid_config["schema_version"] = 2
        self.assert_config_error(invalid_config, "Unsupported.*schema version")

    def test_malformed_json(self) -> None:
        self.config_path.write_text("{not-json", encoding="utf-8")

        with self.assertRaisesRegex(ValueError, "not valid JSON") as context:
            self.load_config()

        self.assertNotIn(self.API_KEY, str(context.exception))

    def test_invalid_timeout(self) -> None:
        invalid_config = copy.deepcopy(self.valid_config)
        invalid_config["request_timeout_seconds"] = -1
        self.assert_config_error(
            invalid_config,
            "request_timeout_seconds.*positive integer",
        )

    def test_invalid_maximum_response_size(self) -> None:
        invalid_config = copy.deepcopy(self.valid_config)
        invalid_config["maximum_response_bytes"] = 0
        self.assert_config_error(
            invalid_config,
            "maximum_response_bytes.*positive integer",
        )

    def test_unsupported_feed_type_in_configuration(self) -> None:
        invalid_config = copy.deepcopy(self.valid_config)
        invalid_config["allowed_feed_types"] = ["vehicle_positions", "alerts"]
        invalid_config["endpoints"]["alerts"] = "https://placeholder.invalid/alerts"
        self.assert_config_error(invalid_config, "Unsupported.*alerts")

    def test_unsupported_feed_type_in_path_derivation(self) -> None:
        config = self.load_config()

        with self.assertRaisesRegex(ValueError, "Unsupported.*alerts"):
            derive_capture_paths(
                config,
                "alerts",
                datetime(2026, 7, 25, tzinfo=timezone.utc),
                uuid.uuid4(),
            )

    def test_missing_endpoint(self) -> None:
        invalid_config = copy.deepcopy(self.valid_config)
        del invalid_config["endpoints"]["trip_updates"]
        self.assert_config_error(invalid_config, "Missing endpoint.*trip_updates")

    def test_unknown_endpoint_feed_type(self) -> None:
        invalid_config = copy.deepcopy(self.valid_config)
        invalid_config["endpoints"]["alerts"] = "https://placeholder.invalid/alerts"
        self.assert_config_error(invalid_config, "Unknown endpoint.*alerts")

    def test_insecure_http_endpoint(self) -> None:
        invalid_config = copy.deepcopy(self.valid_config)
        invalid_config["endpoints"]["vehicle_positions"] = (
            "http://placeholder.invalid/vehicle-positions"
        )
        self.assert_config_error(
            invalid_config,
            "vehicle_positions.*must use HTTPS",
        )

    def test_absolute_storage_path(self) -> None:
        invalid_config = copy.deepcopy(self.valid_config)
        invalid_config["storage_root"] = "C:\\outside\\realtime"
        self.assert_config_error(invalid_config, "storage_root.*relative")

    def test_storage_path_traversal(self) -> None:
        invalid_config = copy.deepcopy(self.valid_config)
        invalid_config["storage_root"] = "../outside"
        self.assert_config_error(invalid_config, "storage_root.*path traversal")

    def test_safe_storage_path_derivation(self) -> None:
        config = self.load_config()
        capture_id = uuid.UUID("12345678-1234-5678-1234-567812345678")
        captured_at = datetime(
            2026,
            7,
            25,
            12,
            34,
            56,
            123456,
            tzinfo=timezone.utc,
        )

        paths = derive_capture_paths(
            config,
            "vehicle_positions",
            captured_at,
            capture_id,
        )
        expected_directory = (
            self.project_root
            / "data"
            / "raw"
            / "gtfs_realtime"
            / "stm"
            / "vehicle_positions"
            / "2026"
            / "07"
            / "25"
        )
        expected_stem = (
            "20260725T123456123456Z_"
            "12345678-1234-5678-1234-567812345678"
        )

        self.assertEqual(paths.directory, expected_directory)
        self.assertEqual(paths.payload_path, expected_directory / f"{expected_stem}.pb")
        self.assertEqual(
            paths.metadata_path,
            expected_directory / f"{expected_stem}.json",
        )
        self.assertFalse(expected_directory.exists())

    def test_capture_identifier_must_be_a_uuid(self) -> None:
        config = self.load_config()

        with self.assertRaisesRegex(ValueError, "identifier must be a UUID"):
            derive_capture_paths(
                config,
                "vehicle_positions",
                datetime(2026, 7, 25, tzinfo=timezone.utc),
                "../unsafe",
            )

    def test_alternate_project_root(self) -> None:
        config = self.load_config()
        self.assertEqual(config.project_root, self.project_root.resolve())

        paths = derive_capture_paths(
            config,
            "trip_updates",
            datetime(2026, 7, 25, tzinfo=timezone.utc),
            uuid.uuid4(),
        )
        self.assertTrue(paths.directory.is_relative_to(self.project_root))

    def test_configuration_loading_creates_no_directories_or_files(self) -> None:
        paths_before = sorted(
            path.relative_to(self.project_root)
            for path in self.project_root.rglob("*")
        )

        self.load_config()

        paths_after = sorted(
            path.relative_to(self.project_root)
            for path in self.project_root.rglob("*")
        )
        self.assertEqual(paths_after, paths_before)

    def test_static_warehouse_and_report_remain_unchanged(self) -> None:
        warehouse_path = (
            self.project_root
            / "data"
            / "warehouse"
            / "montreal_transit.duckdb"
        )
        report_path = self.project_root / "docs" / "index.html"
        warehouse_path.parent.mkdir(parents=True)
        report_path.parent.mkdir(parents=True)
        warehouse_content = b"synthetic-duckdb-marker"
        report_content = b"<html>synthetic report marker</html>"
        warehouse_path.write_bytes(warehouse_content)
        report_path.write_bytes(report_content)

        config = self.load_config()
        derive_capture_paths(
            config,
            "vehicle_positions",
            datetime(2026, 7, 25, tzinfo=timezone.utc),
            uuid.uuid4(),
        )

        self.assertEqual(warehouse_path.read_bytes(), warehouse_content)
        self.assertEqual(report_path.read_bytes(), report_content)


if __name__ == "__main__":
    unittest.main()
