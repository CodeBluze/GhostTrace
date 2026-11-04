#!/usr/bin/env python3
"""Evaluate GhostTrace against participant-visible sample answers."""

from __future__ import annotations

import csv
import json
import sys
from collections import Counter
from pathlib import Path


CODE_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = CODE_DIR.parent
sys.path.insert(0, str(CODE_DIR))

from ghosttrace.engine import GhostTraceEngine  # noqa: E402


def main() -> int:
    dataset = REPO_ROOT / "dataset"
    engine = GhostTraceEngine(dataset, cache_dir=REPO_ROOT / ".ghosttrace-cache")
    cases = engine._load_cases(dataset / "sample_deletion_requests.csv")
    predicted = {decision.case_id: decision for decision in engine.audit_all(cases)}
    with (dataset / "sample_output.csv").open(encoding="utf-8", newline="") as handle:
        expected = {row["case_id"]: row for row in csv.DictReader(handle)}

    status_hits = 0
    exact_hits = 0
    operation_sequence_hits = 0
    step_field_hits = 0
    step_field_total = 0
    expected_steps = predicted_steps = matched_steps = 0
    failures: list[dict[str, object]] = []
    for case_id, target in expected.items():
        decision = predicted[case_id]
        target_steps = json.loads(target["steps_json"])
        actual_steps = [step.to_dict() for step in decision.steps]
        status_ok = decision.deletion_status == target["deletion_status"]
        exact_ok = status_ok and actual_steps == target_steps
        target_sequence = [
            (step["phase"], step["system_id"], step["resource_id"], step["operation"], step["state"])
            for step in target_steps
        ]
        actual_sequence = [
            (step["phase"], step["system_id"], step["resource_id"], step["operation"], step["state"])
            for step in actual_steps
        ]
        status_hits += status_ok
        exact_hits += exact_ok
        operation_sequence_hits += target_sequence == actual_sequence
        field_names = ("step_no", "phase", "system_id", "resource_id", "operation", "state", "evidence_id")
        for index in range(max(len(target_steps), len(actual_steps))):
            expected_step = target_steps[index] if index < len(target_steps) else {}
            actual_step = actual_steps[index] if index < len(actual_steps) else {}
            step_field_hits += sum(expected_step.get(field) == actual_step.get(field) for field in field_names)
            step_field_total += len(field_names)
        expected_steps += len(target_steps)
        predicted_steps += len(actual_steps)
        matched_steps += sum(step in target_steps for step in actual_steps)
        if not exact_ok:
            failures.append(
                {
                    "case_id": case_id,
                    "expected_status": target["deletion_status"],
                    "actual_status": decision.deletion_status,
                    "expected_steps": len(target_steps),
                    "actual_steps": len(actual_steps),
                    "warnings": decision.warnings,
                }
            )
    report = {
        "cases": len(expected),
        "case_status_accuracy": status_hits / len(expected),
        "exact_row_accuracy": exact_hits / len(expected),
        "step_field_accuracy": step_field_hits / step_field_total if step_field_total else 1.0,
        "operation_sequence_accuracy": operation_sequence_hits / len(expected),
        "exact_sequence_accuracy": exact_hits / len(expected),
        "step_precision": matched_steps / predicted_steps if predicted_steps else 0.0,
        "step_recall": matched_steps / expected_steps if expected_steps else 0.0,
        "predicted_statuses": Counter(d.deletion_status for d in predicted.values()),
        "failures": failures,
        "diagnostics": engine.diagnostics(),
    }
    print(json.dumps(report, indent=2, default=dict))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
