from __future__ import annotations

import json
import mimetypes
import re
from dataclasses import asdict
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .engine import GhostTraceEngine


UI_DIR = Path(__file__).resolve().parents[1] / "ui"


def serve(engine: GhostTraceEngine, host: str = "127.0.0.1", port: int = 8787) -> None:
    decisions = {decision.case_id: decision for decision in engine.audit_all()}

    class Handler(BaseHTTPRequestHandler):
        server_version = "GhostTrace/1.0"

        def log_message(self, fmt: str, *args) -> None:
            # Avoid writing case identifiers or PII to access logs by default.
            return

        def do_GET(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            if parsed.path == "/api/health":
                self._json({"status": "ok", **engine.diagnostics()})
                return
            if parsed.path == "/api/summary":
                counts = {status: 0 for status in ("COMPLETE", "INCOMPLETE", "NOT_APPLICABLE")}
                for decision in decisions.values():
                    counts[decision.deletion_status] += 1
                self._json(
                    {
                        "total": len(decisions),
                        "counts": counts,
                        "evidenceFiles": len(engine.evidence.files),
                        "observations": len(engine.evidence.observations),
                        "warnings": sum(bool(item.warnings) for item in decisions.values()),
                    }
                )
                return
            if parsed.path == "/api/cases":
                query = parse_qs(parsed.query)
                needle = query.get("q", [""])[0].casefold().strip()
                status = query.get("status", [""])[0].upper()
                payload = []
                for case in engine.cases:
                    decision = decisions[case.case_id]
                    if status and decision.deletion_status != status:
                        continue
                    searchable = f"{case.case_id} {case.email} {decision.user_id}".casefold()
                    if needle and needle not in searchable:
                        continue
                    payload.append(
                        {
                            "caseId": case.case_id,
                            "email": case.email,
                            "auditAt": case.audit_at.isoformat(),
                            "status": decision.deletion_status,
                            "confidence": decision.confidence,
                            "stepCount": len(decision.steps),
                            "warningCount": len(decision.warnings),
                        }
                    )
                self._json(payload)
                return
            match = re.fullmatch(r"/api/cases/(case_\d+)", parsed.path)
            if match:
                case_id = match.group(1)
                case = next((item for item in engine.cases if item.case_id == case_id), None)
                decision = decisions.get(case_id)
                if case is None or decision is None:
                    self._json({"error": "Case not found"}, HTTPStatus.NOT_FOUND)
                    return
                identity = engine.identities.resolve(case)
                aliases = engine.identities.aliases_at(identity, case) if identity else set()
                observations = [
                    item
                    for item in engine.evidence.eligible(case.audit_at)
                    if engine.evidence.matches(item, aliases, case.audit_at)
                ]
                observations.sort(key=lambda item: (item.observed_at, item.system_id, item.resource_id))
                self._json(
                    {
                        "case": {
                            "caseId": case.case_id,
                            "email": case.email,
                            "requestedAt": case.requested_at.isoformat(),
                            "reportedStatus": case.reported_status,
                            "reportedAt": case.reported_at.isoformat(),
                            "auditAt": case.audit_at.isoformat(),
                            "requestText": case.request_text,
                        },
                        "decision": {
                            "status": decision.deletion_status,
                            "confidence": decision.confidence,
                            "userId": decision.user_id,
                            "steps": [step.to_dict() for step in decision.steps],
                            "warnings": decision.warnings,
                            "evidenceIds": decision.evidence_ids,
                        },
                        "identity": {
                            "userId": identity.user_id if identity else "",
                            "aliasCount": len(aliases),
                            "aliases": sorted(aliases),
                        },
                        "observations": [
                            {
                                "systemId": item.system_id,
                                "resourceId": item.resource_id,
                                "category": item.category,
                                "state": item.state,
                                "observedAt": item.observed_at.isoformat(),
                                "evidenceId": item.evidence_id,
                                "confidence": item.confidence,
                            }
                            for item in observations
                        ],
                    }
                )
                return
            self._static(parsed.path)

        def _json(self, payload, status: HTTPStatus = HTTPStatus.OK) -> None:
            data = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=str).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.end_headers()
            self.wfile.write(data)

        def _static(self, request_path: str) -> None:
            relative = "index.html" if request_path in {"", "/"} else request_path.lstrip("/")
            target = (UI_DIR / relative).resolve()
            if UI_DIR.resolve() not in target.parents and target != UI_DIR.resolve():
                self.send_error(HTTPStatus.FORBIDDEN)
                return
            if not target.is_file():
                target = UI_DIR / "index.html"
            data = target.read_bytes()
            mime = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", f"{mime}; charset=utf-8" if mime.startswith("text/") else mime)
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(data)

    httpd = ThreadingHTTPServer((host, port), Handler)
    print(f"GhostTrace workstation: http://{host}:{port}")
    print("Press Ctrl+C to stop.")
    httpd.serve_forever()

