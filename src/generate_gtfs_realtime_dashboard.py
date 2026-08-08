from __future__ import annotations

import argparse
import os
import sys
import webbrowser
from pathlib import Path

import duckdb

from gtfs_realtime_reporting import (
    PUBLIC_MAXIMUM_BYTES, PUBLIC_PROFILE, ReportingError, build_dashboard_html,
    load_reporting_data, write_dashboard_atomic,
)


DEFAULT_PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_WAREHOUSE = Path("data/warehouse/montreal_transit.duckdb")
DEFAULT_OUTPUT = Path("docs/gtfs_realtime_reliability.html")


def project_root() -> Path:
    return Path(os.environ.get("MONTREAL_TRANSIT_PROJECT_ROOT", DEFAULT_PROJECT_ROOT)).resolve()


def generate_dashboard(reliability_run_id: str, warehouse: Path | None = None,
                       output: Path | None = None,
                       profile: str = PUBLIC_PROFILE) -> tuple[Path, object]:
    if profile != PUBLIC_PROFILE:
        raise ReportingError("Unsupported dashboard profile.")
    root = project_root()
    warehouse_path = (warehouse or root / DEFAULT_WAREHOUSE).resolve()
    output_path = (output or root / DEFAULT_OUTPUT).resolve()
    if not warehouse_path.is_file():
        raise ReportingError("DuckDB warehouse does not exist.")
    connection = duckdb.connect(str(warehouse_path), read_only=True)
    try:
        data = load_reporting_data(connection, reliability_run_id)
        dashboard = build_dashboard_html(data)
    finally:
        connection.close()
    output_bytes = len(dashboard.encode("utf-8"))
    if output_bytes > PUBLIC_MAXIMUM_BYTES:
        raise ReportingError(
            "Public dashboard exceeds the 10 MiB project publication policy."
        )
    write_dashboard_atomic(output_path, dashboard)
    return output_path, data


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Generate a self-contained GTFS-Realtime reliability dashboard.")
    parser.add_argument("--reliability-run-id", required=True)
    parser.add_argument("--warehouse", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--profile", choices=(PUBLIC_PROFILE,), default=PUBLIC_PROFILE,
                        help="Presentation profile (default: public).")
    parser.add_argument("--open", action="store_true", help="Open the dashboard after successful generation.")
    return parser


def run_cli(argv: list[str] | None = None) -> int:
    args = build_argument_parser().parse_args(argv)
    try:
        output, data = generate_dashboard(
            args.reliability_run_id, args.warehouse, args.output, args.profile
        )
    except (ReportingError, OSError, duckdb.Error) as error:
        print(f"GTFS-Realtime dashboard generation failed: {error}", file=sys.stderr)
        return 1
    print(f"Reliability run ID: {data.run['reliability_run_id']}")
    print(f"Dashboard: {output.name}")
    print(f"Dashboard profile: {args.profile}")
    print(f"Output size: {output.stat().st_size / (1024 * 1024):.2f} MiB")
    print(f"Embedded rows: routes={len(data.route_aggregates)}; "
          f"stops={len(data.stop_aggregates)}; trips={len(data.trips)}; "
          f"histogram bins={len(data.histogram_bins)}")
    if args.open:
        webbrowser.open(output.as_uri())
        print("Browser open requested.")
    return 0


def main() -> None:
    raise SystemExit(run_cli())


if __name__ == "__main__":
    main()
