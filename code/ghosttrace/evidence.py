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

from .domain import Case, EvidenceFile, Observation, normalize, parse_time


RESOURCE_RE = re.compile(
    r"\b(account|analytics|audit_record|backup|data_flow|export|marketing|object|search|support|tombstone)[_\s-]?(\d+)\b",
    re.I,
)
EMAIL_RE = re.compile(r"[A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,}", re.I)
USER_RE = re.compile(r"\buser[_\s-]?(\d+)\b", re.I)
HASH_RE = re.compile(r"\b[a-f0-9]{64}\b", re.I)
FILE_REF_RE = re.compile(
    r"\b[A-Za-z0-9][A-Za-z0-9_.-]*\.(?:csv|jpeg|jpg|json|pdf|png|zip)\b",
    re.I,
)
KV_RE = re.compile(r"([A-Za-z_][\w.-]*)=(\"[^\"]*\"|'[^']*'|\S+)")
ISO_PREFIX_RE = re.compile(r"^\s*(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z)\b")
MESSAGE_RE = re.compile(r'\bmessage="((?:\\.|[^"\\])*)"')
TRACE_RE = re.compile(r"\b(?:tr|rb)-\d{5,}\b", re.I)

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


def meta_for_text_resource(
    resource_id: str,
    evidence: EvidenceFile,
    record: dict[str, Any],
    context: str,
) -> tuple[str, str]:
    """Refine prefix metadata using source semantics, never case labels.

    A shared resource prefix is not always a policy category. Support exports
    can describe an exclusive attachment or a legacy contact index rather than
    a retained conversation, and analytics infrastructure can host a marketing
    contact record. The evidence source and operation make that distinction.
    """
    system_id, category = meta_for_resource(resource_id)
    hint = normalize(
        " ".join(
            (
                evidence.path,
                evidence.origin,
                str(record.get("event", "")),
                str(record.get("record_type", "")),
                context,
            )
        )
    )
    if resource_id.startswith("support_"):
        if any(token in hint for token in ("attachment", "uploaded file", "checksum prefix")):
            category = "SUPPORT_ATTACHMENT"
        elif any(
            token in hint
            for token in ("contact_query", "legacy_contact_index", "deletion_marker_check")
        ):
            category = "SECONDARY_IDENTIFIER"
    elif resource_id.startswith("analytics_") and any(
        token in hint for token in ("contact_lookup", "email_opt_in")
    ):
        category = "MARKETING_PROFILE"
    return system_id, category


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
        if value in (None, "", [], {}) or normalize(value) in {
            "null",
            "none",
            "redacted",
            "[redacted]",
        }:
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


def _as_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    folded = normalize(value)
    if folded in {"true", "1", "yes", "enabled", "allow", "allowed"}:
        return True
    if folded in {"false", "0", "no", "disabled", "deny", "denied"}:
        return False
    return None


def _structured_line_record(line: str, line_no: int) -> dict[str, Any]:
    """Parse the structured log dialects used by participant evidence."""
    record: dict[str, Any] = {}
    leading_time = ISO_PREFIX_RE.search(line)
    remainder = line[leading_time.end():].strip() if leading_time else line.strip()
    if remainder.startswith("{"):
        try:
            payload = json.loads(remainder)
            if isinstance(payload, dict):
                record.update(payload)
        except json.JSONDecodeError:
            pass
    if not record:
        record.update(
            (key, value.strip("\"'")) for key, value in KV_RE.findall(line)
        )
        embedded = MESSAGE_RE.search(line)
        if embedded:
            message = embedded.group(1).replace('\\"', '"').replace("\\\\", "\\")
            record.update(
                (key, value.strip("\"'"))
                for key, value in KV_RE.findall(message)
            )
    parts = [part.strip() for part in line.split("|")]
    if len(parts) >= 6:
        record.setdefault("service", parts[2])
        record.setdefault("trace_id", parts[3])
        record.setdefault("event", parts[4])
    record["_line_no"] = line_no
    if leading_time:
        record["observed_at"] = leading_time.group(1)
    return record


def _trace_key(line: str, record: dict[str, Any]) -> str:
    for key in ("trace_id", "requestId", "request_id"):
        value = str(record.get(key, "")).strip()
        if value and value.casefold().startswith(("tr-", "rb-")):
            return value
    match = TRACE_RE.search(line)
    return match.group(0) if match else ""


