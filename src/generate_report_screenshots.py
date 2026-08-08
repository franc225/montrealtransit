from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path


DEFAULT_PROJECT_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT_ENVIRONMENT_VARIABLE = "MONTREAL_TRANSIT_PROJECT_ROOT"


@dataclass(frozen=True)
class Screenshot:
    report_filename: str
    filename: str
    anchor: str
    width: int
    height: int
    visible_section_count: int
    zoom: float


SCREENSHOTS = (
    Screenshot(
        report_filename="index.html",
        filename="data-quality-overview.png",
        anchor="quality-overview",
        width=920,
        height=772,
        visible_section_count=3,
        zoom=1.0,
    ),
    Screenshot(
        report_filename="index.html",
        filename="data-quality-rule-results.png",
        anchor="quality-results",
        width=923,
        height=904,
        visible_section_count=3,
        zoom=0.8,
    ),
    Screenshot(
        report_filename="gtfs_realtime_reliability.html",
        filename="gtfs-realtime-reliability-overview.png",
        anchor="overview",
        width=1200,
        height=980,
        visible_section_count=2,
        zoom=0.72,
    ),
    Screenshot(
        report_filename="gtfs_realtime_reliability.html",
        filename="gtfs-realtime-reliability-performance.png",
        anchor="routes",
        width=1200,
        height=980,
        visible_section_count=2,
        zoom=0.68,
    ),
)


def resolve_project_root() -> Path:
    configured_root = os.environ.get(PROJECT_ROOT_ENVIRONMENT_VARIABLE)
    return Path(configured_root or DEFAULT_PROJECT_ROOT).resolve()


def find_browser(explicit_browser: Path | None = None) -> Path:
    if explicit_browser is not None:
        browser = explicit_browser.resolve()

        if not browser.is_file():
            raise RuntimeError(f"Browser executable was not found: {browser}")

        return browser

    candidates = (
        shutil.which("msedge"),
        shutil.which("microsoft-edge"),
        shutil.which("google-chrome"),
        shutil.which("chromium"),
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    )

    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return Path(candidate).resolve()

    raise RuntimeError(
        "Microsoft Edge, Google Chrome, or Chromium is required to generate "
        "report screenshots."
    )


def capture_screenshot(
    browser: Path,
    report_path: Path,
    output_path: Path,
    screenshot: Screenshot,
    temporary_directory: Path,
) -> None:
    temporary_output = temporary_directory / screenshot.filename
    temporary_report = temporary_directory / f"{screenshot.anchor}.html"
    browser_profile = temporary_directory / f"profile-{screenshot.anchor}"
    source_html = report_path.read_text(encoding="utf-8")
    visible_selectors = []
    selector = f"main > #{screenshot.anchor}"

    for _ in range(screenshot.visible_section_count):
        visible_selectors.append(selector)
        selector += " + section"

    screenshot_styles = f"""
    <base href="{report_path.parent.as_uri()}/">
    <style>
        header, footer, main > section {{
            display: none !important;
        }}

        {", ".join(visible_selectors)} {{
            display: block !important;
        }}

        main {{
            padding-top: 0 !important;
        }}

        body {{
            zoom: {screenshot.zoom};
        }}
    </style>
"""

    if "</head>" not in source_html:
        raise RuntimeError("Generated report does not contain a closing head tag.")

    temporary_report.write_text(
        source_html.replace("</head>", f"{screenshot_styles}</head>", 1),
        encoding="utf-8",
    )
    command = [
        str(browser),
        "--headless",
        "--disable-gpu",
        "--disable-extensions",
        "--hide-scrollbars",
        "--no-first-run",
        "--force-device-scale-factor=1",
        "--virtual-time-budget=1000",
        f"--user-data-dir={browser_profile}",
        f"--window-size={screenshot.width},{screenshot.height}",
        f"--screenshot={temporary_output}",
        temporary_report.as_uri(),
    ]

    completed_process = subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
    )

    if completed_process.returncode != 0:
        raise RuntimeError(
            f"Browser screenshot failed for '{screenshot.anchor}' "
            f"with exit code {completed_process.returncode}."
        )

    if not temporary_output.is_file() or temporary_output.stat().st_size == 0:
        raise RuntimeError(
            f"Browser did not create screenshot '{screenshot.filename}'."
        )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_output.replace(output_path)


def generate_screenshots(browser_path: Path | None = None, report: str = "all") -> None:
    project_root = resolve_project_root()
    output_directory = (
        project_root / "docs" / "assets" / "screenshots"
    ).resolve()

    if not output_directory.is_relative_to(project_root):
        raise RuntimeError("Screenshot output directory is outside the project root.")

    browser = find_browser(browser_path)

    with tempfile.TemporaryDirectory(prefix="montrealtransit-screenshots-") as name:
        temporary_directory = Path(name)

        selected = tuple(
            screenshot for screenshot in SCREENSHOTS
            if report == "all"
            or (report == "static" and screenshot.report_filename == "index.html")
            or (report == "realtime" and screenshot.report_filename != "index.html")
        )
        for screenshot in selected:
            report_path = (project_root / "docs" / screenshot.report_filename).resolve()
            if not report_path.is_file():
                raise RuntimeError(
                    f"Generated report was not found: {screenshot.report_filename}"
                )
            capture_screenshot(
                browser=browser,
                report_path=report_path,
                output_path=output_directory / screenshot.filename,
                screenshot=screenshot,
                temporary_directory=temporary_directory,
            )
            print(f"Screenshot created: {output_directory / screenshot.filename}")


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate README screenshots from the published HTML reports."
    )
    parser.add_argument(
        "--browser",
        type=Path,
        default=None,
        help="Optional path to a Microsoft Edge, Chrome, or Chromium executable.",
    )
    parser.add_argument(
        "--report",
        choices=("all", "static", "realtime"),
        default="all",
        help="Select which report screenshots to generate (default: all).",
    )
    return parser.parse_args()


def main() -> None:
    arguments = parse_arguments()
    generate_screenshots(arguments.browser, arguments.report)


if __name__ == "__main__":
    main()
