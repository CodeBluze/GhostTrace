#!/usr/bin/env python3
"""Build a deterministic, dataset-free GhostTrace submission archive."""

from __future__ import annotations

import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
OUTPUT = ROOT / "code.zip"
EXCLUDED_PARTS = {"__pycache__", ".venv", "venv", "node_modules", ".git"}
EXCLUDED_SUFFIXES = {".pyc", ".pyo", ".zip", ".tar", ".gz", ".tgz", ".7z", ".rar"}


def included_files() -> list[Path]:
    candidates = [ROOT / "README.md", *(ROOT / "code").rglob("*")]
    return sorted(
        path
        for path in candidates
        if path.is_file()
        and not EXCLUDED_PARTS.intersection(path.relative_to(ROOT).parts)
        and path.suffix.casefold() not in EXCLUDED_SUFFIXES
    )


def main() -> int:
    with zipfile.ZipFile(OUTPUT, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path in included_files():
            archive.write(path, path.relative_to(ROOT))
    print(f"Created {OUTPUT} with {len(included_files())} files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
