# GhostTrace Evaluation Report

## Scope and integrity

This report evaluates the final runtime against participant-visible material only. The engine never loads organizer truth, generator internals, or hidden answers. `sample_output.csv` is used only by the evaluation command and regression gate; it is not read by the prediction engine.

Command:

```bash
python3 code/evaluation/main.py
```

Environment: Python 3.14.0, deterministic local execution, 32 public sample cases, 518 full-dataset cases, 94 evidence files, and 2,903 extracted observations. Extraction completed with zero adapter warnings in the measured environment.

## Public-sample metrics

| Level | Definition | Result |
| --- | --- | ---: |
| Case accuracy | Correct `deletion_status` | 29/32 = **90.625%** |
| Row accuracy | Status and complete ordered step array exactly match | 6/32 = **18.750%** |
| Field accuracy | Micro accuracy across seven aligned step fields; missing/extra steps count as seven misses | **66.062%** |
| Sequence accuracy | Exact ordered `(phase, system, resource, operation, state)` sequence | 6/32 = **18.750%** |
| Step precision | Exact predicted step objects also present in target | **62.346%** |
| Step recall | Exact target step objects recovered | **52.332%** |

Status accuracy is the primary readiness gate because it measures whether GhostTrace reaches the correct deletion conclusion. Exact sequence is lower because the participant-visible evidence does not consistently expose every subject-to-resource edge implied by the sample plan. The runtime intentionally refuses to recover those edges from labels.

## Full-dataset contract evaluation

The production command emitted 518/518 canonically sorted rows and passed every validator rule:

- Exact header and one row per request.
- 142 `COMPLETE`, 366 `INCOMPLETE`, 10 `NOT_APPLICABLE`.
- Valid JSON arrays and consecutive step numbering.
- Legal status, phase, operation, and state combinations.
- Evidence on `COMPLETE` verification and `INCOMPLETE` findings.
- Empty evidence IDs on future remediation/verification.
- Canonical phase, operation, system, and resource ordering.

Plan-size distribution after parser hardening: median 5 steps, 95th percentile 12, maximum 14. No plan exceeds 20 steps.

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

## Remaining limitations

Three public status misses (`case_01`, `case_02`, `case_04`) have canonical `COMPLETE` answers but no participant-visible, cutoff-eligible subject-to-resource connection that GhostTrace can substantiate. The system returns `INCOMPLETE` with no invented steps. This is an intentional audit-safety choice.

OCR quality depends on the locally installed Tesseract build. Missing `pdftotext` or Tesseract is reported in diagnostics; the run does not silently pretend those files were parsed. Free-form OCR observations carry reduced confidence.

## Reproduction

```bash
python3 -m unittest discover -s code/tests -v
python3 code/evaluation/main.py
python3 code/main.py run
python3 code/main.py validate
```

Final regression result: **9/9 tests pass**.
