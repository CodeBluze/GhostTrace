from __future__ import annotations

import csv
import json
import re
import shutil
import sqlite3
import subprocess
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterator

from .domain import EvidenceFile, Observation, normalize, parse_time


RESOURCE_RE = re.compile(
    r"\b(account|analytics|audit_record|backup|data_flow|export|marketing|object|search|support|tombstone)[_\s-]?(\d+)\b",
    re.I,
)
EMAIL_RE = re.compile(r"[A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,}", re.I)
USER_RE = re.compile(r"\buser[_\s-]?(\d+)\b", re.I)
HASH_RE = re.compile(r"\b[a-f0-9]{64}\b", re.I)
KV_RE = re.compile(r"([A-Za-z_][\w.-]*)=(\"[^\"]*\"|'[^']*'|\S+)")

PREFIX_META = {
    "account": ("account_service", "USER_PROFILE"),
    "analytics": ("analytics_platform", "ANALYTICS_PROFILE"),
    "audit_record": ("security_service", "SECURITY_AUDIT_LOG"),
    "backup": ("backup_service", "BACKUP_ARCHIVE"),
    "data_flow": ("integration_service", "DATA_FLOW"),
    "export": ("export_service", "USER_EXPORT"),
    "marketing": ("marketing_platform", "MARKETING_PROFILE"),
    "object": ("object_storage", "SUPPORT_ATTACHMENT"),
    "search": ("search_service", "SEARCH_INDEX_ENTRY"),
    "support": ("support_platform", "SUPPORT_MESSAGE"),
    "tombstone": ("identity_service", "ACCOUNT_TOMBSTONE"),
}

TABLE_META = {
    "customer_profiles": ("account_service", "USER_PROFILE"),
    "customer_search_documents": ("search_service", "SEARCH_INDEX_ENTRY"),
    "customer_analytics_profiles": ("analytics_platform", "ANALYTICS_PROFILE"),
    "support_records": ("support_platform", "SUPPORT_MESSAGE"),
    "customer_exports": ("export_service", "USER_EXPORT"),
    "connector_subscriptions": ("integration_service", "DATA_FLOW"),
    "backup_restore_entries": ("backup_service", "BACKUP_ARCHIVE"),
    "security_audit_records": ("security_service", "SECURITY_AUDIT_LOG"),
}

COLLECTION_CATEGORY = {
    "customer_profiles": "USER_PROFILE",
    "profile_lookups": "USER_PROFILE",
    "customer_search_documents": "SEARCH_INDEX_ENTRY",
    "index_queries": "SEARCH_INDEX_ENTRY",
    "customer_analytics_reports": "ANALYTICS_PROFILE",
    "profiles": "ANALYTICS_PROFILE",
    "support_conversations": "SUPPORT_MESSAGE",
    "tickets": "SUPPORT_MESSAGE",
    "ticket_attachments": "SUPPORT_ATTACHMENT",
    "customer_export_jobs": "USER_EXPORT",
    "exports": "USER_EXPORT",
    "connector_configurations": "DATA_FLOW",
    "connectors": "DATA_FLOW",
    "backup_catalog": "BACKUP_ARCHIVE",
    "restore_entries": "BACKUP_ARCHIVE",
    "security_audit_events": "SECURITY_AUDIT_LOG",
    "audit_records": "SECURITY_AUDIT_LOG",
    "operational_record_history": "HISTORY",
}

RESULT_STATE = {
    "absent": "ABSENT",
    "not_found": "ABSENT",
    "deleted": "ABSENT",
    "missing": "ABSENT",
    "found": "PRESENT",
    "present": "PRESENT",
    "identifiable": "PRESENT",
    "active": "ACTIVE",
    "enabled": "ACTIVE",
    "disabled": "DISABLED",
    "stopped": "DISABLED",
    "blocked": "BLOCKED",
    "redacted": "REDACTED",
    "anonymized": "ANONYMIZED",
    "anonymous": "ANONYMIZED",
    "retained": "PRESENT",
}


