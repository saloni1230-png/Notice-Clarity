Notice Clarity
An English-only evidence workspace for Indian notices — built to help teams review, organize, and understand regulatory and procedural notices with traceable evidence, source-backed context, and safer AI-assisted workflows.

Notice Clarity is designed for organizations that need to ingest notices from PDFs, images, and scanned documents, extract the important facts, and surface reliable excerpts for human review. The project combines document processing, structured retrieval, optional AI support, and strong operational safeguards to keep the workflow practical, auditable, and review-oriented.

Why this project exists
Notices often arrive in messy formats: scanned PDFs, screenshots, email attachments, and procedural documents with incomplete metadata. The goal of Notice Clarity is not to replace legal judgment, but to make the review process more structured and evidence-driven.

It helps teams:

ingest notices from uploaded documents
extract and normalize key content
classify and understand the document
retrieve relevant source material and procedural context
keep review trails and operational controls in place
support human decision-making with grounded excerpts rather than unchecked model output
Features
Document intake for PDF and image-based notices
FastAPI-powered backend with PostgreSQL persistence
Worker-based processing for extraction, OCR, AI, mail, and maintenance jobs
Structured retrieval using indexed passages and optional embeddings
AI-assisted insight generation with provider configuration and safety guardrails
Browser-based interface for uploading and reviewing notices
Strong operational controls for security, email, storage, and job retries
Local Docker development workflow and production-oriented deployment patterns
Tech stack
Python 3.12
FastAPI
SQLAlchemy + PostgreSQL
Alembic migrations
Docker + Docker Compose
Playwright for browser testing
Optional AI model integrations for structured extraction and reasoning
HTML/CSS/JavaScript frontend served from the app
Project structure
Notice-Clarity/
├── notice/                  # Main application package
│   ├── ai.py                # AI integrations and model contracts
│   ├── api.py               # API layer
│   ├── cli.py               # Admin/CLI tooling
│   ├── documents.py         # Document logic
│   ├── pipeline.py          # Processing pipeline
│   ├── retrieval.py         # Search and retrieval
│   ├── understanding.py     # Notice interpretation logic
│   ├── security.py          # Security and auth logic
│   ├── storage.py           # Storage abstraction
│   ├── worker.py            # Worker entry points
│   ├── web/                 # Browser UI assets
│   └── ...
├── migrations/              # Database migration scripts
├── tests/                   # Integration and behavior tests
├── evals/                   # Evaluation fixtures
├── ops/                     # Production deployment configuration
├── scripts/                 # Dev and operational scripts
├── compose.yaml             # Local Docker orchestration
├── Dockerfile               # App image
├── pyproject.toml           # Python project metadata and dependencies
├── package.json             # Browser test tooling
├── START_HERE.txt           # Quick-start operational guide
├── operations.txt           # Deployment and security runbook
├── verification.json        # Validation / maturity status
├── requirements.lock        # Reproducible runtime dependencies
├── requirements-dev.lock    # Dev dependencies
├── .env.example             # Example environment file
├── README.md                # Project documentation
└── ...
