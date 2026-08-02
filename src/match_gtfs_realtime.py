from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import duckdb

from gtfs_realtime_matching import MatchingError, assess_matches, load_matching_config
from gtfs_realtime_matching_repository import (
    MatchPersistenceResult,
    ensure_matching_schema,
    load_matching_source,
    persist_assessment,
)


DEFAULT_PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_WAREHOUSE_RELATIVE_PATH = Path("data/warehouse/montreal_transit.duckdb")


def project_root() -> Path:
    return Path(os.environ.get("MONTREAL_TRANSIT_PROJECT_ROOT", DEFAULT_PROJECT_ROOT)).resolve()


def match_capture(capture_uuid: str, warehouse: Path | None = None,
                  persist: bool = True) -> tuple[object, MatchPersistenceResult]:
    root = project_root()
    config = load_matching_config(root)
    warehouse_path = (warehouse or root / DEFAULT_WAREHOUSE_RELATIVE_PATH).resolve()
    if not warehouse_path.is_file():
        raise MatchingError("DuckDB warehouse does not exist.")
    connection = duckdb.connect(str(warehouse_path))
    try:
        source = load_matching_source(connection, capture_uuid, config)
        assessment = assess_matches(
            source.capture_uuid, source.captured_at_utc, source.snapshot,
            source.entities, source.trips, source.calendars, source.exceptions,
            source.stop_times, config,
        )
        if persist:
            ensure_matching_schema(connection)
            result = persist_assessment(connection, source, assessment, config)
        else:
            result = MatchPersistenceResult("", False)
        return assessment, result
    finally:
        connection.close()


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Match one persisted GTFS-Realtime capture to scheduled service.")
    parser.add_argument("--capture-uuid", required=True)
    parser.add_argument("--warehouse", type=Path)
    parser.add_argument("--summary", action="store_true", help="Print concise summary (default).")
    parser.add_argument("--no-persist", action="store_true", help="Calculate matches without adding matching tables or rows.")
    return parser


def run_cli(argv: list[str] | None = None) -> int:
    args = build_argument_parser().parse_args(argv)
    try:
        assessment, persistence = match_capture(
            args.capture_uuid, args.warehouse, not args.no_persist
        )
    except (MatchingError, OSError, duckdb.Error) as error:
        print(f"GTFS-Realtime matching failed: {error}", file=sys.stderr)
        return 1
    counts = assessment.status_counts
    stop_results = tuple(stop for result in assessment.results for stop in result.stop_matches)
    stop_matched = sum(stop.status == "MATCHED" for stop in stop_results)
    print(f"Capture UUID: {assessment.capture_uuid}")
    print("Required persistence schema: 2")
    print(f"Static snapshot: {assessment.snapshot.snapshot_identifier}")
    print(f"Match run ID: {persistence.match_run_id or 'not persisted'}")
    print(f"Eligible entities: {len(assessment.results)}")
    print("Trip matches: " + ", ".join(f"{status}={counts[status]}" for status in counts))
    print(f"StopTimeUpdates: total={len(stop_results)}, matched={stop_matched}")
    state = "inserted" if persistence.inserted else "already matched" if persistence.match_run_id else "disabled"
    print(f"Persistence: {state}")
    return 0


def main() -> None:
    raise SystemExit(run_cli())


if __name__ == "__main__":
    main()
