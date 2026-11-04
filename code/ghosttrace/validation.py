from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

from .engine import OPERATION_ORDER, PHASE_ORDER


ALLOWED_STATUS = {"COMPLETE", "INCOMPLETE", "NOT_APPLICABLE"}
ALLOWED_TRIPLES = {
    ("FINDING", "REPORT_REMAINING_RECORD", "PRESENT"),
    ("FINDING", "REPORT_EXPOSED_PII", "PRESENT"),
    ("FINDING", "REPORT_ACTIVE_DATA_FLOW", "ACTIVE"),
    ("FINDING", "REPORT_RESTORE_RISK", "ACTIVE"),
    ("REMEDIATION", "DISABLE_DATA_FLOW", "DISABLED"),
    ("REMEDIATION", "BLOCK_RESTORE", "BLOCKED"),
    ("REMEDIATION", "DELETE_RECORD", "ABSENT"),
    ("REMEDIATION", "REDACT_RECORD", "REDACTED"),
    ("REMEDIATION", "ANONYMIZE_RECORD", "ANONYMIZED"),
    ("VERIFICATION", "VERIFY_RECORD_ABSENT", "ABSENT"),
    ("VERIFICATION", "VERIFY_RECORD_PRESENT", "PRESENT"),
    ("VERIFICATION", "VERIFY_RECORD_REDACTED", "REDACTED"),
    ("VERIFICATION", "VERIFY_RECORD_ANONYMIZED", "ANONYMIZED"),
    ("VERIFICATION", "VERIFY_DATA_FLOW_DISABLED", "DISABLED"),
    ("VERIFICATION", "VERIFY_RESTORE_BLOCKED", "BLOCKED"),
}
FIELDS = {"step_no", "phase", "system_id", "resource_id", "operation", "state", "evidence_id"}


def validate_output(output_path: Path, request_path: Path) -> dict[str, Any]:
    errors: list[str] = []
    dataset_dir = request_path.parent
    valid_evidence_ids: set[str] = set()
    for filename, field in (
        ("evidence_files.csv", "file_id"),
        ("system_activity.csv", "activity_id"),
        ("user_audit_log.csv", "audit_id"),
    ):
        source = dataset_dir / filename
        if not source.exists():
            continue
        with source.open(encoding="utf-8", newline="") as handle:
            valid_evidence_ids.update(
                row[field] for row in csv.DictReader(handle) if row.get(field)
            )
    with request_path.open(encoding="utf-8", newline="") as handle:
        expected = [row["case_id"] for row in csv.DictReader(handle)]
    with output_path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames != ["case_id", "deletion_status", "steps_json"]:
            errors.append(f"Invalid header: {reader.fieldnames}")
        rows = list(reader)
    actual = [row["case_id"] for row in rows]
    if actual != sorted(expected, key=_case_number):
        errors.append("Case IDs are missing, duplicated, or not canonically sorted.")
    for row in rows:
        case_id = row["case_id"]
        status = row["deletion_status"]
        if status not in ALLOWED_STATUS:
            errors.append(f"{case_id}: invalid status {status}")
        try:
            steps = json.loads(row["steps_json"])
        except json.JSONDecodeError as exc:
            errors.append(f"{case_id}: invalid steps_json: {exc}")
            continue
        if not isinstance(steps, list):
            errors.append(f"{case_id}: steps_json is not an array")
            continue
        if status == "NOT_APPLICABLE" and steps:
            errors.append(f"{case_id}: NOT_APPLICABLE must have []")
        if status == "COMPLETE" and not steps:
            errors.append(f"{case_id}: COMPLETE lacks evidence-backed verification")
        if status == "COMPLETE" and any(step.get("phase") != "VERIFICATION" for step in steps):
            errors.append(f"{case_id}: COMPLETE contains non-verification steps")
        if status == "INCOMPLETE":
            phases = {step.get("phase") for step in steps}
            missing_phases = {"FINDING", "REMEDIATION", "VERIFICATION"} - phases
            if missing_phases:
                errors.append(
                    f"{case_id}: INCOMPLETE plan lacks phases {sorted(missing_phases)}"
                )
        keys = []
        for index, step in enumerate(steps, 1):
            if set(step) != FIELDS:
                errors.append(f"{case_id} step {index}: invalid fields")
                continue
            if step["step_no"] != index:
                errors.append(f"{case_id} step {index}: non-consecutive step_no")
            triple = (step["phase"], step["operation"], step["state"])
            if triple not in ALLOWED_TRIPLES:
                errors.append(f"{case_id} step {index}: invalid phase/operation/state {triple}")
            if status == "COMPLETE" and not step["evidence_id"]:
                errors.append(f"{case_id} step {index}: COMPLETE verification lacks evidence")
            if status == "INCOMPLETE" and step["phase"] == "FINDING" and not step["evidence_id"]:
                errors.append(f"{case_id} step {index}: finding lacks evidence")
            if status == "INCOMPLETE" and step["phase"] != "FINDING" and step["evidence_id"]:
                errors.append(f"{case_id} step {index}: future action cites evidence")
            if step["evidence_id"] and step["evidence_id"] not in valid_evidence_ids:
                errors.append(
                    f"{case_id} step {index}: unknown evidence_id {step['evidence_id']}"
                )
            keys.append((PHASE_ORDER[step["phase"]], OPERATION_ORDER[step["operation"]], step["system_id"], step["resource_id"]))
        if keys != sorted(keys):
            errors.append(f"{case_id}: steps are not canonically ordered")
    return {"valid": not errors, "errors": errors, "row_count": len(rows), "expected_count": len(expected)}


def _case_number(case_id: str) -> tuple[str, int]:
    prefix, _, suffix = case_id.rpartition("_")
    return prefix, int(suffix) if suffix.isdigit() else 0

