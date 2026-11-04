# GhostTrace Runtime Usage Report

## Final full-dataset run

GhostTrace's prediction runtime uses deterministic local parsing and policy logic. It makes no LLM, embedding, reranking, hosted OCR, or other metered model calls. This keeps the 518-case audit reproducible, offline-capable, and free from per-request data egress.

| Provider | Model | Calls | Input tokens | Output tokens | Estimated cost |
| --- | --- | ---: | ---: | ---: | ---: |
| None | None | 0 | 0 | 0 | $0.00 |
| **Overall** | — | **0** | **0** | **0** | **$0.00** |

| Aggregate | Value |
| --- | ---: |
| Requests audited | 518 |
| Total model calls | 0 |
| Calls per request | 0.000 |
| Total input tokens | 0 |
| Total output tokens | 0 |
| Total tokens | 0 |
| Average tokens per request | 0.000 |
| Estimated total model cost | $0.00 |
| Estimated model cost per request | $0.000000 |

## Local extraction

PDF text extraction uses the local `pdftotext` executable and image OCR uses the local Tesseract executable when installed. These are not hosted model API calls, have no token accounting, and incur no metered inference cost. JSON, SQL, logs, CSV, and text are parsed with Python's standard library.

## Development tooling disclosure

OpenAI Codex with GPT-5 assisted during software development and review. It is not imported, invoked, or required by the submitted prediction pipeline, and it made zero calls during the measured final full-dataset execution. Development-assistant token telemetry was not exposed to the repository runtime and is therefore not presented as fabricated usage data.
