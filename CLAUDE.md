# CLAUDE.md — AIOpHub (Alwark Operation Hub, fresh build)

Binding for every AI-assisted change in this repository. If a request conflicts with this file, STOP and report the conflict. Do not resolve it yourself.

---

## 1. Operating Model & Zero-Vibe Execution
- Role: Mid-level Software Engineer under a Principal Systems Architect. The owner reviews outcomes, not code; every change must be small, verifiable and explainable in 5 lines.
- Spec-Driven: never invent architecture, endpoints, schemas or payloads. Execute strictly against `SPEC.md` and `/contracts`.
- Plan-First: any change touching >1 file -> a 3-step plan, then wait for explicit approval.
- Test-First: scaffold tests with fixtures from `/contracts` before implementing services.
- Scope Containment: no refactors of adjacent code, no rewrites of working logic, no unrequested features.
- Migration step: every task states which step of the migration lifecycle (section 9) it belongs to.
- UI changes (`src/templates/`, `src/static/`): read `docs/UX_GUIDELINES.md` before planning.

## 2. Product Context
- AIOpHub is the replacement for the legacy Operation Hub (`AlwarkOperationHub_Dev` / `AlwarkOperationHub`). One product, one visual system, UI: LT numatyta, daugiakalbis (ADR 0002).
- Two processes: `hub` (FastAPI web UI) and `worker` (scheduled and on-demand jobs). Both use PostgreSQL 14.
- Current state: SQLite (`sqlite+aiosqlite`, `ENV_DATABASE_URLS` in `src/config.py`, engine in `src/database.py`). Target: PostgreSQL 14. Migration is its own approved task.
- First module: Frontu (FSM) <-> Business Central integration.
- BC targets: on-prem (OData V4, separate companies for Prod/Dev) now; BC SaaS (Entra ID OAuth2, environment Test, later Production) after migration. The same code serves both via configuration only.

## 3. Layout & Separation of Concerns
```
src/
  hub/          # FastAPI routes (HTTP transport only), templates, static, auth, permissions
  services/     # business logic and sync decisions
  adapters/
    frontu/     # Frontu client + Pydantic models
    bc/         # BcClient protocol + onprem.py + saas.py
  repositories/ # SQL access only
  worker/       # scheduler, job runner, job implementations
  config/       # pydantic-settings, environment profiles
db/migrations/  # numbered SQL: 0001_xxx.sql, 0002_xxx.sql ...
contracts/      # recorded, sanitized real API samples (JSON) per endpoint
tests/
docs/decisions/ # one file per architecture decision
docs/migration/ # parity checklist per legacy feature
```
Target layout. Current code lives in src/api/, src/templates/, src/static/, src/config.py. New modules follow the target layout; moving existing code only as its own approved task.
- Routes never call adapters directly. External HTTP lives only in `adapters/`.
- The UI reads PostgreSQL only. Any external read that can exceed 2 s runs in `worker`.
- Files <= 200 LOC, functions <= 50 LOC.

## 4. Hard Technical Constraints
- Dependencies: only what is in `requirements.txt` (pinned) plus the standard library. No `pip install` / `npm install` without approval.
- Config & secrets: `pydantic-settings` reading `.env`. No URLs, tenants, companies, keys or connection strings in code.
- Types: Pydantic v2 for all external payloads and internal DTOs. No untyped `dict`, no `Any`. `mypy --strict` on `src/`.
- No bare `except`. Map technical exceptions to domain errors; structured logging with trace ID, timestamp, entity ID. Never log secrets or auth headers.
- CDN assets are version-pinned.

## 5. Integration Protocols (Frontu, BC on-prem/SaaS, Pipedrive, MS Graph)
- Zero speculation: a field or endpoint may be used only if it exists in `/contracts` or in official docs referenced in an ADR. Unconfirmed -> STOP and name the sample needed (endpoint + filter).
- Environment profiles: `onprem_dev`, `onprem_prod`, `saas_test`, `saas_prod`. Every job runs with an explicit profile.
- Read-only by default. Write methods are named explicitly (`create_*`, `update_*`), require `allow_writes=true` for the profile, and for `*_prod` additionally `ALLOW_PROD_WRITES=true`. No test or canary writes against production. Ever.
- Retries: `tenacity` with exponential backoff and jitter on 429/502/503/504 and network errors; respect `Retry-After`.
  - Reads: retry freely.
  - Writes: retry ONLY if the operation is idempotent or the adapter first checks whether the previous attempt already succeeded (lookup by external key). A timed-out POST may have succeeded.
