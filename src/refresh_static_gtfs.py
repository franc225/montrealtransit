from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import webbrowser
import zipfile
import pytz
import os
import uuid
from datetime import datetime
from pathlib import Path



DEFAULT_PROJECT_ROOT = Path(__file__).resolve().parents[1]

PROJECT_ROOT = Path(
    os.environ.get(
        "MONTREAL_TRANSIT_PROJECT_ROOT",
        str(DEFAULT_PROJECT_ROOT),
    )
).resolve()

STM_GTFS_URL = "https://www.stm.info/sites/default/files/gtfs/gtfs_stm.zip"

ARCHIVE_DIR = PROJECT_ROOT / "data" / "archive" / "gtfs"
CURRENT_GTFS_DIR = PROJECT_ROOT / "data" / "raw" / "gtfs" / "current"
METADATA_PATH = PROJECT_ROOT / "data" / "raw" / "gtfs" / "refresh_metadata.json"
REPORT_PATH = PROJECT_ROOT / "docs" / "index.html"

MONTREAL_TIMEZONE = pytz.timezone("America/Montreal")

REQUIRED_GTFS_FILES = {
    "agency.txt",
    "calendar.txt",
    "calendar_dates.txt",
    "feed_info.txt",
    "routes.txt",
    "shapes.txt",
    "stop_times.txt",
    "stops.txt",
    "trips.txt",
}

def montreal_now() -> datetime:
    return datetime.now(MONTREAL_TIMEZONE)


def calculate_sha256(file_path: Path) -> str:
    digest = hashlib.sha256()

    with file_path.open("rb") as file_handle:
        for chunk in iter(lambda: file_handle.read(1024 * 1024), b""):
            digest.update(chunk)

    return digest.hexdigest()


def download_file(download_url: str, destination_path: Path) -> None:
    print("Downloading current STM static GTFS feed...")

    request = urllib.request.Request(
        download_url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 "
                "(compatible; MontrealTransitReliability/1.0)"
            )
        },
    )

    with urllib.request.urlopen(request, timeout=90) as response:
        status_code = response.getcode()

        if status_code != 200:
            raise RuntimeError(
                f"Download failed with HTTP status {status_code}."
            )

        with destination_path.open("wb") as output_file:
            while True:
                chunk = response.read(1024 * 1024)

                if not chunk:
                    break

                output_file.write(chunk)

    print(f"Downloaded: {destination_path.name}")


def validate_archive(archive_path: Path) -> None:
    if not zipfile.is_zipfile(archive_path):
        raise RuntimeError(
            "The downloaded file is not a valid ZIP archive. "
            "The STM download URL may have changed."
        )

    with zipfile.ZipFile(archive_path) as zip_file:
        invalid_member = zip_file.testzip()

        if invalid_member is not None:
            raise RuntimeError(
                f"ZIP integrity check failed for: {invalid_member}"
            )

        for member_name in zip_file.namelist():
            member_path = Path(member_name)

            if member_path.is_absolute() or ".." in member_path.parts:
                raise RuntimeError(
                    "Archive contains an unsafe file path and cannot be extracted."
                )

        archived_file_names = {
            Path(member_name).name
            for member_name in zip_file.namelist()
            if not member_name.endswith("/")
        }

        missing_files = sorted(
            REQUIRED_GTFS_FILES - archived_file_names
        )

        if missing_files:
            raise RuntimeError(
                "Archive is missing required GTFS files: "
                + ", ".join(missing_files)
            )

    print("Archive validation passed.")


def find_gtfs_root(extraction_directory: Path) -> Path:
    candidate_directories = [extraction_directory]

    candidate_directories.extend(
        sorted(
            [
                path
                for path in extraction_directory.rglob("*")
                if path.is_dir()
            ],
            key=lambda path: len(path.parts),
        )
    )

    for candidate_directory in candidate_directories:
        file_names = {
            path.name
            for path in candidate_directory.iterdir()
            if path.is_file()
        }

        if REQUIRED_GTFS_FILES.issubset(file_names):
            return candidate_directory

    raise RuntimeError(
        "Unable to locate the GTFS root folder after extraction."
    )


def replace_current_gtfs(gtfs_source_directory: Path) -> None:
    staging_directory = (
        CURRENT_GTFS_DIR.parent / "__current_gtfs_staging"
    )

    if staging_directory.exists():
        shutil.rmtree(staging_directory)

    CURRENT_GTFS_DIR.parent.mkdir(parents=True, exist_ok=True)

    shutil.copytree(gtfs_source_directory, staging_directory)

    if CURRENT_GTFS_DIR.exists():
        shutil.rmtree(CURRENT_GTFS_DIR)

    staging_directory.rename(CURRENT_GTFS_DIR)

    print(f"Current GTFS files replaced: {CURRENT_GTFS_DIR}")


