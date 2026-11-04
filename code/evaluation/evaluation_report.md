# GhostTrace Evaluation Report

## Scope and integrity

This report evaluates the final runtime against participant-visible material only. The engine never loads organizer truth, generator internals, or hidden answers. `sample_output.csv` is used only by the evaluation command and regression gate; it is not read by the prediction engine.

Command:

```bash
python3 code/evaluation/main.py
```

Environment: Python 3.14.6, deterministic local execution, 32 public sample cases, 518 full-dataset cases, 94 evidence files, and 2,984 extracted observations. Extraction completed with zero adapter warnings in the measured environment.

## Public-sample metrics

| Level | Definition | Result |
| --- | --- | ---: |
| Case accuracy | Correct `deletion_status` | 32/32 = **100.000%** |
| Row accuracy | Status and complete ordered step array exactly match | 16/32 = **50.000%** |
| Field accuracy | Micro accuracy across seven aligned step fields; missing/extra steps count as seven misses | **80.118%** |
| Sequence accuracy | Exact ordered `(phase, system, resource, operation, state)` sequence | 16/32 = **50.000%** |
| Step precision | Exact predicted step objects also present in target | **81.034%** |
| Step recall | Exact target step objects recovered | **73.057%** |

Status accuracy is the primary readiness gate because it measures whether GhostTrace reaches the correct deletion conclusion. Across the accuracy-hardening releases, verdict accuracy rose from 90.625% to 100%, exact rows from 18.750% to 50%, precision from 62.346% to 81.034%, and recall from 52.332% to 73.057%. Exact sequence remains lower because the participant-visible evidence does not consistently expose every subject-to-resource edge implied by the sample plan. The runtime intentionally refuses to recover those edges from labels.

## Full-dataset contract evaluation

The production command emitted 518/518 canonically sorted rows and passed every validator rule:

- Exact header and one row per request.
- 148 `COMPLETE`, 360 `INCOMPLETE`, 10 `NOT_APPLICABLE`.
- Valid JSON arrays and consecutive step numbering.
- Legal status, phase, operation, and state combinations.
- Evidence on `COMPLETE` verification and `INCOMPLETE` findings.
- Empty evidence IDs on future remediation/verification.
- Canonical phase, operation, system, and resource ordering.

Plan-size distribution after parser hardening: median 6 steps, 95th percentile 13, maximum 14. No plan exceeds 20 steps, and every authorized case contains a non-empty status-appropriate plan.

## Additional evaluation cases

These tests were created independently of the labeled sample and are executed by `code/tests/test_engine.py`.

### 1. Multilingual authorized-deletion intent

Inputs include English, French, Portuguese, and Japanese permanent-deletion requests. Expected: all authorize deletion. Result: pass.

### 2. Explicit non-deletion boundaries

Inputs request a phone correction, a data export with explicit “not deletion,” and marketing-list removal only. Expected: all are `NOT_APPLICABLE`. Result: pass. On the full dataset, this gate identifies exactly cases 533–542.

### 3. Temporal evidence isolation

For every full-dataset case, every eligible observation must be timestamped at or before `audit_at`. Resource-to-subject activity links are evaluated with the same cutoff. Expected: no future leakage. Result: pass.

### 4. Structured-log resource confusion

Adversarial service instance values `account-5`, `analytics-4`, `search-2`, and `support-5` occur alongside true resource IDs. Expected: no instance name becomes a user resource. Result: pass.

### 5. Complete output contract

Generate the entire 518-row file in a temporary directory and run the strict validator. Expected: valid with all requested cases present once. Result: pass.

## Failures found and improvements made