def canonical_resource(value: str) -> str:
    match = RESOURCE_RE.search(value or "")
    if not match:
        return normalize(value).replace(" ", "_")
    return f"{match.group(1).lower()}_{match.group(2)}"


def meta_for_resource(resource_id: str) -> tuple[str, str]:
    for prefix in sorted(PREFIX_META, key=len, reverse=True):
        if resource_id.startswith(prefix + "_"):
            return PREFIX_META[prefix]
    return "unknown_system", "UNKNOWN"


def _flatten(node: Any, prefix: str = "") -> Iterator[tuple[str, Any]]:
    if isinstance(node, dict):
        for key, value in node.items():
            yield from _flatten(value, f"{prefix}.{key}" if prefix else key)
    elif isinstance(node, list):
        for value in node:
            yield from _flatten(value, prefix)
    else:
        yield prefix, node


def _subjects(record: dict[str, Any]) -> set[str]:
    subjects: set[str] = set()
    hints = ("email", "phone", "hash", "subject", "reference", "lookup", "selector", "user_id", "owner", "name", "correlation", "match_key")
    for key, value in _flatten(record):
        leaf = key.rsplit(".", 1)[-1].casefold()
        if leaf == "resource_id" or not any(hint in leaf for hint in hints):
            continue
        if isinstance(value, (str, int)) and normalize(value):
            text = normalize(value)
            subjects.add(text)
            for email in EMAIL_RE.findall(text):
                subjects.add(normalize(email))
            for user in USER_RE.findall(text):
                subjects.add(f"user_{user}")
            for digest in HASH_RE.findall(text):
                subjects.add(digest.casefold())
    return subjects


def _has_pii(record: dict[str, Any]) -> bool:
    for key, value in _flatten(record):
        leaf = key.rsplit(".", 1)[-1].casefold()
        if value in (None, "", [], {}):
            continue
        if any(token in leaf for token in ("email", "phone", "full_name", "requester_name", "email_hash", "display_name")):
            return True
    return False


def _record_time(record: dict[str, Any], fallback):
    for key in (
        "observed_at",
        "recorded_at",
        "captured_at",
        "updated_at",
        "created_at",
        "event_time",
        "exported_at",
    ):
        if record.get(key):
            try:
                return parse_time(str(record[key]))
            except ValueError:
                pass
    return fallback


def _infer_state(category: str, record: dict[str, Any], text: str = "") -> str:
    result = normalize(record.get("result") or record.get("state") or record.get("status"))
    if result in RESULT_STATE:
        return RESULT_STATE[result]
    status_code = record.get("status_code")
    if status_code == 404 or (category == "USER_PROFILE" and "profile" in record and record.get("profile") is None):
        return "ABSENT"
    if status_code == 200:
        return "PRESENT"
    if category == "DATA_FLOW":
        enabled = record.get("enabled")
        if enabled is not None:
            return "ACTIVE" if bool(enabled) else "DISABLED"
    if category == "BACKUP_ARCHIVE":
        allowed = record.get("restore_allowed")
        if allowed is not None:
            return "ACTIVE" if bool(allowed) else "BLOCKED"
    if category == "SEARCH_INDEX_ENTRY":
        if "hits" in record:
            return "PRESENT" if record.get("hits") else "ABSENT"
        if normalize(record.get("result")) in {"hit", "found"}:
            return "PRESENT"
    if category == "ANALYTICS_PROFILE":
        return "PRESENT" if _has_pii(record) else "ANONYMIZED"
    if category in {"SUPPORT_MESSAGE", "ACCOUNT_TOMBSTONE", "EMAIL_DELIVERY_LOG"}:
        text_has_pii = bool(EMAIL_RE.search(text) or re.search(r"\+?\d[\d\s()\-]{7,}", text))
        return "PRESENT" if _has_pii(record) or text_has_pii else "REDACTED"
    folded = normalize(text)
    if any(marker in folded for marker in ("rows=0", "http_status=404", "result=miss", "not found")):
        return "ABSENT"
    if any(marker in folded for marker in ("enabled=true", "scheduler_registered=true")):
        return "ACTIVE"
    if any(marker in folded for marker in ("enabled=false", "last_delivery_status=stopped")):
        return "DISABLED"
    if any(marker in folded for marker in ("restore_allowed=false", "deletion_block=true")):
        return "BLOCKED"
    if any(marker in folded for marker in ("restore_allowed=true", "deletion_block=false", "eligible=true")):
        return "ACTIVE"
    return "PRESENT"


