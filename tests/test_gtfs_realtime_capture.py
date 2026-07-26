from __future__ import annotations

import hashlib
import http.client
import io
import json
import socket
import sys
import tempfile
import unittest
import urllib.error
import urllib.request
import urllib.response
import uuid
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import replace
from datetime import datetime, timezone
from email.message import Message
from pathlib import Path
from unittest.mock import patch


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SOURCE_DIRECTORY = REPOSITORY_ROOT / "src"

if str(SOURCE_DIRECTORY) not in sys.path:
    sys.path.insert(0, str(SOURCE_DIRECTORY))

from capture_gtfs_realtime import (  # noqa: E402
    CaptureError,
    NoRedirectHandler,
    capture_once,
    finalize_without_overwrite,
    open_https_request,
    plan_capture,
    run_cli,
)
from gtfs_realtime_config import load_gtfs_realtime_config  # noqa: E402


OFFICIAL_ENDPOINTS = {
    "vehicle_positions": (
        "https://api.stm.info/pub/od/gtfs-rt/ic/v2/vehiclePositions"
    ),
    "trip_updates": "https://api.stm.info/pub/od/gtfs-rt/ic/v2/tripUpdates",
}
REAL_BUILD_OPENER = urllib.request.build_opener


class FakeResponse:
    def __init__(
        self,
        payload: bytes = b"\x0a\x03STM\x10\x01",
        *,
        status: int = 200,
        content_type: str | None = "application/x-protobuf",
        content_length: str | None = None,
        read_error_after: int | None = None,
        read_exception: Exception | None = None,
    ) -> None:
        self.payload = payload
        self.status = status
        self.headers: dict[str, str] = {}
        self.read_error_after = read_error_after
        self.read_exception = read_exception
        self.read_calls = 0
        self.offset = 0
        self.closed = False

        if content_type is not None:
            self.headers["Content-Type"] = content_type

        if content_length is not None:
            self.headers["Content-Length"] = content_length

    def read(self, size: int) -> bytes:
        if self.read_exception is not None:
            raise self.read_exception

        if (
            self.read_error_after is not None
            and self.read_calls >= self.read_error_after
        ):
            raise OSError("synthetic read failure")

        self.read_calls += 1

        if self.offset >= len(self.payload):
            return b""

        chunk = self.payload[self.offset : self.offset + size]
        self.offset += len(chunk)
        return chunk

    def close(self) -> None:
        self.closed = True


class RecordingOpener:
    def __init__(
        self,
        response: FakeResponse | None = None,
        error: Exception | None = None,
    ) -> None:
        self.response = response or FakeResponse()
        self.error = error
        self.calls: list[tuple[object, int]] = []

    def __call__(self, request: object, timeout: int) -> FakeResponse:
        self.calls.append((request, timeout))

        if self.error is not None:
            raise self.error

        return self.response


