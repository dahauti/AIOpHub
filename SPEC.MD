# SPECIFICATION: BC SaaS ⇄ Frontu Core Sync Bridge (Plikas Edition MVP)

## 1. System Context & Core Objectives
- Problem Statement: Replace legacy On-Premise Make.com pipelines with a resilient, self-hosted synchronization engine connecting Microsoft Business Central SaaS (v28) and Frontu FSM API v2.
- Business Objective: Ensure bidirectional data consistency (Customers/Equipment: BC -> Frontu; Service Work Orders/Consumables: Frontu -> BC) with zero duplicate transactions and complete auditability.
- Operational Surface: Provide a lightweight, zero-build web dashboard (FastAPI + HTMX) to monitor sync health, view dead-letter payloads, and trigger manual sync execution.

## 2. Tech Stack & Runtime
- Runtime: Python 3.12+ (isolated virtual environment in `.venv/`).
- Web Framework: FastAPI (REST API + Server-rendered HTMX UI).
- External Clients: HTTPX (async), Tenacity (resilience, retries, jitter).
- Persistence & State: PostgreSQL (via asyncpg and SQLAlchemy 2.0 async).
- Validation: Pydantic v2 (strict validation for incoming and outgoing schemas).
- Frontend: Jinja2 templates + HTMX (served directly via CDN, zero npm/node footprint).

## 3. Directory Layout
C:\AIOpHub\
├── .env.example
├── .gitignore
├── CLAUDE.md
├── SPEC.md
├── requirements.txt
├── src/
│   ├── __init__.py
│   ├── config.py             # Pydantic BaseSettings loading .env
│   ├── database.py           # Postgres async engine & sessionmaker
│   ├── main.py               # FastAPI app initialization & route mounting
│   ├── models/               # SQLAlchemy DB entities (sync audit, watermark)
│   │   ├── __init__.py
│   │   └── audit.py
│   ├── schemas/              # Pydantic DTOs for Frontu & BC payloads
│   │   ├── __init__.py
│   │   ├── bc_models.py
│   │   └── frontu_models.py
│   ├── adapters/             # Isolated third-party API clients
│   │   ├── __init__.py
│   │   ├── bc_client.py      # OAuth2 token lifecycle + OData/API v2 requests
│   │   └── frontu_client.py  # Frontu v2 API client (header auth, pagination)
│   ├── services/             # Core synchronization business logic
│   │   ├── __init__.py
│   │   ├── bc_to_frontu.py   # Outbound entity synchronizer
│   │   └── frontu_to_bc.py   # Inbound service act/order processor
│   ├── api/                  # API routers
│   │   ├── __init__.py
│   │   ├── routes_sync.py    # Trigger endpoints & webhook handlers
│   │   └── routes_ui.py      # HTMX HTML views for dashboard
│   └── templates/            # Minimal Jinja2 templates for UI
│       ├── base.html
│       ├── dashboard.html
│       └── partials/
│           ├── sync_status.html
│           └── audit_table.html
└── tests/
    ├── __init__.py
    ├── conftest.py
    ├── test_bc_client.py
    └── test_sync_engine.py

## 4. Database Schema (PostgreSQL sync_engine)

CREATE SCHEMA IF NOT EXISTS sync_engine;

CREATE TABLE IF NOT EXISTS sync_engine.sync_watermarks (
    entity_name VARCHAR(64) PRIMARY KEY, -- e.g., 'frontu_acts', 'bc_customers'
    last_sync_timestamp TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS sync_engine.sync_audit_log (
    id BIGSERIAL PRIMARY KEY,
    direction VARCHAR(16) NOT NULL,       -- 'FRONTU_TO_BC' or 'BC_TO_FRONTU'
    entity_name VARCHAR(64) NOT NULL,
    entity_external_id VARCHAR(128) NOT NULL,
    idempotency_hash VARCHAR(64) NOT NULL UNIQUE,
    status VARCHAR(32) NOT NULL,          -- 'SUCCESS', 'FAILED', 'RETRYING'
    payload JSONB NOT NULL,
    error_message TEXT,
    retry_count INT DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_audit_status ON sync_engine.sync_audit_log (status);
CREATE INDEX idx_audit_direction ON sync_engine.sync_audit_log (direction);

## 5. Core API Contracts & Resilience Rules

### BC Client (adapters/bc_client.py):
- Auth Protocol: OAuth2 Client Credentials (https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token).
- Token Lifecycle: Store access token in memory with expires_at. Refresh 5 minutes before expiration. Never trigger a token request per transaction.
- Throttling & Retries: Wrap all calls with @retry on HTTP 429 and HTTP 5xx. Exponential backoff factor 2, initial delay 1s, max delay 30s.

### Frontu Client (adapters/frontu_client.py):
- Auth Protocol: API key / token header auth.
- Pagination: Handle cursor/page-based delta fetching using updated_at > watermark.

### Idempotency Enforcement:
- Prior to pushing a record to BC, generate idempotency_hash = SHA256(external_id + modified_timestamp).
- If hash exists in sync_audit_log with SUCCESS, skip processing.

## 6. Implementation Roadmap for Claude

### Phase 1: Foundation & Client Scaffolding
1. Implement config.py using pydantic-settings to parse .env variables (BC_TENANT_ID, BC_CLIENT_ID, BC_CLIENT_SECRET, BC_ENVIRONMENT, BC_COMPANY_ID, FRONTU_API_KEY, DATABASE_URL).
2. Scaffold database.py with SQLAlchemy async session management.
3. Build adapters/bc_client.py with token caching and tenacity retry handler.
4. Build adapters/frontu_client.py.

### Phase 2: Domain Schemas & Sync Logic
1. Implement Pydantic models for Customers, Equipment, and Service Acts under schemas/.
2. Implement services/frontu_to_bc.py mapping completed tasks to BC Draft Sales Orders or Service Invoices.
3. Add audit persistence and watermark checkpointing to every sync run.

### Phase 3: Minimal Operational UI (HTMX)
1. Configure Jinja2 templates under src/templates/.
2. Build api/routes_ui.py returning dashboard components:
   - Summary cards: Total synced today, failed items count.
   - Audit log table with filter by status.
   - "Sync Now" button firing an async background task via HTMX hx-post.

## 7. Hard Constraints for Claude
- Zero additions of external JS frameworks (no Vue, React, Tailwind build steps). Use simple CDN CSS (Picocss or basic Bootstrap).
- File size strictly capped below 200 lines. Split logic across layers cleanly.
- Strict Python typing on all function signatures. No untyped dictionaries inside service logic.