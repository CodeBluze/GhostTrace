from __future__ import annotations

import csv
from collections import defaultdict
from pathlib import Path
from typing import Iterable

from .domain import Case, Decision, Observation, Step, latest_by_resource, parse_time
from .evidence import EvidenceIndex
from .identity import IdentityIndex
from .intent import authorizes_deletion
from .policy import PolicyIndex
from .workflow import WorkflowAttributor


PHASE_ORDER = {"FINDING": 0, "REMEDIATION": 1, "VERIFICATION": 2}
OPERATION_ORDER = {
    "REPORT_REMAINING_RECORD": 0,
    "REPORT_EXPOSED_PII": 1,
    "REPORT_ACTIVE_DATA_FLOW": 2,
    "REPORT_RESTORE_RISK": 3,
    "DISABLE_DATA_FLOW": 0,
    "BLOCK_RESTORE": 1,
    "DELETE_RECORD": 2,
    "REDACT_RECORD": 3,
    "ANONYMIZE_RECORD": 4,
    "VERIFY_RECORD_ABSENT": 0,
    "VERIFY_RECORD_PRESENT": 1,
    "VERIFY_RECORD_REDACTED": 2,
    "VERIFY_RECORD_ANONYMIZED": 3,
    "VERIFY_DATA_FLOW_DISABLED": 4,
    "VERIFY_RESTORE_BLOCKED": 5,
}