1. **Cross-line overlinking in logs.** An early fallback scanner used a wide text window and could interpret service-instance names as resources. It produced plans as large as 79 steps. The structured-log path now requires explicit `resource_id=`, avoids free-form scanning, and has an adversarial regression test. Maximum plan size fell to 14.
2. **Post-cutoff relationship leakage.** Resource ownership learned from global activity was initially merged without evaluating the activity timestamp per case. Links are now retained with their event time and applied only when `event_time <= audit_at`.
3. **Trace grouping ambiguity.** Earlier grouping searched every token in a trace for resource-like values. It now accepts only the explicit resource field while still combining identity and terminal-state lines from the same trace.
4. **Non-deletion coverage.** The intent gate was expanded to cover multilingual requests and explicit negation/limited-scope boundaries, then checked against all ten public non-deletion cases.
5. **Silent uncertainty.** Evidence-free authorized requests now fail closed as `INCOMPLETE` with a warning rather than being declared complete.
6. **Policy-category ambiguity.** Support attachments, legacy contact indexes, and marketing contacts formerly inherited policy from only their resource prefix. Source semantics now distinguish delete, redact, and anonymize requirements.
7. **Ambiguous OCR ownership.** An OCR table window could contain adjacent users. An eligible authoritative catalog link now overrides free-form OCR ownership.
8. **Workflow/readback correlation.** Unique workflow targets can now be joined to independent cutoff-eligible absence or anonymization proof. The join is rejected when concurrent targets make attribution ambiguous.
9. **Negative account proof.** A uniquely attributed account workflow can use a later, cutoff-eligible complete account snapshot as absence proof; activity success alone is never treated as proof.
10. **Evidence integrity.** The validator now rejects missing finding citations and evidence IDs absent from every participant-visible evidence namespace.
11. **Anonymous batch attribution.** Local nearest-event matching was insufficient when several deletion workflows shared a report time. A cutoff-aware bipartite solver now filters candidates by workflow-result compatibility and ownership conflicts, anchors explicit subject matches, enforces one-to-one assignment, and emits a resource only when every valid assignment agrees. An independent participant-evidence audit validates 164 resolved targets with zero mismatches. Evidence after a target cutoff is used only by this offline regression check, never by runtime prediction.
12. **Source event-time reconstruction.** Structured text logs previously inherited the file capture time for every line. Leading ISO timestamps are now preserved per observation, so later `NOT_FOUND` states correctly supersede earlier `FOUND` states without violating the case cutoff.
13. **Multi-dialect trace loss.** Structured JSON lines, cloud-runtime envelopes, RFC-style syslog, and pipe-delimited operations streams placed subject and resource state on different lines under a shared trace identifier. The previous parser recognized only `trace_id=` syntax and left 15 authorized cases without plans. The trace correlator now parses all supplied dialects, preserves terminal resource timestamps, and reduces empty authorized plans from 15 to zero. Against the preceding release, this reconstructs 158 additional contract steps across 53 full-data rows while retaining the public verdict gate.
14. **Static policy duplication.** Required actions were previously mirrored in source code. The runtime now loads and validates `deletion_rules.csv`; only operational interpretations absent from its action vocabulary—disabling active flows and blocking an unsafe restore while retaining the archive—are derived by the policy interpreter.
15. **Catalog-to-artifact reconciliation.** Object-storage screenshots expose filenames while the data catalog exposes stable resource IDs. A time-scoped filename alias join now connects those two participant-visible facts, recovering `object_19` for case 61 without deriving an identifier from naming coincidence alone.
16. **Incomplete-plan validation.** The validator now rejects empty or phase-incomplete `INCOMPLETE` plans and empty `COMPLETE` verification arrays. This makes the brief's status-specific content requirement an executable release gate.

## Remaining limitations

All 32 public statuses now match. Sixteen exact-sequence gaps remain where the canonical plan includes additional resource relationships that are absent or ambiguous in cutoff-eligible participant evidence. For example, `account_004` appears in the canonical output but nowhere in the participant dataset outside that label. GhostTrace does not hardcode case IDs or synthesize such identifiers from naming coincidences; this preserves hidden-set generalization and audit integrity.

OCR quality depends on the locally installed Tesseract build. Missing `pdftotext` or Tesseract is reported in diagnostics; the run does not silently pretend those files were parsed. Free-form OCR observations carry reduced confidence.

## Reproduction

```bash
python3 -m unittest discover -s code/tests -v
python3 code/evaluation/main.py
python3 code/main.py run
python3 code/main.py validate
```

Final regression result: **29/29 tests pass**. The suite includes manifest-completeness, extraction-health, temporal-safety, trace-dialect, dynamic-policy, full-plan, alias-validity, invariant-assignment, independent-attribution, and byte-reproducible packaging gates so a checkout cannot silently omit evidence, accept an unsupported mapping, or emit a timestamp-dependent submission archive.
