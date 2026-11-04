from __future__ import annotations

import csv
from pathlib import Path

from .domain import Observation


SUPPORTED_ACTIONS = {"DELETE", "REDACT", "ANONYMIZE", "RETAIN"}
TARGET_STATE = {
    "DELETE": "ABSENT",
    "REDACT": "REDACTED",
    "ANONYMIZE": "ANONYMIZED",
    "RETAIN": "PRESENT",
    "BLOCK_RESTORE": "BLOCKED",
    "DISABLE_DATA_FLOW": "DISABLED",
}


class PolicyIndex:
    """Load the participant-supplied deletion policy and resolve required actions."""

    def __init__(self, dataset_dir: Path) -> None:
        self.path = dataset_dir / "deletion_rules.csv"
        self.rules: dict[str, dict[str, str]] = {}
        with self.path.open(encoding="utf-8", newline="") as handle:
            for row in csv.DictReader(handle):
                category = row.get("data_category", "").strip().upper()
                action = row.get("required_action", "").strip().upper()
                if not category or action not in SUPPORTED_ACTIONS:
                    raise ValueError(
                        f"Invalid deletion rule {row.get('rule_id', '<unknown>')}: "
                        f"category={category!r}, action={action!r}"
                    )
                if category in self.rules:
                    raise ValueError(f"Duplicate deletion rule for {category}")
                self.rules[category] = dict(row)
        if not self.rules:
            raise ValueError("deletion_rules.csv contains no usable rules")

    def supports(self, category: str) -> bool:
        return category in self.rules or category == "DATA_FLOW"

    def action_for(self, observation: Observation) -> str:
        if observation.category == "DATA_FLOW":
            return "DISABLE_DATA_FLOW"
        rule = self.rules.get(observation.category)
        if rule is None:
            raise KeyError(f"No deletion policy for {observation.category}")
        action = rule["required_action"].strip().upper()
        if observation.category == "BACKUP_ARCHIVE" and observation.state in {
            "ACTIVE",
            "BLOCKED",
        }:
            # The archive remains retained; the privacy control required by the
            # rule's condition is to block subject restoration from that copy.
            return "BLOCK_RESTORE"
        return action

    def target_state(self, observation: Observation) -> str:
        return TARGET_STATE[self.action_for(observation)]
