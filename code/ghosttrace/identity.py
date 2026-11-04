from __future__ import annotations

import csv
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from .domain import Case, Identity, normalize, parse_time


IDENTITY_FIELDS = {"email", "phone", "email_hash", "name"}


class IdentityIndex:
    def __init__(self, dataset_dir: Path):
        self.identities: dict[str, Identity] = {}
        self.alias_to_users: dict[str, set[str]] = defaultdict(set)
        self.audit_rows: list[dict[str, str]] = []
        self._load(dataset_dir)

    def _load(self, dataset_dir: Path) -> None:
        with (dataset_dir / "user_identities.csv").open(encoding="utf-8", newline="") as handle:
            for row in csv.DictReader(handle):
                identity = Identity(user_id=row["user_id"], current=dict(row))
                identity.add(row["user_id"], row["name"], row["email"], row["phone"], row["email_hash"])
                self.identities[identity.user_id] = identity

        with (dataset_dir / "user_audit_log.csv").open(encoding="utf-8", newline="") as handle:
            for row in csv.DictReader(handle):
                self.audit_rows.append(row)
                identity = self.identities.get(row["user_id"])
                if identity and row["field_name"] in IDENTITY_FIELDS:
                    identity.add(row["old_value"], row["new_value"])

        for user_id, identity in self.identities.items():
            for alias in identity.aliases:
                self.alias_to_users[alias].add(user_id)

    def resolve(self, case: Case) -> Identity | None:
        candidates = self.alias_to_users.get(normalize(case.email), set())
        if len(candidates) == 1:
            return self.identities[next(iter(candidates))]
        return None

    def aliases_at(self, identity: Identity, case: Case) -> set[str]:
        return self.aliases_at_time(
            identity,
            case.audit_at,
            case.email,
            include_all_history=True,
        )

    def aliases_at_time(
        self,
        identity: Identity,
        audit_at: datetime,
        request_email: str = "",
        *,
        include_all_history: bool = False,
    ) -> set[str]:
        """Return only aliases known by a specific decision cutoff.

        Cross-case workflow attribution uses the target decision's cutoff for
        every cohort member. This prevents a later case's evidence window from
        leaking future identity changes into an earlier audit.
        """
        aliases = {normalize(request_email), normalize(identity.user_id)}
        updated_at = parse_time(identity.current.get("updated_at"))
        if updated_at <= audit_at:
            aliases.update(normalize(identity.current.get(key)) for key in IDENTITY_FIELDS)
        for row in self.audit_rows:
            if row["user_id"] != identity.user_id or parse_time(row["event_time"]) > audit_at:
                continue
            if row["field_name"] in IDENTITY_FIELDS:
                aliases.add(normalize(row["old_value"]))
                aliases.add(normalize(row["new_value"]))
        if include_all_history:
            # The primary audit path deliberately searches every historical
            # alias ever attached to the stable subject. Evidence eligibility
            # still enforces the case cutoff.
            aliases.update(identity.aliases)
        return {alias for alias in aliases if alias}