def _infer_state(category: str, record: dict[str, Any], text: str = "") -> str:
    result = normalize(record.get("result") or record.get("state") or record.get("status"))
    if result in RESULT_STATE:
        return RESULT_STATE[result]
    status_code = str(record.get("status_code") or record.get("http_status") or "")
    rows = str(record.get("rows", ""))
    if status_code == "404" or (
        category == "USER_PROFILE"
        and (rows == "0" or ("profile" in record and record.get("profile") is None))
    ):
        return "ABSENT"
    if status_code == "200" or (category == "USER_PROFILE" and rows not in {"", "0"}):
        return "PRESENT"
    if category == "DATA_FLOW":
        enabled = _as_bool(record.get("enabled"))
        if enabled is not None:
            return "ACTIVE" if enabled else "DISABLED"
    if category == "BACKUP_ARCHIVE":
        decision = normalize(record.get("decision"))
        if decision in {"deny", "denied", "block", "blocked"}:
            return "BLOCKED"
        deletion_block = _as_bool(record.get("deletion_block"))
        if deletion_block is not None:
            return "BLOCKED" if deletion_block else "ACTIVE"
        allowed = _as_bool(record.get("restore_allowed"))
        if allowed is not None:
            return "ACTIVE" if allowed else "BLOCKED"
        eligible = _as_bool(record.get("eligible"))
        if eligible is not None:
            return "ACTIVE" if eligible else "BLOCKED"
    if category == "SEARCH_INDEX_ENTRY":
        if "hits" in record:
            return "PRESENT" if record.get("hits") else "ABSENT"
        if normalize(record.get("result")) in {"hit", "found"}:
            return "PRESENT"
    if category == "ANALYTICS_PROFILE":
        direct_identifiers = record.get("direct_identifiers")
        if direct_identifiers is not None:
            try:
                return "PRESENT" if int(direct_identifiers) > 0 else "ANONYMIZED"
            except (TypeError, ValueError):
                pass
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
        self.resource_aliases: dict[str, list[tuple[str, Any]]] = defaultdict(list)
        self.activity_events: list[dict[str, Any]] = []
        self.observations: list[Observation] = []
        self.observations_by_resource: dict[str, list[Observation]] = defaultdict(list)
        self.extraction_warnings: list[str] = []
        self._captured_at = {item.file_id: item.captured_at for item in self.files}
        self._load_activity_links()
        self._extract_all()
        for observation in self.observations:
            self.observations_by_resource[observation.resource_id].append(observation)

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
                event = dict(row)
                event["parsed_time"] = parse_time(row.get("event_time"))
                self.activity_events.append(event)
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
                for reference in FILE_REF_RE.findall(details):
                    self.resource_aliases[normalize(reference)].append(
                        (resource, parse_time(row.get("event_time")))
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
        groups: dict[str, list[tuple[int, str, dict[str, Any]]]] = defaultdict(list)
        free_lines: list[str] = []
        result: list[Observation] = []

        def append_observation(
            record: dict[str, Any],
            context: str,
            *,
            resource_id: str | None = None,
        ) -> None:
            resource = canonical_resource(resource_id or record.get("resource_id", ""))
            if not resource or not RESOURCE_RE.search(resource):
                return
            system_id, category = meta_for_text_resource(
                resource, evidence, record, context
            )
            subjects = _subjects(record)
            subjects.update(normalize(value) for value in EMAIL_RE.findall(context))
            subjects.update(f"user_{value}" for value in USER_RE.findall(context))
            result.append(
                Observation(
                    system_id=system_id,
                    resource_id=resource,
                    category=category,
                    state=_infer_state(category, record, context),
                    observed_at=_record_time(record, evidence.captured_at),
                    evidence_id=evidence.file_id,
                    subjects=subjects,
                    attributes=dict(record),
                )
            )

        for line_no, line in enumerate(text.splitlines(), 1):
            parts = [part.strip().lstrip("- ") for part in line.split("|")]
            if len(parts) >= 6 and RESOURCE_RE.search(parts[2]):
                resource = canonical_resource(parts[2])
                record = {
                    "recorded_at": parts[0],
                    "system": parts[1],
                    "resource_id": resource,
                    "record_type": parts[3],
                    "result": parts[4],
                    "lookup_value": parts[5],
                    "_line_no": line_no,
                }
                inferred_system, inferred_category = meta_for_text_resource(
                    resource, evidence, record, line
                )
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
            record = _structured_line_record(line, line_no)
            trace = _trace_key(line, record)
            if trace:
                groups[trace].append((line_no, line, record))
            elif record.get("resource_id"):
                append_observation(record, line)
            else:
                free_lines.append(line)
        for entries in groups.values():
            lines = [line for _line_no, line, _record in entries]
            merged = " ".join(lines)
            record: dict[str, Any] = {}
            resources: dict[str, Any] = {}
            for line_no, _line, line_record in entries:
                record.update(line_record)
                explicit_resource = canonical_resource(
                    str(line_record.get("resource_id", ""))
                )
                if explicit_resource and RESOURCE_RE.search(explicit_resource):
                    resources[explicit_resource] = _record_time(
                        line_record, evidence.captured_at
                    )
            if not resources:
                continue
            record["_line_no"] = min(line_no for line_no, _line, _record in entries)
            subjects = _subjects(record)
            subjects.update(normalize(v) for v in EMAIL_RE.findall(merged))
            subjects.update(f"user_{v}" for v in USER_RE.findall(merged))
            for resource, observed_at in resources.items():
                resource_record = dict(record)
                resource_record["resource_id"] = resource
                resource_record["observed_at"] = observed_at.isoformat()
                before = len(result)
                append_observation(resource_record, merged, resource_id=resource)
                if len(result) > before:
                    result[-1].subjects = set(subjects)

        folded_text = normalize(text)
        for reference, links in self.resource_aliases.items():
            if reference not in folded_text:
                continue
            for resource, linked_at in links:
                alias_record = {
                    "resource_id": resource,
                    "file_reference": reference,
                    "inference": "catalog_file_alias",
                    "observed_at": max(evidence.captured_at, linked_at).isoformat(),
                }
                append_observation(alias_record, reference, resource_id=resource)
        if not allow_freeform:
            return result

        free_text = "\n".join(free_lines)
        for match in RESOURCE_RE.finditer(free_text):
            resource = canonical_resource(match.group(0))
            window = free_text[max(0, match.start() - 800): match.end() + 1200]
            system_id, category = meta_for_text_resource(
                resource, evidence, {}, window
            )
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
        return [
            observation
            for observation in self.observations
            if self._captured_at.get(
                observation.evidence_id, observation.observed_at
            ) <= audit_at
            and observation.observed_at <= audit_at
        ]

    def matches(self, observation: Observation, aliases: set[str], audit_at) -> bool:
        """Resolve subject ownership without allowing post-cutoff activity links."""
        relation = self.ownership_relation(observation.resource_id, aliases, audit_at)
        if relation != "UNKNOWN":
            return relation == "MATCH"
        return observation.matches(aliases)

    def ownership_relation(
        self,
        resource_id: str,
        aliases: set[str],
        audit_at,
    ) -> str:
        """Return MATCH, CONFLICT, or UNKNOWN at a decision cutoff.

        Explicit activity-catalog links have precedence over extracted text.
        Subject-bearing observations are used only when no authoritative link
        exists. Anonymous evidence therefore remains UNKNOWN rather than being
        silently assigned.
        """
        eligible_links = [
            subjects
            for linked_at, subjects, _activity_id in self.resource_links.get(
                resource_id, []
            )
            if linked_at <= audit_at
        ]
        if eligible_links:
            return "MATCH" if any(subjects & aliases for subjects in eligible_links) else "CONFLICT"
        subject_observations = [
            item
            for item in self.observations_by_resource.get(resource_id, [])
            if item.subjects
            and self._captured_at.get(item.evidence_id, item.observed_at) <= audit_at
            and item.observed_at <= audit_at
        ]
        if not subject_observations:
            return "UNKNOWN"
        return (
            "MATCH"
            if any(item.subjects & aliases for item in subject_observations)
            else "CONFLICT"
        )

    def unambiguous_account_resource(
        self,
        case: Case,
        aliases: set[str],
    ) -> str:
        """Return a uniquely attributable workflow account near its report.

        Activity results remain claims; this method only discovers the target
        that a future verification must cover. A narrow window and uniqueness
        requirement prevent nearby concurrent workflows from being conflated.
        """
        before_seconds = 75 * 60
        after_seconds = 30 * 60
        candidates: dict[str, str] = {}
        eligible_observations = self.eligible(case.audit_at)
        for event in self.activity_events:
            event_at = event["parsed_time"]
            offset = (event_at - case.reported_at).total_seconds()
            if not (-before_seconds <= offset <= after_seconds):
                continue
            if event_at > case.audit_at:
                continue
            if event.get("event_type") != "DELETE_RECORD":
                continue
            if normalize(event.get("target_system")) != "account_service":
                continue
            resource = canonical_resource(event.get("resource_id", ""))
            if not resource.startswith("account_"):
                continue
            links = [
                subjects
                for linked_at, subjects, _activity_id in self.resource_links.get(resource, [])
                if linked_at <= case.audit_at
            ]
            if links and not any(subjects & aliases for subjects in links):
                continue
            subject_bearing_observations = [
                item
                for item in eligible_observations
                if item.resource_id == resource and item.subjects
            ]
            if subject_bearing_observations and not any(
                item.subjects & aliases for item in subject_bearing_observations
            ):
                continue
            candidates[resource] = normalize(event.get("reported_result")).upper()
        if len(candidates) == 1:
            return next(iter(candidates))
        preferred_result = {
            "COMPLETE": "SUCCESS",
            "PARTIALLY_COMPLETE": "PARTIAL",
            "NOT_STARTED": "FAILED",
        }.get(case.reported_status)
        preferred = [
            resource
            for resource, result in candidates.items()
            if result == preferred_result
        ]
        return preferred[0] if len(preferred) == 1 else ""

    def prove_account_absence(self, resource_id: str, case: Case) -> Observation | None:
        """Use the latest eligible post-workflow account snapshot as negative proof."""
        snapshots: list[EvidenceFile] = []
        for evidence in self.files:
            path = self.dataset_dir / evidence.path
            if path.suffix.casefold() != ".sql":
                continue
            if not (case.reported_at <= evidence.captured_at <= case.audit_at):
                continue
            try:
                header = path.read_text(encoding="utf-8", errors="replace")[:6000]
            except OSError:
                continue
            if re.search(r"CREATE TABLE\s+customer_profiles", header, re.I):
                snapshots.append(evidence)
        if not snapshots:
            return None
        snapshot = max(snapshots, key=lambda item: item.captured_at)
        if any(
            item.evidence_id == snapshot.file_id and item.resource_id == resource_id
            for item in self.observations
        ):
            return None
        return Observation(
            system_id="account_service",
            resource_id=resource_id,
            category="USER_PROFILE",
            state="ABSENT",
            observed_at=snapshot.captured_at,
            evidence_id=snapshot.file_id,
            subjects=set(),
            attributes={"inference": "absent_from_complete_account_snapshot"},
            confidence=0.95,
        )

    def verified_account_readback(
        self,
        resource_id: str,
        case: Case,
        aliases: set[str],
    ) -> Observation | None:
        """Return independent absence proof for an attributed workflow target."""
        readbacks = [
            item
            for item in self.eligible(case.audit_at)
            if item.resource_id == resource_id
            and item.category == "USER_PROFILE"
            and item.state == "ABSENT"
        ]
        if not readbacks:
            return None
        linked_subjects = [
            subjects
            for linked_at, subjects, _activity_id in self.resource_links.get(resource_id, [])
            if linked_at <= case.audit_at
        ]
        if linked_subjects and not any(subjects & aliases for subjects in linked_subjects):
            return None
        subject_bearing = [item for item in readbacks if item.subjects]
        if subject_bearing and not any(item.subjects & aliases for item in subject_bearing):
            return None
        return max(
            readbacks,
            key=lambda item: (item.observed_at, item.confidence, item.evidence_id),
        )

    def verified_unique_anonymization(
        self,
        case: Case,
        aliases: set[str],
    ) -> Observation | None:
        """Join one unambiguous workflow target to independent readback proof.

        The activity identifies which resource the workflow targeted; it does
        not establish success. Success is accepted only when a separate,
        cutoff-eligible evidence observation proves that exact resource is no
        longer attributable. Concurrent targets make the join ambiguous and
        are therefore rejected.
        """
        if case.reported_status != "COMPLETE":
            return None
        before_seconds = 75 * 60
        after_seconds = 30 * 60
        candidates: set[str] = set()
        for event in self.activity_events:
            event_at = event["parsed_time"]
            offset = (event_at - case.reported_at).total_seconds()
            if not (-before_seconds <= offset <= after_seconds):
                continue
            if event_at > case.audit_at:
                continue
            if event.get("event_type") != "ANONYMIZE_RECORD":
                continue
            if normalize(event.get("target_system")) != "analytics_platform":
                continue
            resource = canonical_resource(event.get("resource_id", ""))
            if resource.startswith("analytics_") and self.ownership_relation(
                resource, aliases, case.audit_at
            ) != "CONFLICT":
                candidates.add(resource)
        if len(candidates) != 1:
            return None
        resource = next(iter(candidates))
        readbacks = [
            item
            for item in self.eligible(case.audit_at)
            if item.resource_id == resource
            and item.category == "ANALYTICS_PROFILE"
            and item.state == "ANONYMIZED"
        ]
        if not readbacks:
            return None
        linked_subjects = [
            subjects
            for linked_at, subjects, _activity_id in self.resource_links.get(resource, [])
            if linked_at <= case.audit_at
        ]
        if linked_subjects and not any(subjects & aliases for subjects in linked_subjects):
            return None
        subject_bearing = [item for item in readbacks if item.subjects]
        if subject_bearing and not any(item.subjects & aliases for item in subject_bearing):
            return None
        return max(
            readbacks,
            key=lambda item: (item.observed_at, item.confidence, item.evidence_id),
        )

