from __future__ import annotations

import csv
import hashlib
import json
import tempfile
import unittest
import zipfile
from pathlib import Path


CODE_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = CODE_DIR.parent

import sys

sys.path.insert(0, str(CODE_DIR))

from ghosttrace.domain import Observation, parse_time  # noqa: E402
from ghosttrace.engine import GhostTraceEngine, OPERATION_ORDER, PHASE_ORDER  # noqa: E402
from ghosttrace.intent import authorizes_deletion  # noqa: E402
from ghosttrace.policy import PolicyIndex  # noqa: E402
from ghosttrace.validation import validate_output  # noqa: E402
from ghosttrace.workflow import RESULT_FOR_STATUS  # noqa: E402
from main import write_output  # noqa: E402
from package_submission import build_archive  # noqa: E402


class GhostTraceEngineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.dataset = REPO_ROOT / "dataset"
        cls.engine = GhostTraceEngine(cls.dataset, cache_dir=REPO_ROOT / ".ghosttrace-cache")

    def test_every_request_resolves_to_one_identity(self) -> None:
        unresolved = [case.case_id for case in self.engine.cases if self.engine.identities.resolve(case) is None]
        self.assertEqual(unresolved, [])

    def test_every_manifest_artifact_exists_and_extracts(self) -> None:
        missing = [
            item.path
            for item in self.engine.evidence.files
            if not (self.dataset / item.path).is_file()
        ]
        self.assertEqual(missing, [])
        self.assertEqual(self.engine.evidence.extraction_warnings, [])

    def test_every_authorized_case_has_an_evidence_grounded_plan(self) -> None:
        decisions = self.engine.audit_all()
        empty = [
            decision.case_id
            for decision in decisions
            if decision.deletion_status != "NOT_APPLICABLE" and not decision.steps
        ]
        self.assertEqual(empty, [])
        self.assertEqual(
            [decision.case_id for decision in decisions if decision.warnings],
            [],
        )

    def test_structured_log_dialects_preserve_trace_ownership(self) -> None:
        expected = {
            "case_265": ("account_5015", "evidence_25", "ABSENT"),
            "case_363": ("account_9205", "evidence_34", "ABSENT"),
            "case_416": ("account_3075", "evidence_39", "ABSENT"),
            "case_521": ("account_1482", "evidence_48", "ABSENT"),
        }
        cases = {case.case_id: case for case in self.engine.cases}
        for case_id, (resource_id, evidence_id, state) in expected.items():
            case = cases[case_id]
            identity = self.engine.identities.resolve(case)
            aliases = self.engine.identities.aliases_at(identity, case)
            matching = [
                item
                for item in self.engine.evidence.eligible(case.audit_at)
                if item.resource_id == resource_id
                and item.evidence_id == evidence_id
                and self.engine.evidence.matches(item, aliases, case.audit_at)
            ]
            self.assertEqual(len(matching), 1, case_id)
            self.assertEqual(matching[0].state, state, case_id)

    def test_catalog_file_alias_joins_object_storage_evidence(self) -> None:
        case = next(item for item in self.engine.cases if item.case_id == "case_61")
        decision = self.engine.audit(case)
        finding = [
            step
            for step in decision.steps
            if step.resource_id == "object_19" and step.phase == "FINDING"
        ]
        self.assertEqual(len(finding), 1)
        self.assertEqual(finding[0].evidence_id, "evidence_46")

    def test_catalog_file_alias_is_not_reused_for_a_later_request(self) -> None:
        case = next(
            item
            for item in self.engine._load_cases(
                self.dataset / "sample_deletion_requests.csv"
            )
            if item.case_id == "case_07"
        )
        decision = self.engine.audit(case)
        self.assertNotIn("object_19", {step.resource_id for step in decision.steps})

    def test_intent_gate_matches_known_non_deletion_requests(self) -> None:
        not_applicable = {f"case_{number}" for number in range(533, 543)}
        classified = {case.case_id for case in self.engine.cases if not authorizes_deletion(case.request_text)}
        self.assertEqual(classified, not_applicable)

    def test_no_observation_crosses_case_cutoff(self) -> None:
        for case in self.engine.cases:
            for observation in self.engine.evidence.eligible(case.audit_at):
                self.assertLessEqual(observation.observed_at, case.audit_at)

    def test_structured_logs_do_not_promote_instance_names_to_resources(self) -> None:
        known_instance_ids = {
            "account_5",
            "analytics_4",
            "search_2",
            "support_5",
        }
        parsed_resources = {item.resource_id for item in self.engine.evidence.observations}
        self.assertTrue(known_instance_ids.isdisjoint(parsed_resources))

    def test_source_semantics_select_the_correct_policy_category(self) -> None:
        categories = {
            (item.resource_id, item.category)
            for item in self.engine.evidence.observations
        }
        self.assertIn(("support_001", "SUPPORT_ATTACHMENT"), categories)
        self.assertIn(("support_002", "SECONDARY_IDENTIFIER"), categories)
        self.assertIn(("analytics_9914", "MARKETING_PROFILE"), categories)

    def test_authoritative_catalog_link_overrides_ambiguous_ocr(self) -> None:
        case = next(
            item
            for item in self.engine._load_cases(self.dataset / "sample_deletion_requests.csv")
            if item.case_id == "case_15"
        )
        decision = self.engine.audit(case)
        self.assertNotIn("data_flow_1121", {step.resource_id for step in decision.steps})

    def test_negative_snapshot_proves_unambiguous_account_absence(self) -> None:
        cases = {
            item.case_id: item
            for item in self.engine._load_cases(self.dataset / "sample_deletion_requests.csv")
        }
        for case_id, resource_id in (("case_01", "account_001"), ("case_02", "account_002")):
            decision = self.engine.audit(cases[case_id])
            self.assertEqual(decision.deletion_status, "COMPLETE")
            self.assertEqual([step.resource_id for step in decision.steps], [resource_id])
            self.assertEqual(decision.steps[0].evidence_id, "evidence_54")

    def test_unambiguous_workflow_account_is_in_future_verification(self) -> None:
        cases = {
            item.case_id: item
            for item in self.engine._load_cases(self.dataset / "sample_deletion_requests.csv")
        }
        expected = {
            "case_06": "account_006",
            "case_07": "account_007",
            "case_08": "account_008",
            "case_10": "account_010",
            "case_22": "account_4990",
        }
        for case_id, resource_id in expected.items():
            decision = self.engine.audit(cases[case_id])
            matching = [step for step in decision.steps if step.resource_id == resource_id]
            self.assertEqual(len(matching), 1)
            self.assertEqual(matching[0].operation, "VERIFY_RECORD_ABSENT")
            self.assertEqual(matching[0].evidence_id, "")

    def test_batch_workflow_solver_resolves_only_invariant_targets(self) -> None:
        cases = {
            item.case_id: item
            for item in self.engine._load_cases(
                self.dataset / "sample_deletion_requests.csv"
            )
        }
        self.assertEqual(
            self.engine.workflow.account_resource(cases["case_18"]),
            "account_5521",
        )
        self.assertEqual(
            self.engine.workflow.account_resource(cases["case_20"]),
            "account_5037",
        )
        for ambiguous in ("case_25", "case_26", "case_28", "case_29", "case_31", "case_32"):
            self.assertEqual(self.engine.workflow.account_resource(cases[ambiguous]), "")

    def test_batch_workflow_solver_never_returns_conflicting_owner(self) -> None:
        for members in self.engine.workflow.groups.values():
            for case in members:
                resource = self.engine.workflow.account_resource(case)
                if not resource:
                    continue
                identity = self.engine.identities.resolve(case)
                self.assertIsNotNone(identity)
                aliases = self.engine.identities.aliases_at_time(
                    identity,
                    case.audit_at,
                    case.email,
                )
                self.assertNotEqual(
                    self.engine.evidence.ownership_relation(
                        resource, aliases, case.audit_at
                    ),
                    "CONFLICT",
                    case.case_id,
                )

    def test_batch_workflow_solver_matches_independent_subject_evidence(self) -> None:
        validated = 0
        for members in self.engine.workflow.groups.values():
            for case in members:
                resource = self.engine.workflow.account_resource(case)
                identity = self.engine.identities.resolve(case)
                expected_result = RESULT_FOR_STATUS.get(case.reported_status)
                if not resource or identity is None or expected_result is None:
                    continue
                candidates = self.engine.workflow.account_events.get(
                    (case.reported_at, expected_result), set()
                )
                independently_linked = {
                    candidate
                    for candidate in candidates
                    if any(
                        observation.subjects & identity.aliases
                        for observation in self.engine.evidence.observations_by_resource.get(
                            candidate, []
                        )
                    )
                }
                if len(independently_linked) != 1:
                    continue
                validated += 1
                self.assertEqual(resource, next(iter(independently_linked)), case.case_id)
        self.assertGreaterEqual(validated, 150)

    def test_structured_log_preserves_source_event_time(self) -> None:
        matching = [
            item
            for item in self.engine.evidence.observations
            if item.evidence_id == "evidence_94"
            and item.resource_id == "account_3610"
        ]
        self.assertEqual(len(matching), 1)
        self.assertEqual(
            matching[0].observed_at,
            parse_time("2026-09-26T08:37:56Z"),
        )

    def test_unique_workflow_target_requires_independent_anonymized_readback(self) -> None:
        cases = {
            item.case_id: item
            for item in self.engine._load_cases(self.dataset / "sample_deletion_requests.csv")
        }
        for case_id, resource_id, evidence_id in (
            ("case_04", "analytics_901", "evidence_56"),
            ("case_21", "analytics_5335", "evidence_92"),
        ):
            decision = self.engine.audit(cases[case_id])
            matching = [step for step in decision.steps if step.resource_id == resource_id]
            self.assertEqual(len(matching), 1)
            self.assertEqual(matching[0].operation, "VERIFY_RECORD_ANONYMIZED")
            self.assertEqual(matching[0].evidence_id, evidence_id)

    def test_attributed_account_target_requires_independent_absence_readback(self) -> None:
        case = next(
            item
            for item in self.engine._load_cases(self.dataset / "sample_deletion_requests.csv")
            if item.case_id == "case_21"
        )
        decision = self.engine.audit(case)
        matching = [step for step in decision.steps if step.resource_id == "account_6440"]
        self.assertEqual(len(matching), 1)
        self.assertEqual(matching[0].operation, "VERIFY_RECORD_ABSENT")
        self.assertEqual(matching[0].evidence_id, "evidence_92")

    def test_output_is_complete_and_contract_valid(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir) / "output.csv"
            report = write_output(self.engine, output)
            self.assertEqual(report["rows"], 518)
            validation = validate_output(output, self.dataset / "deletion_requests.csv")
            self.assertTrue(validation["valid"], validation["errors"][:5])

    def test_submission_archive_is_reproducible_and_dataset_free(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            first = Path(temp_dir) / "first.zip"
            second = Path(temp_dir) / "second.zip"
            build_archive(first)
            build_archive(second)
            self.assertEqual(
                hashlib.sha256(first.read_bytes()).digest(),
                hashlib.sha256(second.read_bytes()).digest(),
            )
            with zipfile.ZipFile(first) as archive:
                names = archive.namelist()
                self.assertEqual(archive.testzip(), None)
                self.assertEqual(len(names), len(set(names)))
                required_reports = {
                    "evaluation/evaluation_report.md": (
                        REPO_ROOT
                        / "code"
                        / "evaluation"
                        / "evaluation_report.md"
                    ),
                    "evaluation/usage_report.md": (
                        REPO_ROOT / "code" / "evaluation" / "usage_report.md"
                    ),
                }
                self.assertTrue(required_reports.keys() <= set(names))
                for archive_path, source_path in required_reports.items():
                    self.assertEqual(
                        archive.read(archive_path),
                        source_path.read_bytes(),
                    )
                self.assertFalse(
                    any(
                        name == "log.txt"
                        or name.startswith("dataset/")
                        or "__pycache__" in name
                        or name.endswith(".zip")
                        for name in names
                    )
                )

    def test_validator_rejects_unknown_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir) / "output.csv"
            write_output(self.engine, output)
            with output.open(encoding="utf-8", newline="") as handle:
                rows = list(csv.DictReader(handle))
            for row in rows:
                steps = json.loads(row["steps_json"])
                if steps and steps[0]["evidence_id"]:
                    steps[0]["evidence_id"] = "evidence_does_not_exist"
                    row["steps_json"] = json.dumps(steps)
                    break
            with output.open("w", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(
                    handle, fieldnames=["case_id", "deletion_status", "steps_json"]
                )
                writer.writeheader()
                writer.writerows(rows)
            validation = validate_output(output, self.dataset / "deletion_requests.csv")
            self.assertFalse(validation["valid"])
            self.assertTrue(
                any("unknown evidence_id" in error for error in validation["errors"])
            )

    def test_validator_rejects_incomplete_without_a_complete_plan(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir) / "output.csv"
            write_output(self.engine, output)
            with output.open(encoding="utf-8", newline="") as handle:
                rows = list(csv.DictReader(handle))
            target = next(
                row for row in rows if row["deletion_status"] == "INCOMPLETE"
            )
            target["steps_json"] = "[]"
            with output.open("w", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(
                    handle,
                    fieldnames=["case_id", "deletion_status", "steps_json"],
                )
                writer.writeheader()
                writer.writerows(rows)
            validation = validate_output(
                output, self.dataset / "deletion_requests.csv"
            )
            self.assertFalse(validation["valid"])
            self.assertTrue(
                any("INCOMPLETE plan lacks phases" in error for error in validation["errors"])
            )

    def test_steps_are_canonically_sorted(self) -> None:
        for decision in self.engine.audit_all():
            keys = [
                (PHASE_ORDER[step.phase], OPERATION_ORDER[step.operation], step.system_id, step.resource_id)
                for step in decision.steps
            ]
            self.assertEqual(keys, sorted(keys), decision.case_id)
            self.assertEqual([step.step_no for step in decision.steps], list(range(1, len(decision.steps) + 1)))

    def test_public_sample_status_accuracy_gate(self) -> None:
        sample_cases = self.engine._load_cases(self.dataset / "sample_deletion_requests.csv")
        predictions = {item.case_id: item for item in self.engine.audit_all(sample_cases)}
        with (self.dataset / "sample_output.csv").open(encoding="utf-8", newline="") as handle:
            expected = list(csv.DictReader(handle))
        hits = sum(predictions[row["case_id"]].deletion_status == row["deletion_status"] for row in expected)
        self.assertGreaterEqual(hits / len(expected), 0.90)


class IntentTests(unittest.TestCase):
    def test_multilingual_deletion(self) -> None:
        examples = [
            "Please permanently delete my account and personal data.",
            "Je demande la suppression définitive de mon compte.",
            "Solicito a exclusão permanente da conta.",
            "このアカウントの完全な削除を依頼します。",
        ]
        self.assertTrue(all(authorizes_deletion(text) for text in examples))

    def test_non_deletion(self) -> None:
        examples = [
            "Please correct my phone number and leave the account active.",
            "I need a data export, not deletion.",
            "Remove me from marketing lists only.",
        ]
        self.assertTrue(all(not authorizes_deletion(text) for text in examples))


class PolicyTests(unittest.TestCase):
    def test_policy_actions_are_loaded_from_the_supplied_csv(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "deletion_rules.csv"
            with path.open("w", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(
                    handle,
                    fieldnames=(
                        "rule_id",
                        "data_category",
                        "condition",
                        "required_action",
                    ),
                )
                writer.writeheader()
                writer.writerow(
                    {
                        "rule_id": "test_rule",
                        "data_category": "USER_PROFILE",
                        "condition": "Always",
                        "required_action": "REDACT",
                    }
                )
            policy = PolicyIndex(Path(temp_dir))
            observation = Observation(
                system_id="account_service",
                resource_id="account_test",
                category="USER_PROFILE",
                state="PRESENT",
                observed_at=parse_time("2026-01-01T00:00:00Z"),
                evidence_id="evidence_test",
            )
            self.assertEqual(policy.action_for(observation), "REDACT")
            self.assertEqual(policy.target_state(observation), "REDACTED")

    def test_retained_backup_uses_restore_control_when_active(self) -> None:
        policy = PolicyIndex(REPO_ROOT / "dataset")
        self.assertEqual(
            policy.rules["BACKUP_ARCHIVE"]["required_action"], "RETAIN"
        )
        active = Observation(
            system_id="backup_service",
            resource_id="backup_test",
            category="BACKUP_ARCHIVE",
            state="ACTIVE",
            observed_at=parse_time("2026-01-01T00:00:00Z"),
            evidence_id="evidence_test",
        )
        blocked = Observation(
            system_id=active.system_id,
            resource_id=active.resource_id,
            category=active.category,
            state="BLOCKED",
            observed_at=active.observed_at,
            evidence_id=active.evidence_id,
        )
        self.assertEqual(policy.action_for(active), "BLOCK_RESTORE")
        self.assertEqual(policy.target_state(blocked), "BLOCKED")


if __name__ == "__main__":
    unittest.main()

