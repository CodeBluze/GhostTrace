from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from typing import Iterable

from .domain import Case, normalize
from .evidence import EvidenceIndex, canonical_resource
from .identity import IdentityIndex
from .intent import authorizes_deletion


RESULT_FOR_STATUS = {
    "COMPLETE": "SUCCESS",
    "PARTIALLY_COMPLETE": "PARTIAL",
    "NOT_STARTED": "FAILED",
}


class WorkflowAttributor:
    """Resolve anonymous workflow targets through cutoff-safe constraints.

    The activity stream batches several deletion workflows around a common
    report time. Individual rows may omit the subject, so local nearest-event
    matching is unsafe. This solver instead builds candidate sets from workflow
    result compatibility, removes resources that conflict with cutoff-eligible
    ownership evidence, anchors explicit matches, and accepts a target only if
    every valid one-to-one assignment gives that case the same resource.
    """

    def __init__(
        self,
        identities: IdentityIndex,
        evidence: EvidenceIndex,
        cases: Iterable[Case],
    ) -> None:
        self.identities = identities
        self.evidence = evidence
        self.groups: dict[datetime, list[Case]] = defaultdict(list)
        for case in cases:
            if authorizes_deletion(case.request_text):
                self.groups[case.reported_at].append(case)
        self.account_events: dict[tuple[datetime, str], set[str]] = defaultdict(set)
        for reported_at in self.groups:
            for event in evidence.activity_events:
                offset_minutes = (
                    event["parsed_time"] - reported_at
                ).total_seconds() / 60
                if not (-75 <= offset_minutes <= -30):
                    continue
                if event.get("event_type") != "DELETE_RECORD":
                    continue
                if normalize(event.get("target_system")) != "account_service":
                    continue
                result = normalize(event.get("reported_result")).upper()
                resource = canonical_resource(event.get("resource_id", ""))
                if resource.startswith("account_"):
                    self.account_events[(reported_at, result)].add(resource)
        self._cache: dict[tuple[str, datetime], str] = {}

    def account_resource(self, target: Case) -> str:
        cache_key = (target.case_id, target.audit_at)
        if cache_key in self._cache:
            return self._cache[cache_key]
        members = self.groups.get(target.reported_at, [])
        if not members or target not in members:
            self._cache[cache_key] = ""
            return ""

        options: dict[str, set[str]] = {}
        for member in members:
            identity = self.identities.resolve(member)
            expected_result = RESULT_FOR_STATUS.get(member.reported_status)
            if identity is None or expected_result is None:
                self._cache[cache_key] = ""
                return ""
            aliases = self.identities.aliases_at_time(
                identity,
                target.audit_at,
                member.email,
            )
            candidates = set(
                self.account_events.get((member.reported_at, expected_result), set())
            )
            relationships = {
                resource: self.evidence.ownership_relation(
                    resource, aliases, target.audit_at
                )
                for resource in candidates
            }
            candidates = {
                resource
                for resource in candidates
                if relationships[resource] != "CONFLICT"
            }
            anchored = {
                resource
                for resource in candidates
                if relationships[resource] == "MATCH"
            }
            options[member.case_id] = anchored or candidates
            if not options[member.case_id]:
                self._cache[cache_key] = ""
                return ""

        viable: list[str] = []
        for resource in sorted(options[target.case_id]):
            remaining = [
                member for member in members if member.case_id != target.case_id
            ]
            if self._has_matching(remaining, options, {resource}):
                viable.append(resource)
        resolved = viable[0] if len(viable) == 1 else ""
        self._cache[cache_key] = resolved
        return resolved

    def _has_matching(
        self,
        remaining: list[Case],
        options: dict[str, set[str]],
        used: set[str],
    ) -> bool:
        if not remaining:
            return True
        current = min(
            remaining,
            key=lambda item: len(options[item.case_id] - used),
        )
        available = options[current.case_id] - used
        if not available:
            return False
        rest = [
            item for item in remaining if item.case_id != current.case_id
        ]
        return any(
            self._has_matching(rest, options, used | {resource})
            for resource in sorted(available)
        )