class EvidenceIndex:
    def __init__(self, dataset_dir: Path, cache_dir: Path | None = None):
        self.dataset_dir = dataset_dir
        self.cache_dir = cache_dir
        self.files = self._load_manifest()
        self.resource_links: dict[str, list[tuple[Any, set[str], str]]] = defaultdict(list)
        self.observations: list[Observation] = []
        self.extraction_warnings: list[str] = []
        self._load_activity_links()
        self._extract_all()

    def _load_manifest(self) -> list[EvidenceFile]:
        result: list[EvidenceFile] = []
        with (self.dataset_dir / "evidence_files.csv").open(encoding="utf-8", newline="") as handle:
            for row in csv.DictReader(handle):
                result.append(
                    EvidenceFile(
                        file_id=row["file_id"],
                        path=row["file_path"],
                        origin=row["origin"],
                        captured_at=parse_time(row["captured_at"]),
                    )
                )
        return result

    def _load_activity_links(self) -> None:
        path = self.dataset_dir / "system_activity.csv"
        with path.open(encoding="utf-8", newline="") as handle:
            for row in csv.DictReader(handle):
                resource = canonical_resource(row.get("resource_id", ""))
                if not resource:
                    continue
                details = row.get("details", "")
                subjects = {normalize(v) for v in EMAIL_RE.findall(details)}
                subjects.update(f"user_{v}" for v in USER_RE.findall(details))
                if subjects:
                    self.resource_links[resource].append(
                        (parse_time(row.get("event_time")), subjects, row.get("activity_id", ""))
                    )

    def _extract_all(self) -> None:
        for evidence in self.files:
            path = self.dataset_dir / evidence.path
            try:
                suffix = path.suffix.casefold()
                if suffix == ".json":
                    self.observations.extend(self._from_json(path, evidence))
                elif suffix == ".sql":
                    self.observations.extend(self._from_sql(path, evidence))
                else:
                    text = self._text_for(path, evidence)
                    self.observations.extend(
                        self._from_text(text, evidence, allow_freeform=suffix != ".log")
                    )
            except Exception as exc:  # keep a full run alive and surface the gap
                self.extraction_warnings.append(f"{evidence.file_id}: {type(exc).__name__}: {exc}")

    def _from_json(self, path: Path, evidence: EvidenceFile) -> list[Observation]:
        data = json.loads(path.read_text(encoding="utf-8"))
        result: list[Observation] = []

        def walk(node: Any, system: str = "", collection: str = "") -> None:
            if isinstance(node, list):
                for item in node:
                    walk(item, system, collection)
                return
            if not isinstance(node, dict):
                return
            local_system = normalize(node.get("data_store") or node.get("system") or system).replace(" ", "_")
            local_collection = normalize(node.get("collection") or collection).replace(" ", "_")
            resource_value = node.get("resource_id")
            if resource_value:
                resource = canonical_resource(str(resource_value))
                inferred_system, inferred_category = meta_for_resource(resource)
                category = normalize(node.get("record_type")).upper() or COLLECTION_CATEGORY.get(local_collection, inferred_category)
                system_id = local_system if local_system.endswith(("_service", "_platform", "_storage")) else inferred_system
                result.append(
                    Observation(
                        system_id=system_id,
                        resource_id=resource,
                        category=category,
                        state=_infer_state(category, node),
                        observed_at=_record_time(node, evidence.captured_at),
                        evidence_id=evidence.file_id,
                        subjects=_subjects(node),
                        attributes=node,
                    )
                )
            for key, value in node.items():
                child_system = local_system
                child_collection = local_collection
                normalized_key = normalize(key).replace(" ", "_")
                if normalized_key in {
                    "account_service", "analytics_platform", "backup_service", "export_service",
                    "identity_service", "integration_service", "marketing_platform", "search_service",
                    "security_service", "support_platform", "support_storage_service",
                }:
                    child_system = normalized_key
                if normalized_key in COLLECTION_CATEGORY:
                    child_collection = normalized_key
                walk(value, child_system, child_collection)

        walk(data)
        return result

    def _from_sql(self, path: Path, evidence: EvidenceFile) -> list[Observation]:
        connection = sqlite3.connect(":memory:")
        connection.row_factory = sqlite3.Row
        try:
            connection.executescript(path.read_text(encoding="utf-8"))
            result: list[Observation] = []
            scope = evidence.captured_at
            try:
                times = [parse_time(row[0]) for row in connection.execute("SELECT captured_at FROM snapshot_scope")]
                if times:
                    scope = max(times)
            except sqlite3.Error:
                pass
            tables = [row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")]
            for table in tables:
                columns = [row[1] for row in connection.execute(f'PRAGMA table_info("{table}")')]
                if "resource_id" not in columns:
                    continue
                for row in connection.execute(f'SELECT * FROM "{table}"'):
                    record = dict(row)
                    payload = record.get("payload") or record.get("record_json")
                    if isinstance(payload, str) and payload.lstrip().startswith("{"):
                        try:
                            record = {**record, **json.loads(payload)}
                        except json.JSONDecodeError:
                            pass
                    resource = canonical_resource(str(record.get("resource_id", "")))
                    inferred_system, inferred_category = meta_for_resource(resource)
                    system_id, category = TABLE_META.get(table, (inferred_system, inferred_category))
                    explicit_system = normalize(record.get("system_id") or record.get("system") or record.get("origin_system")).replace(" ", "_")
                    explicit_collection = normalize(record.get("collection") or record.get("collection_name")).replace(" ", "_")
                    if explicit_system:
                        system_id = explicit_system
                    if explicit_collection:
                        category = COLLECTION_CATEGORY.get(explicit_collection, category)
                    result.append(
                        Observation(
                            system_id=system_id,
                            resource_id=resource,
                            category=category,
                            state=_infer_state(category, record),
                            observed_at=_record_time(record, scope),
                            evidence_id=evidence.file_id,
                            subjects=_subjects(record),
                            attributes=record,
                        )
                    )
            return result
        finally:
            connection.close()

    def _text_for(self, path: Path, evidence: EvidenceFile) -> str:
        if path.suffix.casefold() in {".log", ".txt"}:
            return path.read_text(encoding="utf-8", errors="replace")
        cache_path = self.cache_dir / f"{evidence.file_id}.txt" if self.cache_dir else None
        if cache_path and cache_path.exists():
            return cache_path.read_text(encoding="utf-8")
        if path.suffix.casefold() == ".pdf" and shutil.which("pdftotext"):
            command = ["pdftotext", "-layout", str(path), "-"]
        elif path.suffix.casefold() in {".png", ".jpg", ".jpeg"} and shutil.which("tesseract"):
            command = ["tesseract", str(path), "stdout"]
        else:
            self.extraction_warnings.append(f"{evidence.file_id}: no extractor for {path.suffix}")
            return ""
        completed = subprocess.run(command, check=True, capture_output=True, text=True)
        text = completed.stdout
        if cache_path:
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(text, encoding="utf-8")
        return text

    def _from_text(
        self,
        text: str,
        evidence: EvidenceFile,
        *,
        allow_freeform: bool = True,
    ) -> list[Observation]:
        if not text.strip():
            return []
        groups: dict[str, list[str]] = defaultdict(list)
        free_lines: list[str] = []
        result: list[Observation] = []
        for line in text.splitlines():
            parts = [part.strip().lstrip("- ") for part in line.split("|")]
            if len(parts) >= 6 and RESOURCE_RE.search(parts[2]):
                resource = canonical_resource(parts[2])
                inferred_system, inferred_category = meta_for_resource(resource)
                record = {
                    "recorded_at": parts[0],
                    "system": parts[1],
                    "resource_id": resource,
                    "record_type": parts[3],
                    "result": parts[4],
                    "lookup_value": parts[5],
                }
                result.append(
                    Observation(
                        system_id=normalize(parts[1]).replace(" ", "_") or inferred_system,
                        resource_id=resource,
                        category=normalize(parts[3]).upper() or inferred_category,
                        state=_infer_state(normalize(parts[3]).upper() or inferred_category, record),
                        observed_at=_record_time(record, evidence.captured_at),
                        evidence_id=evidence.file_id,
                        subjects=_subjects(record),
                        attributes=record,
                    )
                )
                continue
            match = re.search(r"\btrace_id=([^\s]+)", line)
            if match:
                groups[match.group(1)].append(line)
            elif "resource_id=" in line:
                record = {key: value.strip("\"'") for key, value in KV_RE.findall(line)}
                resource = canonical_resource(record.get("resource_id", ""))
                if resource:
                    system_id, category = meta_for_resource(resource)
                    subjects = _subjects(record)
                    subjects.update(normalize(v) for v in EMAIL_RE.findall(line))
                    subjects.update(f"user_{v}" for v in USER_RE.findall(line))
                    result.append(
                        Observation(
                            system_id=system_id,
                            resource_id=resource,
                            category=category,
                            state=_infer_state(category, record, line),
                            observed_at=_record_time(record, evidence.captured_at),
                            evidence_id=evidence.file_id,
                            subjects=subjects,
                            attributes=record,
                        )
                    )
            else:
                free_lines.append(line)
        for lines in groups.values():
            merged = " ".join(lines)
            record = {key: value.strip("\"'") for key, value in KV_RE.findall(merged)}
            explicit_resource = record.get("resource_id", "")
            resources = {canonical_resource(explicit_resource)} if explicit_resource else set()
            if not resources:
                continue
            subjects = _subjects(record)
            subjects.update(normalize(v) for v in EMAIL_RE.findall(merged))
            subjects.update(f"user_{v}" for v in USER_RE.findall(merged))
            for resource in resources:
                system_id, category = meta_for_resource(resource)
                result.append(
                    Observation(
                        system_id=system_id,
                        resource_id=resource,
                        category=category,
                        state=_infer_state(category, record, merged),
                        observed_at=_record_time(record, evidence.captured_at),
                        evidence_id=evidence.file_id,
                        subjects=set(subjects),
                        attributes=record,
                    )
                )
        if not allow_freeform:
            return result

        free_text = "\n".join(free_lines)
        for match in RESOURCE_RE.finditer(free_text):
            resource = canonical_resource(match.group(0))
            system_id, category = meta_for_resource(resource)
            window = free_text[max(0, match.start() - 800): match.end() + 1200]
            subjects = {normalize(v) for v in EMAIL_RE.findall(window)}
            subjects.update(f"user_{v}" for v in USER_RE.findall(window))
            subjects.update(v.casefold() for v in HASH_RE.findall(window))
            result.append(
                Observation(
                    system_id=system_id,
                    resource_id=resource,
                    category=category,
                    state=_infer_state(category, {}, window),
                    observed_at=evidence.captured_at,
                    evidence_id=evidence.file_id,
                    subjects=set(subjects),
                    attributes={"extract": window.strip()},
                    confidence=0.85,
                )
            )
        return result

    def eligible(self, audit_at) -> list[Observation]:
        captured = {item.file_id: item.captured_at for item in self.files}
        return [
            observation
            for observation in self.observations
            if captured.get(observation.evidence_id, observation.observed_at) <= audit_at
            and observation.observed_at <= audit_at
        ]

    def matches(self, observation: Observation, aliases: set[str], audit_at) -> bool:
        """Resolve subject ownership without allowing post-cutoff activity links."""
        if observation.matches(aliases):
            return True
        return any(
            linked_at <= audit_at and bool(subjects & aliases)
            for linked_at, subjects, _activity_id in self.resource_links.get(
                observation.resource_id, []
            )
        )

