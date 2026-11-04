from __future__ import annotations

import csv
import json
import tempfile
import unittest
from pathlib import Path


CODE_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = CODE_DIR.parent

import sys

sys.path.insert(0, str(CODE_DIR))

from ghosttrace.engine import GhostTraceEngine, OPERATION_ORDER, PHASE_ORDER  # noqa: E402
from ghosttrace.intent import authorizes_deletion  # noqa: E402
from ghosttrace.validation import validate_output  # noqa: E402
from main import write_output  # noqa: E402


class GhostTraceEngineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.dataset = REPO_ROOT / "dataset"
        cls.engine = GhostTraceEngine(cls.dataset, cache_dir=REPO_ROOT / ".ghosttrace-cache")

    def test_every_request_resolves_to_one_identity(self) -> None:
        unresolved = [case.case_id for case in self.engine.cases if self.engine.identities.resolve(case) is None]
        self.assertEqual(unresolved, [])

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


if __name__ == "__main__":
    unittest.main()

