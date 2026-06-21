from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import duckdb


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIRECTORY = REPOSITORY_ROOT / "src"


class PipelineIntegrationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.workspace = Path(self.temporary_directory.name)

        self.gtfs_directory = (
            self.workspace / "data" / "raw" / "gtfs" / "current"
        )
        self.gtfs_directory.mkdir(parents=True, exist_ok=True)

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def write_fixture_file(self, file_name: str, content: str) -> None:
        file_path = self.gtfs_directory / file_name
        file_path.write_text(content, encoding="utf-8")

    def create_gtfs_fixture(
        self,
        invalid_stop_sequence: bool = False,
    ) -> None:
        second_stop_sequence = "0" if invalid_stop_sequence else "2"

        files = {
            "agency.txt": (
                "agency_id,agency_name,agency_url,agency_timezone\n"
                "STM,STM Test,https://example.org,America/Montreal\n"
            ),
            "calendar.txt": (
                "service_id,monday,tuesday,wednesday,thursday,friday,"
                "saturday,sunday,start_date,end_date\n"
                "WKD,1,1,1,1,1,0,0,20260101,20261231\n"
            ),
            "calendar_dates.txt": (
                "service_id,date,exception_type\n"
                "WKD,20260701,2\n"
            ),
            "feed_info.txt": (
                "feed_publisher_name,feed_publisher_url,feed_lang,"
                "feed_start_date,feed_end_date,feed_version\n"
                "STM Test,https://example.org,fr,20260101,20261231,"
                "test-fixture-1\n"
            ),
            "routes.txt": (
                "route_id,route_short_name,route_long_name,route_type\n"
                "10,10,Test Route,3\n"
            ),
            "shapes.txt": (
                "shape_id,shape_pt_lat,shape_pt_lon,shape_pt_sequence\n"
                "shape_10,45.5017,-73.5673,1\n"
                "shape_10,45.5117,-73.5773,2\n"
            ),
            "stops.txt": (
                "stop_id,stop_name,stop_lat,stop_lon\n"
                "STOP_A,Test Stop A,45.5017,-73.5673\n"
                "STOP_B,Test Stop B,45.5117,-73.5773\n"
            ),
            "trips.txt": (
                "route_id,service_id,trip_id,trip_headsign,direction_id,"
                "shape_id\n"
                "10,WKD,TRIP_001,Test Destination,0,shape_10\n"
            ),
            "stop_times.txt": (
                "trip_id,arrival_time,departure_time,stop_id,"
                "stop_sequence\n"
                "TRIP_001,08:00:00,08:00:00,STOP_A,1\n"
                f"TRIP_001,08:10:00,08:10:00,STOP_B,{second_stop_sequence}\n"
            ),
        }

        for file_name, content in files.items():
            self.write_fixture_file(file_name, content)

    def run_script(self, script_name: str) -> None:
        environment = os.environ.copy()
        environment["MONTREAL_TRANSIT_PROJECT_ROOT"] = str(self.workspace)

        completed_process = subprocess.run(
            [
                sys.executable,
                str(SCRIPTS_DIRECTORY / script_name),
            ],
            cwd=REPOSITORY_ROOT,
            env=environment,
            text=True,
            capture_output=True,
            check=False,
        )

        self.assertEqual(
            completed_process.returncode,
            0,
            msg=(
                f"{script_name} failed.\n\n"
                f"STDOUT:\n{completed_process.stdout}\n\n"
                f"STDERR:\n{completed_process.stderr}"
            ),
        )

    def open_database(self) -> duckdb.DuckDBPyConnection:
        database_path = (
            self.workspace
            / "data"
            / "warehouse"
            / "montreal_transit.duckdb"
        )

        return duckdb.connect(str(database_path), read_only=True)

    def test_valid_gtfs_fixture_generates_a_passing_report(self) -> None:
        self.create_gtfs_fixture()

        self.run_script("ingest_gtfs.py")
        self.run_script("run_quality_checks.py")
        self.run_script("generate_quality_report.py")

        report_path = self.workspace / "docs" / "index.html"
        status_chart_path = (
            self.workspace / "docs" / "assets" / "rules_by_status.png"
        )
        severity_chart_path = (
            self.workspace / "docs" / "assets" / "rules_by_severity.png"
        )

        self.assertTrue(report_path.exists())
        self.assertTrue(status_chart_path.exists())
        self.assertTrue(severity_chart_path.exists())

        report_content = report_path.read_text(encoding="utf-8")

        self.assertIn("READY FOR REPORTING", report_content)
        self.assertIn("Quality overview", report_content)

        self.assertIn(
            "Montréal Transit Reliability & Data Quality",
            report_content,
        )

        connection = self.open_database()

        try:
            result_summary = connection.execute(
                """
                SELECT
                    COUNT(*) AS total_rules,
                    SUM(CASE WHEN status = 'PASS' THEN 1 ELSE 0 END)
                        AS passed_rules,
                    SUM(CASE WHEN status = 'FAIL' THEN 1 ELSE 0 END)
                        AS failed_rules
                FROM dq_result
                """
            ).fetchone()

            self.assertEqual(result_summary, (10, 10, 0))

        finally:
            connection.close()

    def test_invalid_stop_sequence_is_detected(self) -> None:
        self.create_gtfs_fixture(invalid_stop_sequence=True)

        self.run_script("ingest_gtfs.py")
        self.run_script("run_quality_checks.py")

        connection = self.open_database()

        try:
            result = connection.execute(
                """
                SELECT
                    status,
                    rows_failed
                FROM dq_result
                WHERE rule_id = 'DQ010'
                """
            ).fetchone()

            self.assertEqual(result, ("FAIL", 1))

        finally:
            connection.close()


if __name__ == "__main__":
    unittest.main()