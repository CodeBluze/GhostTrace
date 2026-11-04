# GhostTrace

GhostTrace is an evidence-grounded deletion-audit system for the HackerRank Orchestrate challenge **Ghost Data Still Exists**. It reconstructs historical identities, reads heterogeneous evidence, enforces the case-specific time cutoff, applies deletion policy, and emits a validated remediation plan for every request.

The submission is a complete local application: a deterministic audit engine, multi-format evidence pipeline, strict output validator, evaluation harness, submission packager, and a responsive investigation workstation inspired by DepthWizard and VeilGraph's restrained scientific design language.

## What ships

- **518-case batch auditor** with exact CSV/JSON contract enforcement.
- **Historical identity graph** across current identity data and audit-log mutations.
- **Evidence adapters** for JSON, SQLite-compatible SQL, structured logs, text, PDFs, and screenshots.
- **Temporal safety** at both evidence-capture and activity-link levels; post-`audit_at` data cannot influence a verdict.
- **Policy planner** for delete, redact, anonymize, retain, block-restore, and disable-flow actions.
- **Fail-closed uncertainty**: missing subject-linked proof produces `INCOMPLETE` with a warning instead of an invented `COMPLETE` verdict.
- **Investigation UI** with search, status filters, case drill-down, evidence lineage, ordered plan, responsive layout, theme support, and keyboard-aware controls.
- **Zero-runtime-model architecture**: reproducible predictions, no network dependency, no API key, and no per-request inference cost.

## Architecture

```text
deletion_requests ── intent gate ── historical identity resolver
                                           │
evidence manifest ── format adapters ── temporal evidence index
                                           │
system activity ── cutoff-safe resource/subject links
                                           │
deletion_rules ─────────────── policy reducer + state planner
                                           │
                           strict validator ── output.csv
                                           │
                           investigation API + workstation UI
```

The workflow's reported status is treated as a claim, never proof. GhostTrace decides from participant-visible state evidence captured at or before the audit cutoff.

## Requirements

- Python 3.11 or later.
- No Python package installation is required.
- Optional but recommended system tools:
  - `pdftotext` (Poppler) for PDF evidence.
  - `tesseract` for screenshot evidence.

When an optional extractor is unavailable, GhostTrace completes the run, surfaces the missing adapter in diagnostics, and never silently fabricates observations.

## Run the production audit

From the repository root:

```bash
python3 code/main.py run
python3 code/main.py validate
```

This writes `output.csv` in the repository root and validates all 518 rows, statuses, step schemas, evidence rules, numbering, and canonical ordering before reporting success.

Useful commands:

```bash
# Inspect extraction coverage and warnings
python3 code/main.py diagnostics

# Run the investigation workstation
python3 code/main.py serve --host 127.0.0.1 --port 8787

# Run the complete regression suite
python3 -m unittest discover -s code/tests -v

# Evaluate against the participant-visible sample
python3 code/evaluation/main.py

# Build the dataset-free submission archive
python3 code/package_submission.py
```

Then open [http://127.0.0.1:8787](http://127.0.0.1:8787). The UI and API are served by the Python standard library; no frontend toolchain or separate service is required.

## Decision pipeline

1. **Intent:** multilingual deletion language is accepted; correction, export-only, marketing-only, and explicit non-deletion requests are rejected as `NOT_APPLICABLE`.
2. **Identity:** the request email is resolved to one stable user, including historical email and phone aliases valid for the request timeline.
3. **Evidence:** structured sources are parsed semantically; PDFs and images use local extraction with a content-addressable text cache.
4. **Time:** both evidence captures and resource-to-subject activity links must exist at or before `audit_at`.
5. **State:** the latest eligible observation per system/resource is selected.
6. **Policy:** the required terminal state is derived by category.
7. **Plan:** violations generate canonically ordered findings, remediation, and verification; compliant cases generate evidence-backed verification only.
8. **Validation:** output is rejected if it violates any part of the challenge contract.

## Output contract

```text
case_id,deletion_status,steps_json
```

- Exactly one canonically sorted row per request.
- Status is `COMPLETE`, `INCOMPLETE`, or `NOT_APPLICABLE`.
- `NOT_APPLICABLE` always has `[]`.
- `COMPLETE` contains only evidence-backed verification steps.
- `INCOMPLETE` findings cite evidence; proposed remediation and future verification deliberately use an empty `evidence_id`.
- Every step contains exactly the seven required fields and uses the specified phase and operation ordering.

## Evaluation snapshot

On the 32 participant-visible labeled cases:

| Metric | Result |
| --- | ---: |
| Case status accuracy | 100.000% |
| Exact row / exact sequence accuracy | 50.000% |
| Step-field accuracy | 78.792% |
| Step precision | 79.651% |
| Step recall | 70.984% |

GhostTrace reaches every participant-visible sample verdict without reading labels at prediction time. Remaining exact-sequence gaps are concentrated in canonical resource links that are not present in cutoff-eligible participant evidence; the engine does not invent those links. Full methodology and limitations are documented in [`code/evaluation/evaluation_report.md`](code/evaluation/evaluation_report.md).

The final full-data run currently produces 143 `COMPLETE`, 365 `INCOMPLETE`, and 10 `NOT_APPLICABLE` decisions. Its `output.csv` passes the strict validator with 518/518 rows. Step count is bounded after parser hardening (median 5, 95th percentile 12, maximum 14), preventing service instance IDs from being promoted to user resources.

## Security and privacy posture

- Binds to loopback by default.
- Emits no case identifiers or PII to HTTP access logs.
- Uses no telemetry, remote model, analytics SDK, cookie, or third-party asset.
- Stores only local extracted-text cache entries, excluded from version control and submission.
- Resolves static paths before serving to prevent traversal.
- Never trusts workflow completion claims as evidence.

## Repository guide

```text
code/
├── main.py                  CLI and batch entry point
├── package_submission.py    deterministic archive builder
├── ghosttrace/
│   ├── domain.py            typed domain model
│   ├── identity.py          temporal identity resolution
│   ├── intent.py            multilingual intent gate
│   ├── evidence.py          multi-format evidence adapters/index
│   ├── engine.py            policy reducer and plan generator
│   ├── validation.py        strict output contract validator
│   └── server.py            local investigation API/server
├── ui/                      production workstation
├── tests/                   regression and safety gates
└── evaluation/              evaluator and required reports
```

See [`problem_statement.md`](problem_statement.md) for the official participant contract and [`WINNING_STRATEGY.md`](WINNING_STRATEGY.md) for the verified forensic strategy.

## Submission

Build `code.zip` with `python3 code/package_submission.py`, submit the root `output.csv`, and upload `log.txt` as the chat transcript. The challenge submission page is:

https://www.hackerrank.com/contests/hackerrank-orchestrate-october26/challenges/ghost-data-still-exists
