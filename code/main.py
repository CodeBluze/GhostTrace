#!/usr/bin/env python3
"""GhostTrace command-line entry point."""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

from ghosttrace.engine import GhostTraceEngine
from ghosttrace.validation import validate_output


CODE_DIR = Path(__file__).resolve().parent
REPO_ROOT = CODE_DIR.parent
DEFAULT_DATASET = REPO_ROOT / "dataset"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ghosttrace",
        description="Evidence-grounded personal-data deletion auditor.",
    )
    parser.add_argument("command", nargs="?", choices=("run", "validate", "diagnostics", "serve"), default="run")
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--output", type=Path, default=REPO_ROOT / "output.csv")
    parser.add_argument("--cache", type=Path, default=REPO_ROOT / ".ghosttrace-cache")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8787)
    return parser


def write_output(engine: GhostTraceEngine, output_path: Path) -> dict[str, object]:
    decisions = engine.audit_all()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["case_id", "deletion_status", "steps_json"])
        writer.writeheader()
        writer.writerows(decision.to_row() for decision in decisions)
    report = validate_output(output_path, engine.dataset_dir / "deletion_requests.csv")
    if not report["valid"]:
        raise RuntimeError("Output validation failed:\n" + "\n".join(report["errors"][:30]))
    return {
        "output": str(output_path.resolve()),
        "rows": len(decisions),
        "status_counts": {
            status: sum(decision.deletion_status == status for decision in decisions)
            for status in ("COMPLETE", "INCOMPLETE", "NOT_APPLICABLE")
        },
        "warnings": sum(bool(decision.warnings) for decision in decisions),
    }


def main() -> int:
    args = build_parser().parse_args()
    if args.command == "validate":
        report = validate_output(args.output, args.dataset / "deletion_requests.csv")
        print(json.dumps(report, indent=2))
        return 0 if report["valid"] else 2

    engine = GhostTraceEngine(args.dataset, cache_dir=args.cache)
    if args.command == "diagnostics":
        print(json.dumps(engine.diagnostics(), indent=2))
        return 0
    if args.command == "serve":
        from ghosttrace.server import serve

        serve(engine, host=args.host, port=args.port)
        return 0
    report = write_output(engine, args.output)
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit(130)
    except Exception as exc:
        print(f"GhostTrace failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