class GhostTraceEngine:
    def __init__(self, dataset_dir: Path, cache_dir: Path | None = None):
        self.dataset_dir = dataset_dir.resolve()
        self.identities = IdentityIndex(self.dataset_dir)
        self.policy = PolicyIndex(self.dataset_dir)
        self.evidence = EvidenceIndex(self.dataset_dir, cache_dir=cache_dir)
        self.cases = self._load_cases(self.dataset_dir / "deletion_requests.csv")
        context_cases = list(self.cases)
        sample_path = self.dataset_dir / "sample_deletion_requests.csv"
        if sample_path.is_file():
            context_cases.extend(self._load_cases(sample_path))
        self.workflow = WorkflowAttributor(
            self.identities,
            self.evidence,
            context_cases,
        )

    @staticmethod
    def _load_cases(path: Path) -> list[Case]:
        with path.open(encoding="utf-8", newline="") as handle:
            return [
                Case(
                    case_id=row["case_id"],
                    email=row["email"],
                    requested_at=parse_time(row["requested_at"]),
                    reported_status=row["reported_status"],
                    reported_at=parse_time(row["reported_at"]),
                    audit_at=parse_time(row["audit_at"]),
                    request_text=row["request_text"],
                )
                for row in csv.DictReader(handle)
            ]

    def audit_all(self, cases: Iterable[Case] | None = None) -> list[Decision]:
        selected = list(cases or self.cases)
        return sorted((self.audit(case) for case in selected), key=lambda item: _case_number(item.case_id))

    def audit(self, case: Case) -> Decision:
        identity = self.identities.resolve(case)
        if not authorizes_deletion(case.request_text):
            return Decision(case.case_id, "NOT_APPLICABLE", [], user_id=identity.user_id if identity else "")
        if identity is None:
            return Decision(
                case.case_id,
                "INCOMPLETE",
                [],
                confidence=0.0,
                warnings=["Identity could not be resolved uniquely."],
            )
        aliases = self.identities.aliases_at(identity, case)
        observations = [
            observation
            for observation in self.evidence.eligible(case.audit_at)
            if self.evidence.matches(observation, aliases, case.audit_at)
            and not (
                observation.attributes.get("inference") == "catalog_file_alias"
                and observation.observed_at < case.requested_at
            )
        ]
        workflow_account = self.workflow.account_resource(
            case
        ) or self.evidence.unambiguous_account_resource(case, aliases)
        if workflow_account and not any(
            item.resource_id == workflow_account for item in observations
        ):
            absence_proof = self.evidence.verified_account_readback(
                workflow_account, case, aliases
            ) or self.evidence.prove_account_absence(workflow_account, case)
            if absence_proof is not None:
                observations.append(absence_proof)
        anonymization_proof = self.evidence.verified_unique_anonymization(case, aliases)
        if anonymization_proof is not None and not any(
            item.resource_id == anonymization_proof.resource_id for item in observations
        ):
            observations.append(anonymization_proof)
        observations = latest_by_resource(observations)
        observations = [
            item for item in observations if self.policy.supports(item.category)
        ]

        # Prefer state evidence close to the audit cutoff while retaining one
        # authoritative state per resource. Resource history is still available
        # to the investigator UI through the EvidenceIndex.
        violations = [item for item in observations if not self._compliant(item)]
        status = "INCOMPLETE" if violations else "COMPLETE"
        planned_verifications: list[Observation] = []
        if (
            status == "INCOMPLETE"
            and workflow_account
            and not any(item.resource_id == workflow_account for item in observations)
        ):
            planned_verifications.append(
                Observation(
                    system_id="account_service",
                    resource_id=workflow_account,
                    category="USER_PROFILE",
                    state="ABSENT",
                    observed_at=case.audit_at,
                    evidence_id="",
                    confidence=0.75,
                )
            )
        steps = self._steps(observations, status, planned_verifications)
        warnings: list[str] = []
        if not observations:
            status = "INCOMPLETE"
            warnings.append("No cutoff-eligible subject-linked state evidence was discovered.")
        confidence = min((item.confidence for item in observations), default=0.25)
        return Decision(
            case_id=case.case_id,
            deletion_status=status,
            steps=steps,
            user_id=identity.user_id,
            confidence=confidence,
            evidence_ids=sorted({item.evidence_id for item in observations}),
            warnings=warnings,
        )

    def _compliant(self, observation: Observation) -> bool:
        return observation.state == self.policy.target_state(observation)

    def _steps(
        self,
        observations: list[Observation],
        status: str,
        planned_verifications: list[Observation] | None = None,
    ) -> list[Step]:
        raw: list[tuple[str, str, str, str, str, str]] = []
        if status == "INCOMPLETE":
            for observation in observations:
                if self._compliant(observation):
                    continue
                action = self.policy.action_for(observation)
                if action == "DELETE":
                    finding = ("REPORT_REMAINING_RECORD", "PRESENT")
                    remediation = ("DELETE_RECORD", "ABSENT")
                elif action in {"REDACT", "ANONYMIZE"}:
                    finding = ("REPORT_EXPOSED_PII", "PRESENT")
                    remediation = (("REDACT_RECORD", "REDACTED") if action == "REDACT" else ("ANONYMIZE_RECORD", "ANONYMIZED"))
                elif action == "DISABLE_DATA_FLOW":
                    finding = ("REPORT_ACTIVE_DATA_FLOW", "ACTIVE")
                    remediation = ("DISABLE_DATA_FLOW", "DISABLED")
                elif action == "BLOCK_RESTORE":
                    finding = ("REPORT_RESTORE_RISK", "ACTIVE")
                    remediation = ("BLOCK_RESTORE", "BLOCKED")
                else:
                    continue
                raw.append(("FINDING", observation.system_id, observation.resource_id, finding[0], finding[1], observation.evidence_id))
                raw.append(("REMEDIATION", observation.system_id, observation.resource_id, remediation[0], remediation[1], ""))

        verification_observations = [
            *observations,
            *(planned_verifications or []),
        ]
        for observation in verification_observations:
            action = self.policy.action_for(observation)
            operation = {
                "DELETE": "VERIFY_RECORD_ABSENT",
                "RETAIN": "VERIFY_RECORD_PRESENT",
                "REDACT": "VERIFY_RECORD_REDACTED",
                "ANONYMIZE": "VERIFY_RECORD_ANONYMIZED",
                "DISABLE_DATA_FLOW": "VERIFY_DATA_FLOW_DISABLED",
                "BLOCK_RESTORE": "VERIFY_RESTORE_BLOCKED",
            }[action]
            evidence_id = observation.evidence_id if status == "COMPLETE" else ""
            raw.append((
                "VERIFICATION",
                observation.system_id,
                observation.resource_id,
                operation,
                self.policy.target_state(observation),
                evidence_id,
            ))

        raw.sort(key=lambda item: (PHASE_ORDER[item[0]], OPERATION_ORDER[item[3]], item[1], item[2]))
        return [
            Step(index, phase, system, resource, operation, state, evidence)
            for index, (phase, system, resource, operation, state, evidence) in enumerate(raw, 1)
        ]

    def diagnostics(self) -> dict[str, object]:
        return {
            "cases": len(self.cases),
            "identities": len(self.identities.identities),
            "policy_rules": len(self.policy.rules),
            "evidence_files": len(self.evidence.files),
            "observations": len(self.evidence.observations),
            "extraction_warnings": list(self.evidence.extraction_warnings),
        }


def _case_number(case_id: str) -> tuple[str, int]:
    prefix, _, suffix = case_id.rpartition("_")
    return prefix, int(suffix) if suffix.isdigit() else 0

