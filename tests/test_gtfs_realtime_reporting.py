from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import duckdb


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from generate_gtfs_realtime_dashboard import (  # noqa: E402
    build_argument_parser, generate_dashboard, run_cli,
)
from gtfs_realtime_reporting import (  # noqa: E402
    PUBLIC_MAXIMUM_BYTES, PUBLIC_STOP_LIMIT, PUBLIC_TRIP_LIMIT,
    REPORTING_GENERATOR_VERSION,
    ReportingError, build_dashboard_html, load_reporting_data, safe_json,
    write_dashboard_atomic,
)
from gtfs_realtime_persistence import ensure_realtime_schema  # noqa: E402
from gtfs_realtime_reliability import assess_reliability, load_reliability_config  # noqa: E402
from gtfs_realtime_reliability_repository import (  # noqa: E402
    ensure_reliability_schema, load_reliability_source, persist_reliability,
)
from tests import test_gtfs_realtime_reliability_persistence as reliability_fixture  # noqa: E402


class GtfsRealtimeReportingTest(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = reliability_fixture.GtfsRealtimeReliabilityPersistenceTest(
            "test_source_lineage_events_relationships_and_route_filter"
        )
        self.fixture.setUp()
        self.root = self.fixture.root
        self.warehouse = self.fixture.warehouse
        connection = duckdb.connect(str(self.warehouse))
        config = load_reliability_config(self.root)
        source = load_reliability_source(connection, self.fixture.match_run_id, config)
        assessment = assess_reliability(source.lineage, source.observations,
                                        source.relationships, config)
        ensure_reliability_schema(connection)
        persisted = persist_reliability(connection, source, assessment, config)
        self.run_id = persisted.reliability_run_id
        connection.execute("CREATE TABLE dim_route (route_id VARCHAR, route_short_name VARCHAR, route_long_name VARCHAR)")
        connection.execute("INSERT INTO dim_route VALUES (?, ?, ?)",
                           ["route-1", "<R&1>", "</script><script>alert('route')</script>"])
        connection.execute("CREATE TABLE dim_stop (stop_id VARCHAR, stop_name VARCHAR)")
        connection.execute("INSERT INTO dim_stop VALUES (?, ?), (?, ?)",
                           ["stop-1", "Stop <One> & 'A'", "stop-2", "</script><img src=x onerror=alert(1)>"])
        ensure_realtime_schema(connection)
        connection.close()
        self.environment = {"MONTREAL_TRANSIT_PROJECT_ROOT": str(self.root),
                            "STM_GTFS_REALTIME_API_KEY": "SYNTHETIC-SECRET-MUST-NOT-APPEAR"}

    def tearDown(self) -> None:
        self.fixture.tearDown()

    def load(self):
        connection = duckdb.connect(str(self.warehouse), read_only=True)
        try:
            return load_reporting_data(connection, self.run_id)
        finally:
            connection.close()

    @staticmethod
    def presentation_model(dashboard: str) -> dict[str, object]:
        prefix = '<script id="dashboard-data" type="application/json">'
        payload = dashboard.split(prefix, 1)[1].split("</script>", 1)[0]
        return json.loads(payload)

    def test_known_run_lineage_and_all_persisted_views_load(self) -> None:
        data = self.load()
        self.assertEqual(data.run["reliability_run_id"], self.run_id)
        self.assertEqual(data.run["static_snapshot_identifier"], "snapshot-1")
        self.assertGreater(len(data.route_aggregates), 0)
        self.assertGreater(len(data.stop_aggregates), 0)
        self.assertGreater(len(data.trips), 0)
        self.assertGreater(len(data.histogram_bins), 0)
        self.assertIn("trip_matching_ratio", data.coverage)
        self.assertIn("arrival_classification_ratio", data.coverage)
        self.assertIn("departure_classification_ratio", data.coverage)
        self.assertEqual(data.route_aggregates[0]["route_name"], "<R&1> – </script><script>alert('route')</script>")
        self.assertIn("Stop <One> & 'A'", {item["stop_name"] for item in data.stop_aggregates})

    def test_unknown_run_and_missing_tables_are_rejected(self) -> None:
        connection = duckdb.connect(str(self.warehouse), read_only=True)
        with self.assertRaisesRegex(ReportingError, "not found"):
            load_reporting_data(connection, "unknown")
        connection.close()
        with tempfile.TemporaryDirectory() as directory:
            empty = Path(directory) / "empty.duckdb"
            connection = duckdb.connect(str(empty))
            with self.assertRaisesRegex(ReportingError, "missing"):
                load_reporting_data(connection, self.run_id)
            connection.close()

    def test_no_quality_rows_still_allows_dashboard_generation(self) -> None:
        self.assertEqual(self.load().quality_context, ())
        dashboard = build_dashboard_html(self.load())
        self.assertIn("Optional feed-quality context is not available", dashboard)

    def test_actual_quality_schema_values_and_nulls_are_preserved(self) -> None:
        connection = duckdb.connect(str(self.warehouse))
        columns = {row[1] for row in connection.execute(
            "PRAGMA table_info('gtfs_realtime_quality_result')"
        ).fetchall()}
        self.assertIn("numeric_value", columns)
        self.assertNotIn("value", columns)
        connection.execute("""INSERT INTO gtfs_realtime_quality_run (
            quality_run_id, capture_uuid, analyzed_at_utc, quality_config_schema_version,
            overall_status, result_count, finding_count) VALUES (?, ?, ?, ?, ?, ?, ?)""",
            ["quality-1", "capture-1", datetime(2026, 8, 3, tzinfo=timezone.utc),
             1, "WARN", 2, 0])
        connection.execute("""INSERT INTO gtfs_realtime_quality_result (
            quality_run_id, result_index, rule_id, rule_name, category, status,
            metric_name, numeric_value, numerator, denominator, ratio, threshold,
            threshold_operator, unit, details, enabled, informational)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?),
                   (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            ["quality-1", 0, "RTF001", "Feed age", "freshness", "WARN",
             "feed_age_seconds", 45.5, None, None, None, 30.0, "<=", "seconds",
             "Synthetic freshness context", True, False,
             "quality-1", 1, "RTC001", "Total entities", "completeness", "INFO",
             "total_entities", None, 2, 3, 2 / 3, None, None, "ratio",
             "Synthetic completeness context", False, True])
        connection.close()
        data = self.load()
        self.assertEqual(data.run["feed_quality_status"], "WARN")
        freshness, completeness = data.quality_context
        self.assertEqual(freshness["numeric_value"], 45.5)
        self.assertEqual(freshness["threshold"], 30.0)
        self.assertEqual(freshness["threshold_operator"], "<=")
        self.assertIsNone(freshness["ratio"])
        self.assertIsNone(completeness["numeric_value"])
        self.assertEqual((completeness["numerator"], completeness["denominator"]), (2, 3))
        self.assertAlmostEqual(completeness["ratio"], 2 / 3)
        dashboard = build_dashboard_html(data)
        self.assertIn('"numeric_value":45.5', dashboard)
        self.assertIn('"numeric_value":null', dashboard)
        self.assertIn('"ratio":null', dashboard)
        self.assertIn("WARN", dashboard)

    def test_dashboard_sections_title_attribution_lineage_and_versions(self) -> None:
        dashboard = build_dashboard_html(self.load(), datetime(2026, 8, 4, tzinfo=timezone.utc))
        required = ("<title>Montréal Transit Reliability</title>", "1. Overview",
                    "2. Coverage and data confidence", "3. Punctuality distribution",
                    "4. Delay distribution", "5. Route performance", "6. Stop performance",
                    "7. Trip-level examples", "8. Reported cancellations",
                    "9. Feed/data-quality context", "10. Methodology and limitations",
                    "Data source: Société de transport de Montréal", "independent and unofficial",
                    self.run_id, "snapshot-1", REPORTING_GENERATOR_VERSION)
        for text in required:
            with self.subTest(text=text):
                self.assertIn(text, dashboard)

    def test_filters_charts_tables_and_no_client_network_api(self) -> None:
        dashboard = build_dashboard_html(self.load())
        for marker in ("classification-filter", "route-search", "stop-search", "trip-search",
                       "event_type", "interpretation_status", "punctuality-chart",
                       "delay-chart", "route-chart", "addEventListener", "table("):
            self.assertIn(marker, dashboard)
        for forbidden in ("fetch(", "XMLHttpRequest", "WebSocket", "api.stm.info"):
            self.assertNotIn(forbidden, dashboard)

    def test_html_serialization_blocks_injection_and_private_values(self) -> None:
        dashboard = build_dashboard_html(self.load())
        self.assertNotIn("<script>alert('route')</script>", dashboard)
        self.assertNotIn("<img src=x onerror=alert(1)>", dashboard)
        self.assertIn("\\u003c/script\\u003e", dashboard)
        self.assertIn("\\u0026", dashboard)
        for forbidden in ("SYNTHETIC-SECRET-MUST-NOT-APPEAR", str(self.root),
                          "license_plate", "vehicle_id", "application/x-protobuf",
                          "apiKey"):
            self.assertNotIn(forbidden, dashboard)
        serialized = safe_json({"unsafe": "</script>\u2028<&"})
        self.assertNotIn("</script>", serialized)
        self.assertIn("\\u003c", serialized)

    def test_metric_semantics_and_limitations_are_explicit(self) -> None:
        dashboard = build_dashboard_html(self.load())
        self.assertIn("v===null||v===undefined?'N/A'", dashboard)
        self.assertIn("Arrivals and departures use separate observations", dashboard)
        self.assertIn("Low-sample and not-applicable routes are not ranked", dashboard)
        self.assertIn("Coverage describes the observations", dashboard)
        self.assertIn("Reported cancellations observed", dashboard)
        self.assertNotIn("headway ratio", dashboard.lower())
        self.assertNotIn("reliability score", dashboard.lower())

    def test_stable_ordering_and_analytical_determinism(self) -> None:
        data = self.load()
        route_keys = [(item["service_date"], item["route_id"], item["direction_id"], item["event_type"])
                      for item in data.route_aggregates]
        self.assertEqual(route_keys, sorted(route_keys))
        self.assertEqual(data.stop_aggregates, self.load().stop_aggregates)
        stop_ranks = [(item["classified_event_count"], item["eligible_event_count"])
                      for item in data.stop_aggregates]
        self.assertEqual(stop_ranks, sorted(stop_ranks, reverse=True))
        timestamp = datetime(2026, 8, 4, tzinfo=timezone.utc)
        self.assertEqual(build_dashboard_html(data, timestamp), build_dashboard_html(data, timestamp))
        self.assertIn('"classificationOrder":["EARLY","ON_TIME","LATE","VERY_LATE","UNCLASSIFIED"]',
                      build_dashboard_html(data, timestamp))

    def test_generation_is_read_only_atomic_utf8_and_replaces_explicitly(self) -> None:
        connection = duckdb.connect(str(self.warehouse), read_only=True)
        before = {table: connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
                  for table in ("gtfs_realtime_match_run", "gtfs_realtime_reliability_run",
                                "gtfs_realtime_reliability_event")}
        connection.close()
        output = self.root / "published" / "dashboard.html"
        output.write_text("old", encoding="utf-8") if output.parent.exists() else None
        with patch.dict(os.environ, self.environment, clear=True), \
             patch("socket.create_connection", side_effect=AssertionError("network attempted")):
            generated, _ = generate_dashboard(self.run_id, self.warehouse, output)
        self.assertEqual(generated, output.resolve())
        decoded = output.read_bytes().decode("utf-8")
        self.assertIn("Montréal Transit Reliability", decoded)
        connection = duckdb.connect(str(self.warehouse), read_only=True)
        after = {table: connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
                 for table in before}
        connection.close()
        self.assertEqual(after, before)
        self.assertEqual(list(output.parent.glob("*.tmp")), [])

    def test_failed_generation_has_no_parent_or_partial_final(self) -> None:
        output = self.root / "not-created" / "dashboard.html"
        with patch.dict(os.environ, self.environment, clear=True):
            with self.assertRaises(ReportingError):
                generate_dashboard("unknown", self.warehouse, output)
        self.assertFalse(output.parent.exists())
        output.parent.mkdir()
        output.write_text("existing", encoding="utf-8")
        with patch.object(Path, "replace", side_effect=OSError("replace failed")):
            with self.assertRaisesRegex(OSError, "replace failed"):
                write_dashboard_atomic(output, "new")
        self.assertEqual(output.read_text(encoding="utf-8"), "existing")
        self.assertEqual(list(output.parent.glob("*.tmp")), [])

    def test_public_size_guard_preserves_existing_output(self) -> None:
        output = self.root / "guarded" / "dashboard.html"
        output.parent.mkdir()
        output.write_text("existing", encoding="utf-8")
        with patch("generate_gtfs_realtime_dashboard.build_dashboard_html",
                   return_value="x" * (PUBLIC_MAXIMUM_BYTES + 1)):
            with self.assertRaisesRegex(ReportingError, "10 MiB"):
                generate_dashboard(self.run_id, self.warehouse, output, "public")
        self.assertEqual(output.read_text(encoding="utf-8"), "existing")
        self.assertEqual(list(output.parent.glob("*.tmp")), [])

    def test_cli_help_generation_open_unknown_and_secret_free_output(self) -> None:
        with redirect_stdout(io.StringIO()):
            with self.assertRaises(SystemExit) as help_exit:
                build_argument_parser().parse_args(["--help"])
        self.assertEqual(help_exit.exception.code, 0)
        output = self.root / "cli-dashboard.html"
        stdout = io.StringIO()
        with patch.dict(os.environ, self.environment, clear=True), \
             patch("socket.create_connection", side_effect=AssertionError("network attempted")), \
             patch("webbrowser.open", return_value=True) as browser, redirect_stdout(stdout):
            result = run_cli(["--reliability-run-id", self.run_id, "--warehouse",
                              str(self.warehouse), "--output", str(output),
                              "--profile", "public", "--open"])
        self.assertEqual(result, 0)
        browser.assert_called_once()
        self.assertTrue(output.is_file())
        self.assertIn("Browser open requested", stdout.getvalue())
        self.assertIn("Dashboard profile: public", stdout.getvalue())
        self.assertIn("Output size:", stdout.getvalue())
        self.assertIn("histogram bins=", stdout.getvalue())
        self.assertNotIn(str(self.root), stdout.getvalue())
        self.assertNotIn("SYNTHETIC-SECRET", stdout.getvalue())
        stderr = io.StringIO()
        with patch.dict(os.environ, self.environment, clear=True), redirect_stderr(stderr):
            self.assertEqual(run_cli(["--reliability-run-id", "unknown", "--warehouse",
                                      str(self.warehouse), "--output", str(output)]), 1)
        self.assertIn("not found", stderr.getvalue())
        self.assertNotIn("SYNTHETIC-SECRET", stderr.getvalue())

    def test_scaled_public_presentation_is_bounded_compact_and_deterministic(self) -> None:
        connection = duckdb.connect(str(self.warehouse))
        connection.execute("""INSERT INTO gtfs_realtime_reliability_aggregate
            SELECT * EXCLUDE (i) REPLACE (
                (10000 + i)::INTEGER AS aggregate_index,
                'STOP_ROUTE_DIRECTION' AS dimension_type,
                ('scale-stop-' || lpad(i::VARCHAR, 3, '0')) AS stop_id,
                (i + 1)::INTEGER AS eligible_event_count,
                i::INTEGER AS classified_event_count)
            FROM (SELECT * FROM gtfs_realtime_reliability_aggregate
                  WHERE reliability_run_id = ? AND dimension_type = 'STOP_ROUTE_DIRECTION'
                  LIMIT 1), range(250) AS generated(i)""", [self.run_id])
        connection.execute("""INSERT INTO gtfs_realtime_reliability_trip
            SELECT * EXCLUDE (i) REPLACE (
                ('scale-trip-' || lpad(i::VARCHAR, 3, '0')) AS static_trip_id,
                (i + 1)::INTEGER AS eligible_event_count,
                i::INTEGER AS classified_event_count)
            FROM (SELECT * FROM gtfs_realtime_reliability_trip
                  WHERE reliability_run_id = ? LIMIT 1), range(250) AS generated(i)""",
            [self.run_id])
        connection.execute("""INSERT INTO gtfs_realtime_reliability_event
            SELECT * EXCLUDE (i) REPLACE (
                (10000 + i)::INTEGER AS event_index,
                ('raw-event-stop-' || i::VARCHAR) AS static_stop_id,
                (i - 500)::INTEGER AS selected_delta_seconds)
            FROM (SELECT * FROM gtfs_realtime_reliability_event
                  WHERE reliability_run_id = ? LIMIT 1), range(1000) AS generated(i)""",
            [self.run_id])
        expected_histogram_count = connection.execute("""SELECT count(*)
            FROM gtfs_realtime_reliability_event WHERE reliability_run_id = ?
              AND eligibility_status = 'ELIGIBLE' AND selected_delta_seconds IS NOT NULL""",
            [self.run_id]).fetchone()[0]
        connection.close()

        first = self.load()
        second = self.load()
        self.assertEqual(first.stop_aggregates, second.stop_aggregates)
        self.assertEqual(first.trips, second.trips)
        self.assertEqual(len(first.stop_aggregates), PUBLIC_STOP_LIMIT)
        self.assertEqual(len(first.trips), PUBLIC_TRIP_LIMIT)
        self.assertTrue(first.source_stop_count > PUBLIC_STOP_LIMIT)
        self.assertTrue(first.source_trip_count > PUBLIC_TRIP_LIMIT)
        self.assertEqual(first.stop_aggregates[0]["stop_id"], "scale-stop-249")
        self.assertEqual(first.trips[0]["static_trip_id"], "scale-trip-249")
        self.assertEqual(sum(item["observation_count"] for item in first.histogram_bins),
                         expected_histogram_count)

        dashboard = build_dashboard_html(first, datetime(2026, 8, 4, tzinfo=timezone.utc))
        model = self.presentation_model(dashboard)
        self.assertNotIn("events", model)
        self.assertIn("histogram", model)
        self.assertNotIn("raw-event-stop-", dashboard)
        self.assertEqual(dashboard.count('id="dashboard-data"'), 1)
        self.assertIn("Showing ${M.embeddedStopRows} of ${M.sourceStopRows}", dashboard)
        self.assertIn("Showing ${M.embeddedTripRows} of ${M.sourceTripRows}", dashboard)
        self.assertIn("D.histogram.filter", dashboard)
        self.assertIn("D.routes.map", dashboard)
        self.assertEqual(model["presentation"]["embeddedStopRows"], PUBLIC_STOP_LIMIT)
        self.assertEqual(model["presentation"]["embeddedTripRows"], PUBLIC_TRIP_LIMIT)
        self.assertTrue(model["presentation"]["stopsTruncated"])
        self.assertTrue(model["presentation"]["tripsTruncated"])
        self.assertLess(len(dashboard.encode("utf-8")), 750_000)


if __name__ == "__main__":
    unittest.main()
