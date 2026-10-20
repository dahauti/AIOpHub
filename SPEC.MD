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
|   |   +-- routes_ui.py
|   |-- pipelines/
|   |   |-- __init__.py
|   |   +-- sync_vehicles.py
|   |-- repositories/
|   |   |-- __init__.py
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
2. Read cursor from sync_states (fallback: NOW() - 30 days).
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
