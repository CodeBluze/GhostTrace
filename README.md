# GhostTrace

GhostTrace is an evidence-grounded deletion-audit system for the HackerRank Orchestrate challenge **Ghost Data Still Exists**. It reconstructs historical identities, reads heterogeneous evidence, enforces the case-specific time cutoff, applies deletion policy, and emits a validated remediation plan for every request.

The submission is a complete local application: a deterministic audit engine, multi-format evidence pipeline, strict output validator, evaluation harness, submission packager, and a responsive investigation workstation inspired by DepthWizard and VeilGraph's restrained scientific design language.

## Download the verified submission

Judges and reviewers can download the frozen submission directly from the public [`submission-v1.0.0` GitHub Release](https://github.com/CodeBluze/GhostTrace/releases/tag/submission-v1.0.0). No GitHub sign-in or repository clone is required.

| Required artifact | One-click download | Purpose |
| --- | --- | --- |
| `code.zip` | **[Download runnable solution](https://github.com/CodeBluze/GhostTrace/releases/download/submission-v1.0.0/code.zip)** | Code, prompts, configuration, README, and both required evaluation reports |
| `output.csv` | **[Download 518-row prediction file](https://github.com/CodeBluze/GhostTrace/releases/download/submission-v1.0.0/output.csv)** | Final answers, sorted by `case_id` |
| `log.txt` | **[Download AI chat transcript](https://github.com/CodeBluze/GhostTrace/releases/download/submission-v1.0.0/log.txt)** | Required development conversation log |

GitHub is a convenient mirror for reviewers. The official submission must still be uploaded through the [HackerRank challenge page](https://www.hackerrank.com/contests/hackerrank-orchestrate-october26/challenges/ghost-data-still-exists).

## What ships

- **518-case batch auditor** with exact CSV/JSON contract enforcement.
- **Historical identity graph** across current identity data and audit-log mutations.
- **Evidence adapters** for JSON, SQLite-compatible SQL, structured logs, text, PDFs, and screenshots.
- **Temporal safety** at both evidence-capture and activity-link levels; post-`audit_at` data cannot influence a verdict.
- **Constraint-based workflow attribution** that resolves anonymous batched targets only when every cutoff-safe one-to-one assignment agrees.
- **Data-driven policy planner** that loads `deletion_rules.csv` and resolves delete, redact, anonymize, retain, block-restore, and disable-flow actions.
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
batched workflows ── invariant assignment solver
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
3. **Evidence:** JSON, cloud-envelope, syslog, pipe-delimited, SQL, and structured-text sources are parsed semantically; PDFs and images use local extraction with a content-addressable text cache.
4. **Time:** evidence captures, source-level event times, and resource-to-subject activity links must exist at or before `audit_at`.
5. **Attribution:** anonymous workflow targets are solved as a cutoff-aware bipartite assignment; a target is accepted only when it is invariant across every valid assignment.
6. **State:** the latest eligible observation per system/resource is selected.
7. **Policy:** the required terminal state is derived by category.
8. **Plan:** violations generate canonically ordered findings, remediation, and verification; compliant cases generate evidence-backed verification only.
9. **Validation:** output is rejected if it violates any part of the challenge contract.

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
| Step-field accuracy | 80.118% |
| Step precision | 81.034% |
| Step recall | 73.057% |

GhostTrace reaches every participant-visible sample verdict without reading labels at prediction time. Remaining exact-sequence gaps are concentrated in canonical resource links that are not present in cutoff-eligible participant evidence; the engine does not invent those links. Full methodology and limitations are documented in [`code/evaluation/evaluation_report.md`](code/evaluation/evaluation_report.md).

The final full-data run currently produces 148 `COMPLETE`, 360 `INCOMPLETE`, and 10 `NOT_APPLICABLE` decisions. Its `output.csv` passes the strict validator with 518/518 rows. Every authorized case now has an evidence-grounded plan containing the status-required phases; no `COMPLETE` or `INCOMPLETE` row is empty. Step count remains bounded after parser hardening (median 6, 95th percentile 13, maximum 14), preventing service instance IDs from being promoted to user resources. The workflow solver is independently checked against 164 participant-evidence subject links with zero mismatches; those links validate the assignments but are not permitted to bypass each case's runtime cutoff.

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
│   ├── workflow.py          cutoff-safe batch assignment solver
│   ├── policy.py            deletion_rules.csv policy interpreter
│   ├── engine.py            policy reducer and plan generator
│   ├── validation.py        strict output contract validator
│   └── server.py            local investigation API/server
├── ui/                      production workstation
├── tests/                   regression and safety gates
└── evaluation/              evaluator and required reports
```

See [`problem_statement.md`](problem_statement.md) for the official participant contract and [`WINNING_STRATEGY.md`](WINNING_STRATEGY.md) for the verified forensic strategy.

## Submission and verification

Build `code.zip` with `python3 code/package_submission.py`, submit the root `output.csv`, and upload `log.txt` as the chat transcript. The archive includes the required reports at the exact paths `evaluation/evaluation_report.md` and `evaluation/usage_report.md`; their source-of-truth copies remain beside the evaluator under `code/evaluation/`.

For reproducibility, compare downloaded files with the SHA-256 checksums published in the [`submission-v1.0.0` release notes](https://github.com/CodeBluze/GhostTrace/releases/tag/submission-v1.0.0). The authoritative upload destination is:

https://www.hackerrank.com/contests/hackerrank-orchestrate-october26/challenges/ghost-data-still-exists
