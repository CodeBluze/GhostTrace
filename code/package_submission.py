#!/usr/bin/env python3
"""Build a deterministic, dataset-free GhostTrace submission archive."""

from __future__ import annotations

import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
OUTPUT = ROOT / "code.zip"
EXCLUDED_PARTS = {"__pycache__", ".venv", "venv", "node_modules", ".git"}
EXCLUDED_SUFFIXES = {".pyc", ".pyo", ".zip", ".tar", ".gz", ".tgz", ".7z", ".rar"}
FIXED_ZIP_TIMESTAMP = (1980, 1, 1, 0, 0, 0)
REPORT_ALIASES = {
    ROOT / "code" / "evaluation" / "evaluation_report.md": (
        "evaluation/evaluation_report.md"
    ),
    ROOT / "code" / "evaluation" / "usage_report.md": (
        "evaluation/usage_report.md"
    ),
}


def included_files() -> list[Path]:
    candidates = [ROOT / "README.md", *(ROOT / "code").rglob("*")]
    return sorted(
        path
        for path in candidates
        if path.is_file()
        and not EXCLUDED_PARTS.intersection(path.relative_to(ROOT).parts)
        and path.suffix.casefold() not in EXCLUDED_SUFFIXES
    )


def archive_entries() -> list[tuple[Path, str]]:
    """Return source files and their contract-compliant archive paths."""
    entries = [
        (path, path.relative_to(ROOT).as_posix())
        for path in included_files()
    ]
    entries.extend(REPORT_ALIASES.items())
    entries = sorted(entries, key=lambda item: item[1])
    names = [name for _path, name in entries]
    if len(names) != len(set(names)):
        raise ValueError("Submission archive contains duplicate paths")
    return entries


def build_archive(output: Path = OUTPUT) -> list[tuple[Path, str]]:
    """Write a byte-reproducible archive independent of checkout timestamps."""
    entries = archive_entries()
    with zipfile.ZipFile(output, "w") as archive:
        for path, archive_path in entries:
            info = zipfile.ZipInfo(
                archive_path,
                date_time=FIXED_ZIP_TIMESTAMP,
            )
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            archive.writestr(
                info,
                path.read_bytes(),
                compress_type=zipfile.ZIP_DEFLATED,
                compresslevel=9,
            )
    return entries


def main() -> int:
    entries = build_archive()
    print(f"Created {OUTPUT} with {len(entries)} files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