- Tokens: cached in memory, refreshed 5 minutes before expiry; never re-authenticate per request.
- Defensive parsing: models use `ConfigDict(extra="ignore")`, AND the adapter logs (once per run, keys only, no values) any unknown top-level fields so vendor drift is visible.
- Pagination is explicit; never assume one page.
- Inbound webhooks (only when confirmed in SPEC.md): validate, persist raw event to a staging table, return 200/202, process asynchronously in `worker`.
- Idempotency keys: `SHA256(source_system + entity_id + source_version)` where `source_version` comes from the source (version number, ETag or source `updated_at`). Never use local receive time.

## 6. Data & Sync Rules
- Mapping table (`source_system`, `source_id`, `target_system`, `target_id`, `object_type`, `profile`) is the single source of linkage.
- Change detection via payload hash + source version; never permanently skip already-matched records.
- 95/5 rule: edge cases under 5% of records are not handled in the sync path. They go to an exception table with CSV export for human remediation.
- Every job run writes `job_runs` (start, end, status, counts, error summary). Manual triggers go through `job_requests`.
- Schema changes only via a new numbered file in `db/migrations/`, including GRANTs for app roles. No ad hoc DDL.

## 7. Verification (run before reporting)
- `ruff check .` (no `--fix` in the gate; fixes are applied as a separate, visible step)
- `ruff format --check .`
- `mypy --strict src`
- `pytest -q` (no network access in tests)
- Per sync job, tests must cover: happy path, idempotency (run twice), one failure path (API error -> job_run `failed`, no partial corruption).

## 8. Reporting Format (nothing else)
```
STEP: <migration lifecycle step, section 9>
FILES: <modified/created paths>
CHECKS: ruff=<ok|fail> mypy=<ok|fail> pytest=<passed/failed>
INTENT: <max 2 lines: what behavior changed>
RISKS/OPEN: <max 3 bullets or "none">
DEVIATIONS: <every departure from the plan or from this file, or "none">
```
No code walkthroughs, no restating the diff.

## 9. Legacy Migration (Strangler, Parallel Run)
- Legacy (`AlwarkOperationHub_Dev`, `AlwarkOperationHub`, `frontu_bc_audit.py`) is in production use and lives OUTSIDE this repo. Claude never reads or modifies it from here. The owner supplies behavior descriptions and sample outputs.
- AIOpHub never writes to legacy-owned tables. New tables come from `db/migrations/` only.
- Lifecycle per feature, one feature at a time:
  1. Inventory — `docs/migration/<feature>.md`: what users actually do (inputs, outputs, e-mails, exports, permissions).
  2. Build — in AIOpHub, behind permissions, visible to the owner only.
  3. Shadow run — both systems on the same inputs, outputs compared; minimum 2 full business cycles.
  4. Parity gate — every checklist item verified or explained; owner sign-off in an ADR.
  5. Pilot — selected users switch; legacy stays as fallback.
  6. Cutover — all users switch; legacy feature hidden but deployable for 30 days.
  7. Retire — legacy removed in its own folder/commit, referencing the ADR.

## 10. Deployment
- Source of truth is git. The server never receives hand-copied files.
- Deploy = checkout of a tagged release + install from pinned requirements + pending migrations + service restart, via one script.
- Dev and Prod run from separate checkouts with separate `.env` files and separate ports.

## 11. Security & Access Sandbox
- Filesystem boundary: `C:\AIOpHub` only. No reading, searching or writing outside it (`..`, `C:\Users\`, `AppData`, legacy folders).
- Never open, print or output `.env`, `*.key`, `*.pem`, `*.cert`. Configuration is known via `.env.example` only.
- No outbound network calls (curl, wget, webfetch, live API calls) unless the active prompt explicitly instructs it and names the profile. Live calls only against `*_dev` / `*_test` profiles unless the owner states otherwise.
- Do not inspect or change system environment variables outside `.venv`.
- Ask before any filesystem change outside `src/`, `tests/`, `contracts/`, `db/migrations/`, `docs/`.
