from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable


UTC = timezone.utc


def parse_time(value: str | None) -> datetime:
    if not value:
        return datetime.min.replace(tzinfo=UTC)
    return datetime.fromisoformat(value.strip().replace("Z", "+00:00"))


def normalize(value: Any) -> str:
    if value is None:
        return ""
    return " ".join(str(value).strip().casefold().split())


def is_identifier(value: Any) -> bool:
    text = normalize(value)
    return bool(text) and (
        "@" in text
        or text.startswith("user_")
        or text.startswith("sha256:")
        or (len(text) == 64 and all(c in "0123456789abcdef" for c in text))
        or sum(c.isdigit() for c in text) >= 7
    )


@dataclass(frozen=True)
class Case:
    case_id: str
    email: str
    requested_at: datetime
    reported_status: str
    reported_at: datetime
    audit_at: datetime
    request_text: str


@dataclass
class Identity:
    user_id: str
    aliases: set[str] = field(default_factory=set)
    current: dict[str, str] = field(default_factory=dict)

    def add(self, *values: Any) -> None:
        self.aliases.update(normalize(v) for v in values if normalize(v))


@dataclass(frozen=True)
class EvidenceFile:
    file_id: str
    path: str
    origin: str
    captured_at: datetime


@dataclass
class Observation:
    system_id: str
    resource_id: str
    category: str
    state: str
    observed_at: datetime
    evidence_id: str
    subjects: set[str] = field(default_factory=set)
    attributes: dict[str, Any] = field(default_factory=dict)
    confidence: float = 1.0

    @property
    def key(self) -> tuple[str, str]:
        return self.system_id, self.resource_id

    def matches(self, aliases: set[str]) -> bool:
        return bool(self.subjects & aliases)


@dataclass(frozen=True)
class Step:
    step_no: int
    phase: str
    system_id: str
    resource_id: str
    operation: str
    state: str
    evidence_id: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Decision:
    case_id: str
    deletion_status: str
    steps: list[Step]
    user_id: str = ""
    confidence: float = 1.0
    evidence_ids: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_row(self) -> dict[str, str]:
        import json

        return {
            "case_id": self.case_id,
            "deletion_status": self.deletion_status,
            "steps_json": json.dumps(
                [step.to_dict() for step in self.steps],
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ),
        }


def latest_by_resource(observations: Iterable[Observation]) -> list[Observation]:
    latest: dict[tuple[str, str], Observation] = {}
    for observation in observations:
        current = latest.get(observation.key)
        rank = (observation.observed_at, observation.confidence, observation.evidence_id)
        if current is None or rank > (
            current.observed_at,
            current.confidence,
            current.evidence_id,
        ):
            latest[observation.key] = observation
    return list(latest.values())