class GtfsRealtimeCaptureTest(unittest.TestCase):
    API_KEY = "test-api-key-not-a-real-secret"
    CAPTURE_TIME = datetime(2026, 7, 25, 14, 30, tzinfo=timezone.utc)
    CAPTURE_UUID = uuid.UUID("12345678-1234-5678-1234-567812345678")
    PAYLOAD = b"\x0a\x03STM\x10\x01\xff\x00"

    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.project_root = Path(self.temporary_directory.name)
        config_directory = self.project_root / "config"
        config_directory.mkdir()
        self.config_path = config_directory / "gtfs_realtime.json"
        self.config_path.write_text(
            (REPOSITORY_ROOT / "config" / "gtfs_realtime.json").read_text(
                encoding="utf-8"
            ),
            encoding="utf-8",
        )
        self.environment = {
            "MONTREAL_TRANSIT_PROJECT_ROOT": str(self.project_root),
            "STM_GTFS_REALTIME_API_KEY": self.API_KEY,
        }
        self.config = load_gtfs_realtime_config(
            self.config_path,
            self.environment,
        )
        self.network_tripwire = patch(
            "urllib.request.build_opener",
            side_effect=AssertionError("Real network transport is forbidden in tests."),
        )
        self.network_tripwire.start()

    def tearDown(self) -> None:
        self.network_tripwire.stop()
        self.temporary_directory.cleanup()

    def fixed_now(self) -> datetime:
        return self.CAPTURE_TIME

    def fixed_uuid(self) -> uuid.UUID:
        return self.CAPTURE_UUID

    def capture(
        self,
        feed_type: str = "vehicle_positions",
        *,
        response: FakeResponse | None = None,
        opener: RecordingOpener | None = None,
        config=None,
        metadata_writer=None,
        finalizer=None,
    ):
        selected_opener = opener or RecordingOpener(
            response or FakeResponse(self.PAYLOAD)
        )
        arguments = {
            "now_provider": self.fixed_now,
            "uuid_provider": self.fixed_uuid,
            "opener": selected_opener,
        }

        if metadata_writer is not None:
            arguments["metadata_writer"] = metadata_writer

        if finalizer is not None:
            arguments["finalizer"] = finalizer

        result = capture_once(
            config or self.config,
            feed_type,
            **arguments,
        )
        return result, selected_opener

    def capture_plan(self, feed_type: str = "vehicle_positions"):
        return plan_capture(
            self.config,
            feed_type,
            self.CAPTURE_TIME,
            self.CAPTURE_UUID,
        )

    def assert_no_capture_files(self) -> None:
        storage_root = (
            self.project_root / "data" / "raw" / "gtfs_realtime"
        )
        files = list(storage_root.rglob("*")) if storage_root.exists() else []
        self.assertFalse([path for path in files if path.is_file()])

    def test_dry_run_for_each_feed_is_network_and_filesystem_free(self) -> None:
        for feed_type in ("vehicle_positions", "trip_updates"):
            with self.subTest(feed_type=feed_type):
                output = io.StringIO()
                error_output = io.StringIO()
                opener = RecordingOpener(error=AssertionError("network called"))

                with redirect_stdout(output), redirect_stderr(error_output):
                    exit_code = run_cli(
                        ["--feed", feed_type, "--dry-run"],
                        environment=self.environment,
                        now_provider=self.fixed_now,
                        uuid_provider=self.fixed_uuid,
                        opener=opener,
                    )

                text = output.getvalue()
                self.assertEqual(exit_code, 0)
                self.assertEqual(error_output.getvalue(), "")
                self.assertEqual(opener.calls, [])
                self.assertIn(OFFICIAL_ENDPOINTS[feed_type], text)
                self.assertIn(feed_type, text)
                self.assertIn("20260725T143000Z", text)
                self.assertIn(str(self.CAPTURE_UUID), text)
                self.assertIn("30 seconds", text)
                self.assertIn("52428800 bytes", text)
                self.assertNotIn(self.API_KEY, text)
                self.assertFalse((self.project_root / "data").exists())

    def test_cli_requires_feed(self) -> None:
        with (
            redirect_stderr(io.StringIO()),
            self.assertRaises(SystemExit) as context,
        ):
            run_cli(["--dry-run"], environment=self.environment)
        self.assertEqual(context.exception.code, 2)

    def test_cli_rejects_unsupported_feed(self) -> None:
        error_output = io.StringIO()

        with redirect_stderr(error_output):
            exit_code = run_cli(
                ["--feed", "alerts", "--dry-run"],
                environment=self.environment,
                now_provider=self.fixed_now,
                uuid_provider=self.fixed_uuid,
            )

        self.assertEqual(exit_code, 1)
        self.assertIn("Unsupported GTFS-Realtime feed type", error_output.getvalue())
        self.assertNotIn(self.API_KEY, error_output.getvalue())

    def test_successful_capture_for_each_feed(self) -> None:
        for feed_type in ("vehicle_positions", "trip_updates"):
            with self.subTest(feed_type=feed_type):
                response = FakeResponse(
                    self.PAYLOAD,
                    content_length=str(len(self.PAYLOAD)),
                )
                result, opener = self.capture(feed_type, response=response)

                self.assertEqual(len(opener.calls), 1)
                request, timeout = opener.calls[0]
                headers = {
                    name.lower(): value
                    for name, value in request.header_items()
                }
                self.assertEqual(request.get_method(), "GET")
                self.assertEqual(request.full_url, OFFICIAL_ENDPOINTS[feed_type])
                self.assertEqual(headers["apikey"], self.API_KEY)
                self.assertEqual(headers["accept"], "application/x-protobuf")
                self.assertEqual(timeout, 30)
                self.assertTrue(response.closed)
                self.assertEqual(result.feed_type, feed_type)
                self.assertNotIn(self.API_KEY, repr(result))

                expected_directory = (
                    self.project_root
                    / "data"
                    / "raw"
                    / "gtfs_realtime"
                    / "stm"
                    / feed_type
                    / "2026"
                    / "07"
                    / "25"
                )
                expected_stem = (
                    "20260725T143000Z_"
                    "12345678-1234-5678-1234-567812345678"
                )
                self.assertEqual(result.payload_path.parent, expected_directory)
                self.assertEqual(result.payload_path.name, f"{expected_stem}.pb")
                self.assertEqual(result.metadata_path.name, f"{expected_stem}.json")
                self.assertEqual(result.payload_path.read_bytes(), self.PAYLOAD)

                metadata = json.loads(
                    result.metadata_path.read_text(encoding="utf-8")
                )
                required_fields = {
                    "schema_version",
                    "provider",
                    "feed_type",
                    "endpoint",
                    "http_method",
                    "http_status",
                    "response_content_type",
                    "response_size_bytes",
                    "sha256",
                    "capture_uuid",
                    "captured_at_utc",
                    "filename_timestamp_utc",
                    "payload_relative_path",
                    "metadata_relative_path",
                    "request_timeout_seconds",
                    "maximum_response_bytes",
                }
                self.assertTrue(required_fields.issubset(metadata))
                self.assertEqual(metadata["schema_version"], 1)
                self.assertEqual(metadata["provider"], "stm")
                self.assertEqual(metadata["http_method"], "GET")
                self.assertEqual(metadata["http_status"], 200)
                self.assertEqual(metadata["response_size_bytes"], len(self.PAYLOAD))
                self.assertEqual(
                    metadata["sha256"],
                    hashlib.sha256(self.PAYLOAD).hexdigest(),
                )
                self.assertEqual(
                    self.project_root / metadata["payload_relative_path"],
                    result.payload_path,
                )
                self.assertEqual(
                    self.project_root / metadata["metadata_relative_path"],
                    result.metadata_path,
                )
                self.assertNotIn(self.API_KEY, json.dumps(metadata))

    def test_content_length_must_exactly_match_stored_body(self) -> None:
        result, _ = self.capture(
            response=FakeResponse(
                self.PAYLOAD,
                content_length=str(len(self.PAYLOAD)),
            )
        )
        metadata = json.loads(result.metadata_path.read_text(encoding="utf-8"))
        self.assertEqual(
            metadata["response_content_length_header"],
            len(self.PAYLOAD),
        )
        self.assertEqual(metadata["response_size_bytes"], len(self.PAYLOAD))

        mismatches = (
            (str(len(self.PAYLOAD) + 1), "larger declaration"),
            (str(len(self.PAYLOAD) - 1), "smaller declaration"),
        )
        result.payload_path.unlink()
        result.metadata_path.unlink()

        for content_length, label in mismatches:
            with self.subTest(label=label):
                with self.assertRaisesRegex(
                    CaptureError,
                    "does not match Content-Length",
                ) as context:
                    self.capture(
                        response=FakeResponse(
                            self.PAYLOAD,
                            content_length=content_length,
                        )
                    )

                self.assertNotIn(self.API_KEY, str(context.exception))
                self.assert_no_capture_files()

    def test_missing_content_length_allows_valid_streamed_body(self) -> None:
        result, _ = self.capture(response=FakeResponse(self.PAYLOAD))
        metadata = json.loads(result.metadata_path.read_text(encoding="utf-8"))
        self.assertNotIn("response_content_length_header", metadata)
        self.assertEqual(result.payload_path.read_bytes(), self.PAYLOAD)

    def test_invalid_and_zero_content_lengths_are_rejected_before_read(self) -> None:
        for content_length in ("0", "-1", "not-a-number", " 1 ", "+1", "1.0"):
            with self.subTest(content_length=content_length):
                response = FakeResponse(
                    self.PAYLOAD,
                    content_length=content_length,
                )

                with self.assertRaises(CaptureError) as context:
                    self.capture(response=response)

                self.assertIn("Content-Length", str(context.exception))
                self.assertNotIn(self.API_KEY, str(context.exception))
                self.assertEqual(response.read_calls, 0)
                self.assert_no_capture_files()

    def test_conflicting_content_lengths_are_rejected_before_read(self) -> None:
        response = FakeResponse(self.PAYLOAD)
        response.headers = Message()
        response.headers["Content-Type"] = "application/x-protobuf"
        response.headers["Content-Length"] = str(len(self.PAYLOAD))
        response.headers["Content-Length"] = str(len(self.PAYLOAD) + 1)

        with self.assertRaisesRegex(CaptureError, "values conflict") as context:
            self.capture(response=response)

        self.assertNotIn(self.API_KEY, str(context.exception))
        self.assertEqual(response.read_calls, 0)
        self.assert_no_capture_files()

    def test_content_type_validation(self) -> None:
        accepted = (
            "application/x-protobuf",
            "APPLICATION/X-PROTOBUF",
            "application/x-protobuf; charset=binary",
        )

        for content_type in accepted:
            with self.subTest(accepted=content_type):
                result, _ = self.capture(
                    response=FakeResponse(
                        self.PAYLOAD,
                        content_type=content_type,
                    )
                )
                self.assertEqual(
                    result.response_content_type,
                    "application/x-protobuf",
                )
                result.payload_path.unlink()
                result.metadata_path.unlink()

        rejected = (None, "text/html", "text/plain", "application/json")

        for content_type in rejected:
            with self.subTest(rejected=content_type):
                with self.assertRaises(CaptureError) as context:
                    self.capture(
                        response=FakeResponse(
                            self.PAYLOAD,
                            content_type=content_type,
                        )
                    )
                self.assertNotIn(self.API_KEY, str(context.exception))
                self.assert_no_capture_files()

    def test_empty_response_is_rejected(self) -> None:
        with self.assertRaisesRegex(CaptureError, "body is empty"):
            self.capture(response=FakeResponse(b""))
        self.assert_no_capture_files()

    def test_oversized_content_length_is_rejected_before_read(self) -> None:
        config = replace(self.config, maximum_response_bytes=4)
        response = FakeResponse(
            b"12345",
            content_length="5",
        )

        with self.assertRaisesRegex(CaptureError, "Content-Length exceeds"):
            self.capture(response=response, config=config)

        self.assertEqual(response.read_calls, 0)
        self.assert_no_capture_files()

    def test_streamed_size_limit_and_exact_limit(self) -> None:
        config = replace(self.config, maximum_response_bytes=4)

        with self.assertRaisesRegex(CaptureError, "body exceeds"):
            self.capture(
                response=FakeResponse(b"12345"),
                config=config,
            )
        self.assert_no_capture_files()

        result, _ = self.capture(
            response=FakeResponse(b"1234"),
            config=config,
        )
        self.assertEqual(result.payload_path.read_bytes(), b"1234")

    def test_http_statuses_are_secret_safe(self) -> None:
        statuses = (301, 302, 303, 307, 308, 400, 401, 403, 404, 408, 429, 500, 502, 503, 504)

        for status in statuses:
            with self.subTest(status=status):
                opener = RecordingOpener(response=FakeResponse(status=status))

                with self.assertRaises(CaptureError) as context:
                    self.capture(opener=opener)

                message = str(context.exception)
                self.assertIn(str(status), message)
                self.assertNotIn(self.API_KEY, message)
                self.assertEqual(len(opener.calls), 1)
                self.assert_no_capture_files()

    def test_urllib_http_errors_are_secret_safe(self) -> None:
        for status in (301, 401, 429, 503):
            with self.subTest(status=status):
                http_error = urllib.error.HTTPError(
                    OFFICIAL_ENDPOINTS["vehicle_positions"],
                    status,
                    "synthetic",
                    None,
                    None,
                )
                opener = RecordingOpener(error=http_error)

                with self.assertRaises(CaptureError) as context:
                    self.capture(opener=opener)

                self.assertIn(str(status), str(context.exception))
                self.assertNotIn(self.API_KEY, str(context.exception))

    def test_no_redirect_handler_rejects_every_redirect_code(self) -> None:
        handler = NoRedirectHandler()
        request = urllib.request.Request(
            OFFICIAL_ENDPOINTS["vehicle_positions"],
            headers={"apiKey": self.API_KEY},
        )

        for status in (301, 302, 303, 307, 308):
            with self.subTest(status=status):
                self.assertIsNone(
                    handler.redirect_request(
                        request,
                        None,
                        status,
                        "synthetic redirect",
                        {},
                        "https://redirected.invalid/never-contacted",
                    )
                )

    def test_open_https_request_constructs_no_redirect_opener(self) -> None:
        constructed_handlers = []
        opener_calls = []

        class SyntheticOpener:
            def open(self, request, timeout):
                opener_calls.append((request, timeout))
                raise urllib.error.HTTPError(
                    request.full_url,
                    302,
                    "synthetic redirect",
                    None,
                    None,
                )

        synthetic_opener = SyntheticOpener()

        def fake_build_opener(*handlers):
            constructed_handlers.extend(handlers)
            return synthetic_opener

        request = urllib.request.Request(
            OFFICIAL_ENDPOINTS["vehicle_positions"],
            headers={"apiKey": self.API_KEY},
        )

        with patch(
            "capture_gtfs_realtime.urllib.request.build_opener",
            side_effect=fake_build_opener,
        ):
            with self.assertRaises(urllib.error.HTTPError) as context:
                open_https_request(request, 30)

        self.assertTrue(
            any(isinstance(handler, NoRedirectHandler) for handler in constructed_handlers)
        )
        self.assertEqual(len(opener_calls), 1)
        self.assertNotIn(self.API_KEY, str(context.exception))

    def test_redirect_does_not_contact_destination_or_resend_api_key(self) -> None:
        contacted_urls = []

        class SyntheticRedirectSource(urllib.request.BaseHandler):
            handler_order = 100

            def https_open(self, request):
                contacted_urls.append(request.full_url)
                headers = Message()
                headers["Location"] = "https://redirected.invalid/never-contacted"
                response = urllib.response.addinfourl(
                    io.BytesIO(),
                    headers,
                    request.full_url,
                    302,
                )
                response.msg = "synthetic redirect"
                return response

        opener = REAL_BUILD_OPENER(
            NoRedirectHandler(),
            SyntheticRedirectSource(),
        )
        request = urllib.request.Request(
            OFFICIAL_ENDPOINTS["vehicle_positions"],
            headers={"apiKey": self.API_KEY},
        )

        with self.assertRaises(urllib.error.HTTPError) as context:
            opener.open(request, timeout=30)

        self.assertEqual(
            contacted_urls,
            [OFFICIAL_ENDPOINTS["vehicle_positions"]],
        )
        self.assertNotIn("redirected.invalid", " ".join(contacted_urls))
        self.assertNotIn(self.API_KEY, str(context.exception))

    def test_timeout_and_connection_failure_are_handled(self) -> None:
        failures = (
            (socket.timeout(), "timed out"),
            (urllib.error.URLError("synthetic DNS failure"), "Unable to connect"),
            (OSError("synthetic connection failure"), "Unable to connect"),
        )

        for error, message in failures:
            with self.subTest(error=type(error).__name__):
                opener = RecordingOpener(error=error)

                with self.assertRaisesRegex(CaptureError, message) as context:
                    self.capture(opener=opener)

                self.assertNotIn(self.API_KEY, str(context.exception))
                self.assert_no_capture_files()

    def test_http_protocol_failures_are_secret_safe_and_clean(self) -> None:
        failures = (
            (
                http.client.IncompleteRead(b"partial", 10),
                "ended before capture completed",
            ),
            (
                http.client.HTTPException("synthetic protocol failure"),
                "failed HTTP protocol validation",
            ),
        )

        for error, message in failures:
            with self.subTest(error=type(error).__name__):
                response = FakeResponse(
                    self.PAYLOAD,
                    read_exception=error,
                )

                with self.assertRaisesRegex(CaptureError, message) as context:
                    self.capture(response=response)

                self.assertNotIn(self.API_KEY, str(context.exception))
                self.assert_no_capture_files()

    def test_download_failure_removes_temporary_and_final_files(self) -> None:
        response = FakeResponse(
            self.PAYLOAD,
            read_error_after=1,
        )

        with self.assertRaisesRegex(CaptureError, "Unable to persist"):
            self.capture(response=response)

        self.assert_no_capture_files()

    def test_metadata_write_failure_removes_temporary_and_final_files(self) -> None:
        def failing_metadata_writer(path, metadata) -> None:
            path.write_text("partial", encoding="utf-8")
            raise OSError("synthetic metadata failure")

        with self.assertRaisesRegex(CaptureError, "Unable to persist"):
            self.capture(metadata_writer=failing_metadata_writer)

        self.assert_no_capture_files()

    def test_existing_final_file_is_not_overwritten(self) -> None:
        plan = self.capture_plan()
        plan.destination_directory.mkdir(parents=True)
        plan.payload_path.write_bytes(b"existing")

        with self.assertRaisesRegex(CaptureError, "already exists"):
            self.capture()

        self.assertEqual(plan.payload_path.read_bytes(), b"existing")
        self.assertFalse(plan.metadata_path.exists())

    def test_existing_metadata_file_is_not_overwritten(self) -> None:
        plan = self.capture_plan()
        plan.destination_directory.mkdir(parents=True)
        plan.metadata_path.write_text("existing metadata", encoding="utf-8")

        with self.assertRaisesRegex(CaptureError, "already exists") as context:
            self.capture()

        self.assertNotIn(self.API_KEY, str(context.exception))
        self.assertEqual(
            plan.metadata_path.read_text(encoding="utf-8"),
            "existing metadata",
        )
        self.assertFalse(plan.payload_path.exists())
        remaining_files = [
            path
            for path in plan.destination_directory.iterdir()
            if path != plan.metadata_path
        ]
        self.assertEqual(remaining_files, [])

    def test_real_capture_cli_success_output_is_secret_free(self) -> None:
        standard_output = io.StringIO()
        error_output = io.StringIO()
        opener = RecordingOpener(
            FakeResponse(
                self.PAYLOAD,
                content_length=str(len(self.PAYLOAD)),
            )
        )

        with redirect_stdout(standard_output), redirect_stderr(error_output):
            exit_code = run_cli(
                ["--feed", "vehicle_positions"],
                environment=self.environment,
                now_provider=self.fixed_now,
                uuid_provider=self.fixed_uuid,
                opener=opener,
            )

        combined_output = standard_output.getvalue() + error_output.getvalue()
        self.assertEqual(exit_code, 0)
        self.assertEqual(len(opener.calls), 1)
        self.assertNotIn(self.API_KEY, combined_output)
        self.assertNotIn("apiKey:", combined_output)
        self.assertNotIn("Accept: application/x-protobuf", combined_output)

    def test_real_capture_cli_failures_are_secret_free(self) -> None:
        failures = (
            RecordingOpener(response=FakeResponse(status=401)),
            RecordingOpener(
                response=FakeResponse(
                    self.PAYLOAD,
                    content_length=str(len(self.PAYLOAD) + 1),
                )
            ),
        )

        for opener in failures:
            with self.subTest(opener=opener):
                standard_output = io.StringIO()
                error_output = io.StringIO()

                with redirect_stdout(standard_output), redirect_stderr(error_output):
                    exit_code = run_cli(
                        ["--feed", "vehicle_positions"],
                        environment=self.environment,
                        now_provider=self.fixed_now,
                        uuid_provider=self.fixed_uuid,
                        opener=opener,
                    )

                combined_output = (
                    standard_output.getvalue() + error_output.getvalue()
                )
                self.assertEqual(exit_code, 1)
                self.assertNotIn(self.API_KEY, combined_output)
                self.assertNotIn("apiKey:", combined_output)
                self.assertNotIn(
                    "Accept: application/x-protobuf",
                    combined_output,
                )
                self.assert_no_capture_files()

    def test_second_file_finalization_failure_rolls_back_payload(self) -> None:
        finalization_calls = 0

        def failing_second_finalizer(temporary_path, final_path) -> None:
            nonlocal finalization_calls
            finalization_calls += 1

            if finalization_calls == 2:
                raise OSError("synthetic second finalization failure")

            finalize_without_overwrite(temporary_path, final_path)

        with self.assertRaisesRegex(CaptureError, "Unable to persist"):
            self.capture(finalizer=failing_second_finalizer)

        self.assertEqual(finalization_calls, 2)
        self.assert_no_capture_files()

    def test_unsafe_runtime_values_are_rejected(self) -> None:
        with self.assertRaises((ValueError, CaptureError)):
            plan_capture(
                self.config,
                "../vehicle_positions",
                self.CAPTURE_TIME,
                self.CAPTURE_UUID,
            )

        with self.assertRaises((ValueError, CaptureError)):
            plan_capture(
                self.config,
                "vehicle_positions",
                datetime(2026, 7, 25, 14, 30),
                self.CAPTURE_UUID,
            )

        with self.assertRaises((ValueError, CaptureError)):
            plan_capture(
                self.config,
                "vehicle_positions",
                self.CAPTURE_TIME,
                "../unsafe",
            )

        self.assertFalse((self.project_root / "data").exists())

    def test_capture_does_not_modify_static_outputs(self) -> None:
        warehouse_path = (
            self.project_root
            / "data"
            / "warehouse"
            / "montreal_transit.duckdb"
        )
        report_path = self.project_root / "docs" / "index.html"
        warehouse_path.parent.mkdir(parents=True)
        report_path.parent.mkdir(parents=True)
        warehouse_path.write_bytes(b"warehouse marker")
        report_path.write_text("report marker", encoding="utf-8")

        self.capture()

        self.assertEqual(warehouse_path.read_bytes(), b"warehouse marker")
        self.assertEqual(report_path.read_text(encoding="utf-8"), "report marker")


if __name__ == "__main__":
    unittest.main()