def save_refresh_metadata(
    archive_path: Path,
    archive_sha256: str,
    download_url: str,
) -> None:
    METADATA_PATH.parent.mkdir(parents=True, exist_ok=True)

    metadata = {
        "download_url": download_url,
        "archive_path": str(archive_path.relative_to(PROJECT_ROOT)),
        "archive_sha256": archive_sha256,
        "refreshed_at_montreal": montreal_now().isoformat(),
        "current_gtfs_path": str(
            CURRENT_GTFS_DIR.relative_to(PROJECT_ROOT)
        ),
    }

    METADATA_PATH.write_text(
        json.dumps(metadata, indent=2),
        encoding="utf-8",
    )

    print(f"Refresh metadata saved: {METADATA_PATH}")


def run_pipeline_script(
    script_name: str,
    *script_arguments: str,
) -> None:
    script_path = PROJECT_ROOT / "src" / script_name

    if not script_path.exists():
        raise FileNotFoundError(
            f"Pipeline script not found: {script_path}"
        )

    print()
    print("=" * 70)
    print(f"Running: {script_name}")
    print("=" * 70)

    subprocess.run(
        [
            sys.executable,
            str(script_path),
            *script_arguments,
        ],
        cwd=PROJECT_ROOT,
        check=True,
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Download the current STM static GTFS feed, refresh DuckDB, "
            "run data quality checks, and regenerate the HTML report."
        )
    )

    parser.add_argument(
        "--download-url",
        default=STM_GTFS_URL,
        help=(
            "Override the STM GTFS ZIP URL if the official source changes."
        ),
    )

    parser.add_argument(
        "--open-report",
        action="store_true",
        help="Open docs/index.html in the default browser after completion.",
    )

    arguments = parser.parse_args()

    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)

    refresh_timestamp = montreal_now().strftime("%Y%m%d_%H%M%S")

    with tempfile.TemporaryDirectory() as temp_directory_text:
        temp_directory = Path(temp_directory_text)

        downloaded_archive = temp_directory / "gtfs_stm.zip"
        extraction_directory = temp_directory / "extracted"

        try:
            download_file(
                download_url=arguments.download_url,
                destination_path=downloaded_archive,
            )

            validate_archive(downloaded_archive)

            archive_sha256 = calculate_sha256(downloaded_archive)

            archive_path = ARCHIVE_DIR / (
                f"gtfs_stm_{refresh_timestamp}_{archive_sha256[:8]}.zip"
            )

            shutil.copy2(downloaded_archive, archive_path)

            print(f"Archived GTFS snapshot: {archive_path}")
            print(f"SHA-256: {archive_sha256}")

            with zipfile.ZipFile(downloaded_archive) as zip_file:
                zip_file.extractall(extraction_directory)

            gtfs_source_directory = find_gtfs_root(extraction_directory)

            replace_current_gtfs(gtfs_source_directory)

            save_refresh_metadata(
                archive_path=archive_path,
                archive_sha256=archive_sha256,
                download_url=arguments.download_url,
            )

        except Exception as error:
            raise SystemExit(
                "\nRefresh stopped before the data pipeline was executed.\n"
                f"Reason: {error}"
            ) from error

    quality_run_id = str(uuid.uuid4())

    print()
    print(f"Quality run ID for this refresh: {quality_run_id}")

    run_pipeline_script("ingest_gtfs.py")

    run_pipeline_script(
        "run_quality_checks.py",
        "--run-id",
        quality_run_id,
    )

    run_pipeline_script(
        "generate_quality_report.py",
        "--run-id",
        quality_run_id,
    )

    if not REPORT_PATH.exists():
        raise RuntimeError(
            f"Report was not generated: {REPORT_PATH}"
        )

    report_content = REPORT_PATH.read_text(encoding="utf-8")

    if quality_run_id not in report_content:
        raise RuntimeError(
            "The generated report does not contain the quality run created "
            f"by this refresh: {quality_run_id}"
        )

    print(
        "Verified report quality run: "
        f"{quality_run_id}"
    )

    print()
    print("=" * 70)
    print("Refresh completed successfully.")
    print(f"Updated report: {REPORT_PATH}")
    print("=" * 70)

    if arguments.open_report:
        webbrowser.open(REPORT_PATH.resolve().as_uri())


if __name__ == "__main__":
    main()