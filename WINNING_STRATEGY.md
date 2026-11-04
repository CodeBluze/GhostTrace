# Ghost Data Still Exists - Competition Analysis and Winning Strategy

## 1. Source hierarchy

Use the following precedence whenever two sources differ:

1. Current official repository and `problem_statement.md`
2. Repository `AGENTS.md`
3. Participant challenge brief
4. Student-friendly guide

The student-friendly guide is explanatory, not normative. The official repository currently resolves to commit `0eddbbd33f682eae0a2770d67412af8a3cf4af20`.

## 2. The real problem

This is a temporal evidence-reconciliation and policy-compliance problem with an exact serialization contract. It is not primarily a free-form language-generation problem.

For each request, the system must:

1. Decide whether the text actually authorizes deletion.
2. Resolve the subject through current and historical identifiers.
3. Discover all subject-linked resources and processes.
4. Reconstruct each resource's state at `audit_at` using cutoff-eligible evidence.
5. Apply the policy target for the resource category.
6. Decide whether every required target is satisfied.
7. Emit the complete, exact, canonically ordered step array.

The reported workflow status and reported operation results are claims. They are useful for retrieval and provenance but are not proof of final state.

## 3. Verified dataset profile

- 518 evaluation requests (`case_33` through `case_550`)
- 32 public sample requests and outputs
- 850 current identity rows
- 306 historical identity-change rows
- 4,011 system-activity rows
- 15 deletion-policy rules
- 94 evidence artifacts
- 72 distinct evaluation cutoff times

Evidence formats:

- 28 JSON files
- 26 log files
- 24 SQLite-compatible SQL exports
- 11 PNG screenshots
- 4 PDFs
- 1 plain-text handover

Identity resolution is central:

- 418 evaluation request emails match the current identity snapshot.
- 100 match only through historical identity data.
- 0 are unresolved.
- 0 are ambiguous after exact normalized matching.

The ten requests `case_533` through `case_542` are non-deletion requests (correction, access, export, login recovery, invoices, or marketing opt-out) and therefore require `NOT_APPLICABLE` with `[]`. This conclusion comes from request intent, not from `reported_status`.

## 4. Temporal identity model

Build an identity graph keyed by stable `user_id` with typed aliases:

- current and historical email addresses
- case-normalized email addresses
- current and historical email hashes
- current and historical phone numbers
- stable user reference
- names, used only with corroboration

For a case cutoff, reconstruct the effective identity state rather than blindly using the latest snapshot:

1. Resolve the request email against the full identity graph.
2. Start from the current snapshot.
3. Reverse identity changes occurring after `audit_at` to recover values effective at cutoff.
4. Include identifiers that existed before or at cutoff for historical searches.
5. Do not use an identifier first introduced after cutoff to prove an earlier resource link.

Names are weak identifiers. A name-only match must not establish ownership without a stable reference, email, hash, phone, or corroborated resource link.

## 5. Canonical evidence model

Normalize every supported artifact into observations with this internal shape:

```text
Observation(
  evidence_id,
  observed_at,
  captured_at,
  system_id,
  resource_id,
  data_category,
  subject_keys,
  state_facts,
  binding_provenance,
  source_path
)
```

An observation is eligible only when both the artifact capture time and the embedded observation time are at or before `audit_at`.

Evidence adapters should cover:

- SQL tables and replica snapshots
- JSON snapshot bundles, service exports, and specialized ledgers
- key-value and structured service logs
- plain-text handovers
- PDF text extraction
- screenshot OCR with structural validation

The evidence manifest locates artifacts; it does not establish case ownership.

## 6. Resource-state reduction

For each subject/resource pair:

1. Collect cutoff-eligible direct observations.
2. Sort by effective observation time.
3. Prefer direct resource readback over workflow claims.
4. Use activity rows to bind a resource to a subject or discover a candidate, not to accept a reported result as final proof.
5. Resolve replica drift per system and snapshot scope; do not let a primary deletion hide a live regional, reporting, read, or failover replica.
6. Record contradictions and the rule used to select the authoritative state.

Absence requires scoped evidence: a targeted 404/zero-row lookup or a declared complete inventory for the relevant store. An empty array without declared scope is not automatically subject-specific proof.

## 7. Policy targets

The policy reduces each resource category to one required terminal treatment:

| Category | Required treatment |
|---|---|
| User profile | Delete |
| Secondary identifier | Delete |
| Search index entry | Delete |
| Cache entry | Delete |
| Support message with shared context | Redact |
| Subject-only support attachment | Delete |
| Attributable analytics profile | Anonymize |
| Attributable product telemetry | Anonymize |
| User export | Delete |
| Marketing profile | Delete |
| Email delivery log containing email | Redact |
| In-retention security audit record | Retain |
| Active legal hold record | Retain |
| Backup archive whose restore can be blocked | Retain and block restore |
| Account tombstone | Retain stable `user_id`; redact direct identifiers |

For a tombstone, name, email, phone, email hash, and any other prohibited direct identifier must be absent while the stable internal reference remains.

## 8. Status decision

### NOT_APPLICABLE

Use only when the request text does not authorize deletion. Return `[]`. Do not inspect or report deletion state because destructive action is not authorized.

### COMPLETE

Use only when all of these are proven at cutoff:

