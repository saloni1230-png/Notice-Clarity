<div align="center">

<img src="notice/web/mark.svg" alt="Notice Clarity mark" width="80" />

# Notice Clarity

**Understand an Indian notice, and prove every word of it.**

An English-only evidence workspace that turns messy notices into traceable facts, exact source excerpts, and review-ready next steps.

[![CI](https://github.com/saloni1230-png/Notice-Clarity/actions/workflows/ci.yaml/badge.svg)](https://github.com/saloni1230-png/Notice-Clarity/actions/workflows/ci.yaml)
![Python](https://img.shields.io/badge/Python-3.12-3776AB?logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-009688?logo=fastapi&logoColor=white)
![PostgreSQL](https://img.shields.io/badge/PostgreSQL-17-4169E1?logo=postgresql&logoColor=white)
![Docker](https://img.shields.io/badge/Docker-ready-2496ED?logo=docker&logoColor=white)

[Quick start](#quick-start) · [How it works](#how-it-works) · [Configuration](#configuration) · [API](#api) · [Operations](#operations-and-admin-cli) · [Status](#honest-status)

</div>

---

## The problem

A notice arrives as a blurry scan, a screenshot, or a 40-page PDF. The deadline is buried on page 7, the reference number must be quoted exactly, and the amount must not be misread. Most tools either dump raw text on you or ask you to trust a chatbot's paraphrase.

Notice Clarity takes a different position: **nothing is shown unless it can be traced back to the page.**

It does not replace legal judgment. It makes review faster and auditable, so the human decision rests on evidence.

## What makes it different

| | Typical "AI document" tools | Notice Clarity |
|---|---|---|
| Facts | Generated and paraphrased | Extracted and verified against exact source text |
| No AI key | Unusable | Still extracts issuer, references, amounts and dates, and answers factual questions |
| Risky interpretations | Stated confidently | Withheld, with an extractive fallback and a review flag |
| Legal sources | Recalled from model memory | Only approved, versioned snapshots with effective dates |
| Traceability | Hard to check | Highlighted on the page image, word by word |
| Operations | Demo-grade | Durable queues, retries, dead letters, budgets, audit trail |

## How it works

```mermaid
flowchart LR
    A[Upload PDF or image] --> B[Validate and virus scan]
    B --> C[Text extraction]
    B --> D[OCR]
    C --> E[Typed facts]
    D --> E
    E --> F[Evidence verifier]
    F --> G[Passage index]
    G --> H[Optional structured AI]
    H --> I[Support verification]
    I -->|supported| J[Sourced summary, checklist, Q and A]
    I -->|unsupported| K[Withheld: exact excerpts only]
    F --> J
    J --> L[Professional review and audit trail]
```

1. **Ingest.** PDF, PNG or JPEG, up to 20 MB and 80 pages by default. Files are scanned for malware and encrypted PDFs are rejected.
2. **Extract.** Embedded text is read directly; scans go through Tesseract OCR with weighted confidence and rotated-page coordinates.
3. **Ground.** Each fact (issuer, request, reference, amount, date) is checked against the exact page text. Numbers and dates get strict checks, and ambiguity is flagged rather than guessed.
4. **Retrieve.** Permission-filtered BM25 over full pages and approved source snapshots, with optional embeddings, rank fusion and cross-encoder reranking. Without an embedding model, search explicitly runs lexical-only.
5. **Explain (optional AI).** A structured-output model drafts insights. A separate verification pass must confirm support for each claim; anything unsupported is withheld.
6. **Review.** Context collection, quoted deadlines, action checklists, Q&A and an institution-assigned professional reviewer.

**Knowledge sources.** Official-host pages are ingested as snapshots (URL, digest, fetch time). A qualified reviewer must approve each one, with effective dates, before the AI may cite it. Unapproved, stale or superseded sources cannot back a legal-source claim, and no statute is ever seeded from model memory.

## Highlights

- **Evidence viewer:** page rendering behind authorization (the browser never receives the original PDF) with approximate word-level highlights
- **Durable engine:** five independent worker pools (`extract`, `ocr`, `ai`, `mail`, `maintenance`) using `SKIP LOCKED` queues, heartbeats, lease fencing, retries and dead letters, with per-chunk checkpoints
- **AI guardrails:** off by default, OpenAI-compatible provider, tokenizer-based budgets, and per-organization daily token, cost and concurrency limits
- **Security:** Argon2id passwords, email verification, session revocation, CSRF and origin protection, encrypted tokens and mail outbox, tenant-scoped deduplication, organization roles and an audit log
- **Hardened containers:** read-only filesystem, `cap_drop: ALL`, `no-new-privileges`, memory and CPU limits
- **Storage:** local volume in development; private S3 with SSE-KMS in production. Startup refuses to run if core production controls are missing.
- **Observability:** liveness and readiness probes, Prometheus metrics and alert rules

## Architecture

```mermaid
flowchart TB
    UI[Browser UI] <--> API[FastAPI service]
    API <--> DB[(PostgreSQL 17)]
    API --> OBJ[(Object storage)]
    API --> AV[ClamAV]
    W[Worker pools: extract, ocr, ai, mail, maintenance] <--> DB
    W --> OBJ
    W --> LLM[Optional AI provider]
    W --> SMTP[SMTP]
```

For small deployments, set `NS_EMBEDDED_WORKER=true` and run everything as a single web service with `python -m notice.serve --migrate` (see `SINGLE_SERVICE.txt`). For scale, run the worker pools as separate containers, as the Compose files do.

## Quick start

**Prerequisite:** Docker Engine or Docker Desktop with Linux containers.

```bash
git clone https://github.com/saloni1230-png/Notice-Clarity.git
cd Notice-Clarity

sh start.sh                                                   # Linux / macOS
powershell -ExecutionPolicy Bypass -File .\start.ps1          # Windows
```

The script copies `.env.example` to `.env` if needed and runs `docker compose up --build -d`.

| Service | URL |
|---|---|
| App | http://localhost:65030 |
| Mailpit (optional email-flow testing) | http://localhost:8025 |

1. Register with an email and a password of **12+ characters**. Development mode skips email verification.
2. Upload a PDF, PNG or JPEG, or use the synthetic example in the UI.
3. Open a case to explore the evidence viewer, context, actions and Q&A.

> **First run:** ClamAV must finish downloading signatures before uploads work. Watch with `docker compose logs -f clamav`.

> **Note:** the Docker build and run path has not yet been verified on a clean Linux CI host (see [Honest status](#honest-status)). If something fails, please open an issue.

### Enable structured AI

Edit the untracked `.env`, then run `docker compose up -d`:

```bash
NS_AI_API_KEY=your-provider-key
NS_AI_MODEL=your-tested-structured-output-capable-model
NS_AI_STRONG_MODEL=                 # optional stronger model
NS_AI_PRICE_INPUT_PER_MILLION=      # real maximum USD per million input tokens
NS_AI_PRICE_OUTPUT_PER_MILLION=     # real maximum USD per million output tokens
NS_EMBEDDING_MODEL=                 # optional, enables hybrid retrieval
```

Prices are never defaulted; take them from your provider. Budget reservations are conservative and are not refunded on timeout. Assess provider privacy, retention and model quality before processing real notices.

<details>
<summary><b>Run without Docker</b></summary>

Requires Python 3.12+, PostgreSQL 17 and Tesseract 5 with `eng` language data.

```bash
python -m venv .venv
.venv/bin/python -m pip install -r requirements.lock -r requirements-dev.lock
.venv/bin/python -m pip install --no-deps -e .

export NS_DATABASE_URL=postgresql+psycopg://notice:notice@localhost:5432/notice
export NS_LOCAL_STORAGE=./.runtime/objects
export NS_SMTP_HOST=localhost NS_SMTP_PORT=1025
export NS_SCANNER_HOST=localhost

alembic upgrade head
python scripts/dev.py     # API plus workers; development only
```

On Windows, use `.venv\Scripts\python` instead of `.venv/bin/python`.

</details>

## Configuration

All settings are environment variables with the `NS_` prefix (see `notice/config.py` and `.env.example`). Common ones:

| Variable | Default | Purpose |
|---|---|---|
| `NS_ENVIRONMENT` | `development` | `production` enforces core security controls at startup |
| `NS_DATABASE_URL` | local Postgres | Must use the `postgresql+psycopg://` driver |
| `NS_SECRET` | dev placeholder | 64+ random characters in production; rotating it logs everyone out |
| `NS_PUBLIC_URL` | `http://localhost:8000` | Public base URL |
| `NS_STORAGE` | `local` | `local` or `s3` (with `NS_S3_BUCKET`, `NS_S3_KMS_KEY`, `NS_S3_REGION`) |
| `NS_EMBEDDED_WORKER` | `false` | Run workers inside the API process |
| `NS_REQUIRE_EMAIL_VERIFICATION` | `true` | Disabled only in local development |
| `NS_MAX_UPLOAD_MB` / `NS_MAX_PAGES` | `20` / `80` | Upload limits |
| `NS_MAX_DOCUMENTS_PER_DAY` | `30` | Per-organization upload limit |
| `NS_ORG_DAILY_TOKENS` / `NS_ORG_DAILY_COST_USD` | `80000` / `3.0` | Per-organization AI budget |
| `NS_ORG_AI_CONCURRENCY` | `2` | Concurrent AI calls per organization |
| `NS_SCANNER_HOST` | empty | ClamAV host; empty disables scanning (synthetic dev only) |
| `NS_SMTP_HOST` / `NS_SMTP_PORT` | `mailpit` / `1025` | Mail relay |
| `NS_METRICS_TOKEN` | empty | Protects `/metrics` |

## API

The OpenAPI schema is served at `/api/openapi.json` outside production (there is no Swagger UI; it is disabled in the app and the schema is turned off in production).

| Area | Endpoints |
|---|---|
| Health | `GET /health/live` · `GET /health/ready` · `GET /metrics` |
| Auth | `POST /api/auth/register`, `login`, `logout`, `verify`, `resend`, `recover`, `reset` · `GET /api/auth/sessions` |
| Cases | `GET, POST /api/cases` · `GET, DELETE /api/cases/{id}` · `POST …/reprocess` · `GET …/export` |
| Evidence | `GET …/pages/{n}` · `GET …/pages/{n}/image` · `PATCH …/context` |
| Q&A and actions | `POST …/questions` · `POST, PATCH …/actions` · `POST …/review` |
| Sources | `GET /api/sources` |
| Organization | `GET, POST, DELETE /api/org/members` · `GET /api/org/audit` |

Long-running work (upload, reprocess, questions, deletion) returns `202 Accepted` and is completed by the workers.

## Operations and admin CLI

```bash
docker compose exec api notice-admin doctor            # environment self-check
docker compose exec api notice-admin queue-status      # job pools at a glance
docker compose exec api notice-admin redrive           # retry dead-lettered jobs
docker compose exec api notice-admin cleanup           # retention sweep
docker compose exec api notice-admin storage-orphans   # reconcile object storage

docker compose exec api notice-admin sources-fetch
docker compose exec api notice-admin sources-status
docker compose exec api notice-admin source-show SOURCE_ID
docker compose exec api notice-admin source-approve SOURCE_ID \
    --reviewer "Reviewer identity" --days 14 --valid-from YYYY-MM-DD --valid-to YYYY-MM-DD
```

The starter source manifest is a portal and procedure discovery list, **not a legal corpus**. A qualified reviewer must inspect each snapshot before approval.

Production uses `ops/compose.production.yaml` with a Caddy TLS proxy, managed PostgreSQL, private S3/KMS, authenticated SMTP and a private ClamAV. Alert rules are in `ops/prometheus-alerts.yaml`; the full runbook (deploy, restore, retention, failure handling) is [`operations.txt`](operations.txt).

## Project structure

```text
Notice-Clarity/
├── notice/                 # Application package
│   ├── api.py              #   Routes and authorization
│   ├── pipeline.py         #   Processing pipeline
│   ├── documents.py        #   PDF/image handling, OCR, rendering
│   ├── evidence.py         #   Exact-quote and numeric/date verification
│   ├── understanding.py    #   Question planning, fact structuring
│   ├── explanations.py     #   Summaries, fallbacks, missing context
│   ├── retrieval.py        #   BM25, embeddings, fusion, reranking
│   ├── knowledge.py        #   Official source ingestion and approval
│   ├── ai.py               #   Structured model contract and budgets
│   ├── jobs.py, worker.py  #   Durable queue and worker pools
│   ├── security.py         #   Auth, sessions, CSRF
│   ├── storage.py          #   Local / S3-KMS abstraction
│   ├── cli.py              #   notice-admin
│   └── web/                #   Browser UI
├── migrations/             # Alembic migrations
├── tests/                  # Integration, security, AI-contract, browser, load
├── evals/                  # Synthetic regression fixtures
├── ops/                    # Production compose, Caddy, alert rules
├── scripts/                # Dev launcher, evaluation, benchmarks, dependency audit
├── compose.yaml            # Local development stack
├── operations.txt          # Runbooks
└── verification.json       # Implemented / tested / pending status
```

## Testing

```bash
# Use an isolated database named notice_test: the tests TRUNCATE its tables
export NS_DATABASE_URL=postgresql+psycopg://notice:notice@localhost:5432/notice_test
alembic upgrade head

python -m pytest                 # backend suite
python scripts/evaluate.py       # synthetic regression cases
ruff check .                     # lint

npm ci && npx playwright install chromium
npx playwright test              # browser and axe accessibility checks
```

Last recorded results (`verification.json`, 2026-10-20): 108 backend tests passed and 1 skipped (needs Tesseract) on PostgreSQL 17, 82.6% line coverage, 2 browser tests passed, no serious or critical axe violations in the tested screens, and 0 known advisories across 52 runtime packages. Live status is on the CI badge above.

## Troubleshooting

| Symptom | Likely cause and fix |
|---|---|
| Uploads fail right after first start | ClamAV is still downloading signatures. Check `docker compose logs -f clamav`. |
| `docker compose` fails immediately | Docker is not running, or it is set to Windows containers. Start Docker and switch to Linux containers. |
| Port `65030` or `8025` already in use | Stop the other process, or change the published port in `compose.yaml`. |
| Registration works but no verification email | Development skips verification. For the email flow, open Mailpit at `localhost:8025`. |
| OCR test skipped | Tesseract with `eng` data is not installed (local Python runs only). |
| Q&A shows excerpts, not interpretations | AI is not configured, or a claim failed verification. This is by design. |
| API refuses to start in production | A required control is missing (secret, S3/KMS, scanner, SMTP, metrics token). Read the startup error. |

## Honest status

**State:** implemented and locally tested; public production deployment not yet verified. Details are in [`verification.json`](verification.json).

**Still to validate before real-world use**
- Docker image build and run on a Linux CI host
- Real OCR on representative noisy scans
- Live AI, embedding, SMTP, S3/KMS and ClamAV integrations (mocked in tests)
- A professionally reviewed Indian legal corpus, and evaluation on redacted real notices
- External penetration testing, capacity testing, and backup and restore drills

**By design, it does not**
- give legal advice, file replies, make payments or calculate limitation periods
- offer per-case private sharing (all members of an organization see its active cases)
- provide a tamper-proof audit log (it is an application table, not immutable against DB administrators)
- support languages other than English
- claim any production latency, cost or accuracy SLA

## Contributing and security

Issues and pull requests are welcome. Before opening a PR, run `ruff check .`, `python -m pytest` and `npm run check`. Keep changes fenced by tests, and never commit real notices or credentials.

To report a vulnerability, please use GitHub's private security advisory feature on this repository rather than a public issue.

## Licensing and notices

This repository does not yet declare a license for its own code. Third-party components have their own terms; read [`THIRD_PARTY_NOTICES.txt`](THIRD_PARTY_NOTICES.txt) before distributing an image or binary. In particular, **PyMuPDF / MuPDF is AGPL or commercially licensed**.

Official portal content remains attributable to its authority. A source snapshot's approval is an operator review, not government endorsement of Notice Clarity.

---

<div align="center">

*Notice Clarity helps you read notices. It is not a substitute for professional legal advice.*

</div>
