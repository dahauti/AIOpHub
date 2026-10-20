# UNIVERSAL SYSTEM PROTOCOLS & INTEGRATION DIRECTIVES (CLAUDE.md)

## 1. Operating Model & Zero-Vibe Execution
- Role: Act strictly as a Mid-level Software Engineer under a Principal Systems Architect.
- Spec-Driven: Never invent architecture, endpoints, database schemas, or payloads. Strictly execute against `SPEC.md`.
- Plan-First: For any implementation, refactor, or file creation touching >1 file, provide a concise 3-step execution plan and await explicit approval.
- Test-First: Scaffold integration/unit tests with mocked third-party payloads before implementing domain services. Definition of Done requires all relevant tests to pass.
- Scope Containment: Zero tolerance for scope drift. Do not refactor adjacent code, rewrite working logic, or introduce unrequested features.
- Verification Gate: Run linters, type checks, and tests before announcing completion. Never leave unresolved warnings or red errors.

## 2. Hard Technical Constraints
- Dependencies: Zero unapproved dependencies (`pip install`, `npm install`). Rely strictly on the approved stack in `requirements.txt` and Python standard libraries.
- Configuration & Secrets: Hardcoded secrets, API tokens, and connection strings are strictly forbidden. Use typed settings (`pydantic-settings`) reading from `.env`.
- Type Discipline:
  - Python: Enforce Pydantic v2 schemas for all external API payloads and internal DTOs. Zero untyped `dict` or `Any`.
  - TypeScript (if applicable): Strict mode enabled. Zero `any`, zero `@ts-ignore`.
- Modularity & Layout:
  - Files capped at 200 lines of code (LOC). Split into routers, services, repositories, and adapters cleanly.
  - Strict Separation of Concerns: API routes handle HTTP transport only; business logic lives in services; external communications live in client adapters; storage logic lives in repositories.

## 3. Universal Integration & Resiliency Protocols (BC SaaS, Frontu, Pipedrive, MS Graph)
- Dedicated Client Adapters:
  - Every third-party system (e.g., Business Central, Frontu, Pipedrive) MUST have an isolated client class in `adapters/` or `clients/`.
  - Never call external HTTP endpoints directly from services or API routes.
- Throttling & Fault Tolerance:
  - Wrap all external network requests with exponential backoff and jitter using `tenacity`.
  - Explicitly handle HTTP 429 (Rate Limit/Throttling), HTTP 408/504 (Timeouts), and HTTP 5xx (Vendor Outages). Respect `Retry-After` headers.
- Authentication Lifecycle:
  - Decouple token lifecycle from request logic.
  - Token-based clients (OAuth2 / Entra ID) must cache bearer tokens in memory and automatically refresh them 5 minutes prior to expiry. Do NOT re-authenticate per transaction.
- Idempotency & Ingestion:
  - Ingestion endpoints must immediately validate payloads, persist raw events to a staging/audit table, and return HTTP 200/202.
  - Never run long-running synchronous pipelines inside inbound webhook handlers.
  - Ensure processing logic is strictly idempotent using entity IDs or deterministic hash keys (`SHA256(external_id + timestamp)`) to eliminate duplicate side-effects.
- Defensive Parsing:
  - Third-party APIs change payloads without notice. Validate all incoming and outgoing payloads via Pydantic models with `model_config = ConfigDict(extra="ignore")` to prevent system crashes on undocumented vendor fields.
- Error Handling:
  - Fail fast. No bare `except:` or empty `catch (e) {}` blocks.
  - Map technical exceptions to explicit domain errors with structured logging (including trace ID, timestamp, and entity ID).

## 4. Verification Commands (Run Before Outputting Results)
- Python Projects:
  - Linting: `ruff check . --fix`
  - Type Check: `mypy .`
  - Tests: `pytest -v`
- Node/TypeScript Projects:
  - Linting: `npx lint-staged` or `npm run lint`
  - Type Check: `npx tsc --noEmit`
  - Tests: `npm test`