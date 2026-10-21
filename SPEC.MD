# SPECIFICATION: BC ⇄ Frontu Vehicle to Workspace Sync Engine

## 1. System Context & Architecture
- Problem Statement: Synchronize custom Vehicle master data from Microsoft Business Central (On-Prem transitioning to SaaS) to Frontu FSM API v2 Workspaces.
- Source of Truth: BC OData V4 page VehicleCard with optional TG_ShipToAdress address resolution.
- Target: Frontu FSM API v2 /workspaces (type: "object", keyed by systemId).
- Core Strategy: High-water-mark cursor (SystemModifiedAt ge {watermark}) with 15-minute lookback + SHA-256 payload fingerprinting to bypass unmutated records.

## 2. Tech Stack & Infrastructure
- Runtime: Python 3.12+ (isolated in C:\AIOpHub\.venv).
- Frameworks: FastAPI, SQLAlchemy 2.1 (async), aiosqlite, Pydantic v2, HTTPX.
- Fault Tolerance: Tenacity. Retries target HTTP 429, 502, 503, and 504 with exponential backoff and jitter. Client-side token bucket (150 req/min, 0.5s pace for Frontu).
- Database: Embedded SQLite in WAL mode (`sqlite+aiosqlite:///./data/sync.db`), flat namespace (no schema prefix).

## 3. Directory Layout
C:\AIOpHub\
|-- .env.example
|-- .gitignore
|-- CLAUDE.md
|-- SPEC.md
|-- requirements.txt
|-- src/
|   |-- __init__.py
|   |-- config.py
|   |-- database.py
|   |-- main.py
|   |-- models/
|   |   |-- __init__.py
|   |   +-- audit.py
|   |-- schemas/
|   |   |-- __init__.py
|   |   |-- bc_vehicle.py
|   |   +-- frontu_models.py
|   |-- adapters/
|   |   |-- __init__.py
|   |   |-- bc_client.py
|   |   +-- frontu_client.py
|   |-- api/
|   |   |-- __init__.py
|   |   |-- config_handlers.py
|   |   +-- routes_ui.py
|   |-- pipelines/
|   |   |-- __init__.py
|   |   +-- sync_vehicles.py
|   |-- repositories/
|   |   |-- __init__.py
|   |   |-- config_repository.py
|   |   +-- sync_repository.py
|   +-- templates/
|       |-- base.html
|       |-- dashboard.html
|       +-- partials/
|           +-- status.html
+-- tests/
    |-- __init__.py
    |-- conftest.py
    +-- test_sync_vehicles.py

## 4. Database Schema (embedded SQLite)
- Engine: `sqlite+aiosqlite:///./data/sync.db` (path relative to the working directory; `./data` is created on first use).
- Every connection sets `PRAGMA journal_mode=WAL` and `PRAGMA foreign_keys=ON`.
- Flat namespace: no `sync_engine` schema prefix. Tables are created idempotently by `python -m src.database` (`Base.metadata.create_all`).
- Timestamps are stored as UTC text and read back as timezone-aware UTC (`UtcDateTime` column type).

