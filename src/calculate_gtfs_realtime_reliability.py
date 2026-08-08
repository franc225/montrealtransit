from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import duckdb

from gtfs_realtime_reliability import ReliabilityError, assess_reliability, load_reliability_config
from gtfs_realtime_reliability_repository import (
    ReliabilityPersistenceResult,
    ensure_reliability_schema,
    load_reliability_source,
    persist_reliability,
)


DEFAULT_PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_WAREHOUSE_RELATIVE_PATH = Path("data/warehouse/montreal_transit.duckdb")


def project_root() -> Path:
    return Path(os.environ.get("MONTREAL_TRANSIT_PROJECT_ROOT", DEFAULT_PROJECT_ROOT)).resolve()


def calculate_reliability(match_run_id: str, warehouse: Path | None = None,
                          route_id: str | None = None, persist: bool = True):
    root = project_root()
    config = load_reliability_config(root)
    warehouse_path = (warehouse or root / DEFAULT_WAREHOUSE_RELATIVE_PATH).resolve()
    if not warehouse_path.is_file():
        raise ReliabilityError("DuckDB warehouse does not exist.")
    connection = duckdb.connect(str(warehouse_path))
    try:
        source = load_reliability_source(connection, match_run_id, config, route_id)
        assessment = assess_reliability(source.lineage, source.observations,
                                        source.relationships, config)
        if persist:
            ensure_reliability_schema(connection)
            result = persist_reliability(connection, source, assessment, config)
        else:
            result = ReliabilityPersistenceResult("", False)
        return assessment, result
    finally:
        connection.close()


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Calculate transparent GTFS-Realtime reliability indicators.")
    parser.add_argument("--match-run-id", required=True)
    parser.add_argument("--route-id")
    parser.add_argument("--warehouse", type=Path)
    parser.add_argument("--summary", action="store_true", help="Print concise summary (default).")
    parser.add_argument("--no-persist", action="store_true", help="Calculate without adding reliability tables or rows.")
    return parser


def run_cli(argv: list[str] | None = None) -> int:
    args = build_argument_parser().parse_args(argv)
    try:
        assessment, result = calculate_reliability(
            args.match_run_id, args.warehouse, args.route_id, not args.no_persist
        )
    except (ReliabilityError, OSError, duckdb.Error) as error:
        print(f"GTFS-Realtime reliability calculation failed: {error}", file=sys.stderr)
        return 1
    coverage = assessment.coverage
    counts = {name: sum(event.punctuality_classification == name for event in assessment.events)
              for name in ("EARLY", "ON_TIME", "LATE", "VERY_LATE")}
    print(f"Reliability run ID: {result.reliability_run_id or 'not persisted'}")
    print(f"Input scope: match run {assessment.lineage.match_run_id}")
    print(f"Static snapshot: {assessment.lineage.static_snapshot_identifier}")
    service_dates = sorted({event.observation.service_date for event in assessment.events
                            if event.observation.service_date is not None})
    print("Service dates: " + (", ".join(value.isoformat() for value in service_dates) or "none"))
    print(f"Candidate observations: {assessment.source_observation_count}")
    print(f"Canonical selected events: {len(assessment.events)}")
    print(f"Eligible events: {sum(event.eligibility_status == 'ELIGIBLE' for event in assessment.events)}")
    print(f"Classified events: {coverage.classified_event_count}")
    print("Punctuality: " + ", ".join(f"{name}={counts[name]}" for name in counts))
    for event_type in ("ARRIVAL", "DEPARTURE"):
        aggregate = next((item for item in assessment.aggregates
                          if item.dimension_type == "SYSTEM_CAPTURE_SCOPE" and
                          item.event_type == event_type), None)
        if aggregate is None:
            print(f"{event_type.title()}s: classified=0, on_time_ratio=None, median=None, p95=None")
        else:
            print(f"{event_type.title()}s: classified={aggregate.classified_event_count}, "
                  f"on_time_ratio={aggregate.on_time_ratio}, "
                  f"median={aggregate.median_delay_seconds}, p95={aggregate.p95_delay_seconds}")
    print(f"Reported cancellations: {assessment.reported_cancellation_count}")
    print(f"Trip matching ratio: {coverage.trip_matching_ratio}")
    print(f"Stop matching ratio: {coverage.stop_matching_ratio}")
    print(f"Comparison availability ratio: {coverage.comparison_availability_ratio}")
    print(f"Interpretation status: {assessment.overall_status}")
    print("Persistence: " + ("inserted" if result.inserted else
                            "already analyzed" if not args.no_persist else "disabled"))
    return 0


def main() -> None:
    raise SystemExit(run_cli())


if __name__ == "__main__":
    main()