- every relevant resource satisfies its policy target;
- every required retained record remains present;
- every relevant data flow is disabled;
- every relevant restore path is blocked;
- retained/tombstone records expose only permitted identifiers.

Emit verification steps only, each with cutoff-eligible evidence.

### INCOMPLETE

Use when any relevant target is violated. Emit:

1. every evidence-backed finding;
2. the complete remediation plan;
3. all checks required to prove the final compliant state.

Findings cite evidence. Proposed remediation and all future verification steps use `evidence_id: ""`.

## 9. Exact step generation

Allowed finding mappings:

- deletable record still present -> `REPORT_REMAINING_RECORD/PRESENT`
- retained/redactable/anonymizable record exposes prohibited identifiers -> `REPORT_EXPOSED_PII/PRESENT`
- recreating connector/process active -> `REPORT_ACTIVE_DATA_FLOW/ACTIVE`
- restore path active -> `REPORT_RESTORE_RISK/ACTIVE`

Remediation and verification operations must be derived from policy, never generated by a language model.

Sort with fixed ranks:

1. Phase: `FINDING`, `REMEDIATION`, `VERIFICATION`
2. Operation rank exactly as listed in the challenge contract
3. `system_id` ascending
4. `resource_id` ascending

Assign `step_no` only after final sorting.

## 10. Recommended architecture

```text
CSV loaders
  -> temporal identity graph
  -> evidence manifest and adapters
  -> normalized observation store
  -> subject/resource linker
  -> cutoff state reducer
  -> policy engine
  -> finding/remediation/verification planner
  -> canonical sorter and serializer
  -> strict validator
```

Use a hybrid model:

- deterministic code for identity, time, policy, state reduction, operation selection, sorting, and CSV serialization;
- optional constrained model calls only for genuinely ambiguous multilingual intent or unstructured artifact extraction;
- cache any model-derived structured facts and validate them against a schema;
- never ask a model to write `steps_json` directly.

## 11. Known current-repository defects

The latest public repository has unresolved evidence gaps documented in open issues 19 and 24. Examples include:

- sample retained-security resource IDs that do not appear in participant-facing evidence;
- anonymous 404 account lookups without a participant-visible subject binding;
- anonymous analytics rows whose original ownership is not established;
- a backup observed before cutoff without a visible restore-authorization state;
- an empty snapshot collection used by a sample answer as if it were scoped absence proof;
- ambiguous account candidates in a public sample.

This means strict evidence reconstruction and hidden canonical output can diverge. The solution must not hide this.

Maintain an uncertainty ledger with:

- case and resource
- missing relation or state
- eligible sources inspected
- ineligible later sources
- deterministic fallback used, if any
- confidence and evaluation impact

For scoring, use a constrained fallback only after grounded retrieval is exhausted. The strongest permissible fallback is a one-to-one bipartite assignment inside a cutoff-aligned evidence batch, using independently discovered activity candidates and resource-type constraints. Do not hardcode case labels or numeric case families.

## 12. Validation gates

Reject the run unless all gates pass:

- exact header: `case_id,deletion_status,steps_json`
- exactly 518 data rows
- rows sorted by `case_id`
- exactly one row per input request
- status belongs to the allowed enum
- `steps_json` parses as an array
- every step contains exactly seven required fields
- consecutive step numbers from 1
- allowed phase/operation/state triples only
- canonical phase and operation order
- canonical `system_id`/`resource_id` order within operation
- `COMPLETE` has verification steps only and non-empty valid evidence IDs
- `INCOMPLETE` findings have valid evidence and future steps have empty evidence
- `NOT_APPLICABLE` has `[]`
- no evidence or observation after `audit_at`
- every evidence ID exists in the participant manifest/activity/audit data

## 13. Evaluation strategy

Measure public samples at multiple levels:

- status accuracy
- exact row accuracy
- exact step-array accuracy
- step precision/recall/F1 by operation and resource
- field accuracy
- sequence accuracy
- evidence-reference accuracy
- temporal-cutoff violations

Report two public-sample views:

1. raw comparison with `sample_output.csv`;
2. evidence-supported comparison with documented repository gaps isolated.

Add custom cases for at least:

- explicit non-deletion/negated deletion intent;
- historical email resolution;
- evidence captured after cutoff;
- replica drift after primary deletion;
- active sync recreating a deleted record;
- retained security record;
- redacted tombstone;
- backup present with restore blocked versus allowed.

## 14. Submission strategy

Deliver:

- root `output.csv`
- `code.zip` containing runnable code, README, prompts/configuration, and `code/evaluation/`
- root `log.txt` as the chat transcript
- reviewed Codex trace archive if requested by the event instructions
- interview narrative explaining temporal identity, evidence normalization, policy reduction, uncertainty handling, validation, and cost

Never include the dataset, virtual environments, caches, nested archives, credentials, or secrets in `code.zip`.

## 15. Non-negotiable winning principles

1. Evidence state beats workflow status.
2. Time cutoff beats later truth.
3. Stable identity closure beats email-only search.
4. Policy decides treatment; the model does not.
5. Every relevant resource must be covered.
6. Retained data is as important as deleted data.
7. Flows and restores must be neutralized before dependent record cleanup.
8. Exact ordering and evidence IDs are part of correctness, not formatting polish.
9. Uncertainty must be explicit and measurable.
10. Final output must be generated and validated deterministically.