CREATE TABLE IF NOT EXISTS sync_states (
    module_name VARCHAR(64) NOT NULL PRIMARY KEY,
    cursor_timestamp DATETIME NOT NULL,
    status VARCHAR(32) NOT NULL DEFAULT 'IDLE',
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS sync_config (
    module_name VARCHAR(64) NOT NULL PRIMARY KEY,
    dry_run BOOLEAN NOT NULL DEFAULT 1,
    auto_sync_enabled BOOLEAN NOT NULL DEFAULT 0,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS sync_fingerprints (
    module_name VARCHAR(64) NOT NULL,
    entity_key VARCHAR(128) NOT NULL,
    payload_hash VARCHAR(64) NOT NULL,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (module_name, entity_key)
);

CREATE TABLE IF NOT EXISTS sync_audit_log (
    id INTEGER PRIMARY KEY,  -- rowid alias; auto-increments (BIGINT would not on SQLite)
    entity_key VARCHAR(128) NOT NULL,
    action VARCHAR(32) NOT NULL,
    status_code INTEGER,
    payload JSON,
    error_message TEXT,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_audit_entity ON sync_audit_log(entity_key);

## 5. Domain Schemas & Contracts

### 5.1 BC Raw Schemas (src/schemas/bc_vehicle.py)
BCODataVehicle fields:
- SerialNo: str (required, min_length=1)
- VIN: Optional[str] = None
- MakeCode: Optional[str] = None
- ModelCode: Optional[str] = None
- RegistrationNo: Optional[str] = None
- CustomerNo: Optional[str] = None
- CustomerServiceAddressCode: Optional[str] = None
- Blocked: bool = False
- SystemModifiedAt: datetime (required)
- LastDateModified: Optional[date] = None

BCODataShipToAddress fields:
- Customer_No: str (required)
- Code: str (required)
- Name: Optional[str] = None
- Address: Optional[str] = None
- City: Optional[str] = None
- Post_Code: Optional[str] = None

### 5.2 Frontu Schemas (src/schemas/frontu_models.py)
FrontuWorkspaceUpsert fields:
- systemId: str (BC SerialNo)
- title: str (Constructed: MakeCode + " " + ModelCode, fallback to SerialNo)
- client: str (BC CustomerNo, fallback to internal_company_client_id)
- type: str = "object"
- address: Optional[str] = None
- city: Optional[str] = None
- zipCode: Optional[str] = None
- serialNumber: Optional[str] = None (BC VIN)
- customValues: Optional[dict[str, str]] = None
- deleted: bool = False (Mirrors BC Blocked)

## 6. Adapter Protocols & Throttling Guardrails

### BC Adapter (src/adapters/bc_client.py):
- Endpoints:
  - Vehicles: {base_url}/Company('{company}')/VehicleCard
  - Ship-To: {base_url}/Company('{company}')/TG_ShipToAdress
- Pagination: $top=500&$skip={n}&$orderby=SystemModifiedAt asc,SerialNo asc
- Delta Filter: $filter=SystemModifiedAt ge {cursor - 15min}
- Auth Modes:
  - onprem: HTTP Basic (BC_USER : BC_WEB_SERVICE_ACCESS_KEY)
  - saas: Entra ID Client Credentials with in-memory token caching (refresh at expires_at - 300s)
- Retries: Retries target HTTP 429, 502, 503, and 504 with exponential backoff and jitter.

### Frontu Adapter (src/adapters/frontu_client.py):
- Base URL: https://{subdomain}.frontu.com/client/api/v2
- Auth: Header Authorization: Bearer {token}
- Dynamic Custom Fields Resolution:
  - GET /customfields/workspaces on initialization.
  - Resolve GUID for title "Registracijos Nr." (case-insensitive).
  - Resolve GUID for title "Padalinio pavadinimas" (case-insensitive).
- Throttling & Pacing:
  - Max 150 requests per minute.
  - Mandatory minimum 0.5s pause between network calls.
- Target: POST /workspaces (Upserts matched by systemId).

## 7. Pipeline Execution Logic (src/pipelines/sync_vehicles.py)
1. Acquire module execution lock for workspaces_vehicles.
2. Read cursor from sync_states (fallback on a clean database: NOW() - SYNC_INITIAL_LOOKBACK_DAYS, default 0 = NOW()).
   Run mode (DRY RUN / LIVE) is read from sync_config at the start of each run (section 11).
3. Compute fetch start window: cursor - 15 minutes.
4. Read batch from BC VehicleCard ($top=500).
5. For vehicles having CustomerNo + CustomerServiceAddressCode, resolve address via TG_ShipToAdress lookup.
6. For each record:
   - Construct FrontuWorkspaceUpsert.
   - Compute payload_hash = SHA256(json_dump_without_deleted).
   - Check sync_fingerprints: if hash matches and not Blocked, SKIP.
   - If Blocked == True: POST {"deleted": true} to Frontu.
   - If Active: POST payload to Frontu /workspaces.
   - Save fingerprint on HTTP 200/201.
   - Record transaction to sync_audit_log.
7. Advance cursor_timestamp to latest batch SystemModifiedAt.
8. Release module lock.

## 8. Hard Constraints for Claude
- Zero untyped dictionaries; all transformations must use Pydantic models.
- Maximum 200 LOC per file. Split logic across layers cleanly.
- Strict Mypy compliance (no Any, no ignore flags).
- No direct external network calls outside src/adapters/.

## 9. Feature: Warranty Data Enrichment (VehicleWarrList -> Frontu Workspace)

### 9.1 BC OData Source Contract
- Endpoint: `VehicleWarrList`
- Query Path: `.../Company('{company}')/VehicleWarrList?$filter=VehicleSerialNo eq '{escaped_serial_no}' and Status eq 'Active'`
- Raw Fields to Ingest:
  - `VehicleSerialNo`: str (matched with vehicle `SerialNo`)
  - `WarrantyTypeCode`: str (e.g., `STANDART_FACTORY`, `EXTENDED_FACTORY`, `NOWARRANTY`)
  - `StartingDate`: str (date format `YYYY-MM-DD`, ignore/nullify if `"0001-01-01"`)
  - `TermDateFormula`: str (BC date formula string, e.g., `"1Y"`, `"3M"`, `"6M"`, `"0Y"`)
  - `VariableFieldRun1`: int | float (operating indicator limit, e.g., moto-hours/mileage)
  - `Status`: str (`"Active"`)

### 9.2 Parsing & Date Formula Resolution
- Date Formulas parser:
  - Supports format `r"^(\d+)([YMD])$"` (Years, Months, Days).
  - Computation: `target_date = starting_date + relativedelta(years=N / months=N / days=N)`.
  - Format output strictly as `YYYY-MM-DD`.

### 9.3 Mapping & Hierarchy Rules
- If multiple warranty records exist for a single `VehicleSerialNo`:
  1. `warrantyStartDate`: Earliest valid `StartingDate` where `StartingDate != "0001-01-01"`.
  2. `warrantyEndDate` (Standard): Compute from record where `WarrantyTypeCode` is NOT `NOWARRANTY` and does NOT start with `EXTENDED_`.
  3. `extendedWarrantyEndDate` (Extended): Compute from record where `WarrantyTypeCode` starts with `EXTENDED_`.
  4. `warrantyIndicatorLimit`: Highest positive value from `VariableFieldRun1` across active non-`NOWARRANTY` records (cast to `int`). If 0, pass `None`.
- Frontu Payload Fields (Direct top-level attributes in `FrontuWorkspaceUpsert`):
  - `warrantyStartDate`: Optional[str] = None
  - `warrantyEndDate`: Optional[str] = None
  - `extendedWarrantyEndDate`: Optional[str] = None
  - `warrantyIndicatorLimit`: Optional[int] = None

## 10. Legacy Frontu Workspaces (Operational Remediation)

### 10.1 Problem
Frontu holds about 2,000 workspaces created manually or by a legacy import: `serialNumber` is set but `systemId` is `null`/blank. The `POST /workspaces` upsert matches only by `systemId`, so syncing such a vehicle creates a second workspace.

### 10.2 Decision (2026-10-20)
- The sync pipelines do NOT resolve identities at runtime: every active vehicle is written with `POST /workspaces` (Frontu updates by `systemId` when it exists, otherwise inserts).
- Frontu v2 offers no way to set `systemId` on an existing workspace (`OPTIONS /workspaces` -> `Allow: POST, GET`; `GET`/`OPTIONS`/`PUT` on `/workspaces/{guid}` return an HTML 404), so adoption is an operations task in the Frontu UI.
- Required order: operations set `systemId = BC.SerialNo` on every workspace in `legacy_adoption_candidates.csv` BEFORE the first live sync touches that vehicle; otherwise a duplicate is created.

### 10.3 Operations Reports (scripts/, read-only, output in data/reports/)
- Listing source: `GET /workspaces?type=object&pageSize=100&page={n}` (`FrontuClient.iter_workspace_pages`), envelope `{"items": [...], "next_page": n}`; rows identify workspaces by `guid` (`id` as fallback). Paging stops on a short page or `total`; more than 1000 pages raises.
- Matching (src/pipelines/identity_resolver.py, report use only): managed = non-blank `systemId`; unmanaged objects keyed by `serialNumber.strip().upper()`; per active vehicle the VIN key is tried first, then SerialNo. Exactly one unmanaged match -> LEGACY_MATCH; several -> AMBIGUOUS; a vehicle already managed or blocked is never matched.
- `python -m scripts.export_legacy_candidates` -> `legacy_adoption_candidates.csv`: active BC vehicle, matched legacy guid, matched key and the action (set `systemId`).
- `python -m scripts.export_ambiguous_frontu` -> `frontu_ambiguous_duplicates.csv`: unmanaged serial keys shared by several workspaces (merge candidates).

## 11. Runtime Dashboard Controls (2026-10-21)

### 11.1 Runtime Configuration (`sync_config`)
- One row per module (`module_name` PK). Strongly typed columns (DTO: `RuntimeConfig` extends `ScheduleSettings`, src/schemas/schedule.py); never serialized label strings:

| Column | Type | Default | Constraint |
|---|---|---|---|
| `dry_run` | BOOLEAN | true | DRY RUN / LIVE run mode |
| `auto_sync_enabled` | BOOLEAN | false | master switch for all scheduled runs |
| `schedule_mode` | VARCHAR(16) | `nightly` | `nightly` \| `interval` |
| `nightly_time` | VARCHAR(5) | `02:00` | `HH:MM`, 00:00-23:59 |
| `interval_minutes` | INTEGER | 60 | 5-1440 |
| `full_sync_enabled` | BOOLEAN | false | weekly full sync on/off |
| `full_sync_weekday` | INTEGER | 6 | 0 = Monday ... 6 = Sunday |
| `full_sync_time` | VARCHAR(5) | `02:00` | `HH:MM`, 00:00-23:59 |

- Precedence: a stored row wins over `.env`. Without a row, `SYNC_DRY_RUN` / `SYNC_ENABLED` and the schedule defaults above apply; the first dashboard write seeds the row from them.
- The batch pipeline, the single-vehicle sync (unless `force_live`) and the CLI batch read `dry_run` at the start of each run.
- The app lifespan runs `init_db()`: missing tables are created and model columns missing from existing tables are added (`ALTER TABLE ... ADD COLUMN` with the server default), so deployed databases upgrade without migrations.

### 11.1a Scheduler Execution Semantics (src/pipelines/schedule.py, src/pipelines/scheduler.py)
- The web app always starts the daemon loop; every `SYNC_INTERVAL_SECONDS` tick (schedule check granularity) reads `sync_config` and starts at most one due run.
- Schedule times are interpreted in the server's local time zone (DST-aware).
- Incremental slots: `nightly` -> next `nightly_time` strictly after now; `interval` -> `interval_minutes` after the last scheduled start.
- Startup sync suppression: the daemon never runs on startup; the first tick only plans the next slots from the current time.
- Schedule edits re-plan from the current time; a slot that an edit moves into the past never fires.
- Missed slot drops on re-enable: while `auto_sync_enabled` is false the plan is discarded; re-enabling re-plans from that moment, so slots missed while disabled are dropped, not caught up.
- A due run that finds another run active stays due and starts on the first tick after it finishes.
- Weekly full sync (`full_sync_enabled`): due at `full_sync_weekday` + `full_sync_time`. It supersedes an incremental run due on the same tick and also re-plans the incremental slot.
- Full sync behavior: the cursor window is bypassed (`SystemModifiedAt ge 1753-01-01`, BC's minimum date, i.e. all records), but fingerprint filtering stays active: only vehicles with a payload delta or without a stored fingerprint (never written with their `systemId`) are dispatched to Frontu. The cursor stays forward-only, and dry-run rules (section 7 / 11.1) apply unchanged.
- `POST /partials/config/schedule` (src/api/schedule_handlers.py) validates and stores the whole schedule (an unchecked full-sync switch = off) and returns the `#schedule-card` partial; 422 with the first validation error and nothing stored.

### 11.2 Endpoints (src/api/config_handlers.py; each returns the `#sync-status` partial)
- `POST /partials/config/toggle-dry-run`: flips DRY RUN / LIVE. 409 while a run is active (the mode of a running sync cannot change).
- `POST /partials/config/toggle-auto-sync`: flips auto-sync (allowed while running; affects the next tick).
- `POST /partials/sync/abort`: when a run is active in-process or `sync_states.status = RUNNING`, cancels the run task, releases `sync_states` to `IDLE` and writes an `ABORTED` row to `sync_audit_log` (`entity_key` = module name). 409 when nothing is running.
- Fingerprints and the cursor are only written per confirmed record or page, so an aborted run is resumed safely by the next run.

### 11.3 Dashboard
- Mode card: amber `DRY RUN` / emerald `LIVE` toggle (switching to LIVE asks for confirmation; disabled while running) and an `Auto-sync ON/OFF` toggle.
- Action bar (inside the polled KPI block): `Sync now` while idle; a red `Abort Sync` (with confirmation) while `RUNNING`.
- Schedule card (outside the poll loop): incremental mode selector (nightly time / interval minutes), weekly full sync switch with weekday and time selectors, `Save schedule`; shows `paused while Auto-sync is OFF`.
