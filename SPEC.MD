# SPECIFICATION: BC ⇄ Frontu Vehicle to Workspace Sync Engine

## 1. System Context & Architecture
- Problem Statement: Synchronize custom Vehicle master data from Microsoft Business Central (On-Prem transitioning to SaaS) to Frontu FSM API v2 Workspaces.
- Source of Truth: BC OData V4 page VehicleCard with optional TG_ShipToAdress address resolution.
- Target: Frontu FSM API v2 /workspaces (type: "object", keyed by systemId).
- Core Strategy: High-water-mark cursor (SystemModifiedAt ge {watermark}) with 15-minute lookback + SHA-256 payload fingerprinting to bypass unmutated records.

## 2. Tech Stack & Infrastructure
- Runtime: Python 3.12+ (isolated in C:\AIOpHub\.venv).
- Frameworks: FastAPI, SQLAlchemy 2.0 (async), asyncpg, Pydantic v2, HTTPX.
- Fault Tolerance: Tenacity (backoff on 429/503), client-side token bucket (150 req/min, 0.5s pace for Frontu).
- Database: PostgreSQL (schema sync_engine).

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
|   +-- pipelines/
|       |-- __init__.py
|       +-- sync_vehicles.py
+-- tests/
    |-- __init__.py
    |-- conftest.py
    +-- test_sync_vehicles.py

## 4. Database Schema (PostgreSQL sync_engine)

CREATE SCHEMA IF NOT EXISTS sync_engine;

CREATE TABLE IF NOT EXISTS sync_engine.sync_states (
    module_name VARCHAR(64) PRIMARY KEY,
    cursor_timestamp TIMESTAMPTZ NOT NULL,
    status VARCHAR(32) NOT NULL DEFAULT 'IDLE',
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS sync_engine.sync_fingerprints (
    module_name VARCHAR(64) NOT NULL,
    entity_key VARCHAR(128) NOT NULL,
    payload_hash VARCHAR(64) NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (module_name, entity_key)
);

CREATE TABLE IF NOT EXISTS sync_engine.sync_audit_log (
    id BIGSERIAL PRIMARY KEY,
    entity_key VARCHAR(128) NOT NULL,
    action VARCHAR(32) NOT NULL,
    status_code INT,
    payload JSONB,
    error_message TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_audit_entity ON sync_engine.sync_audit_log(entity_key);

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
- Retries: Tenacity on HTTP 429 and 503 only.

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
2. Read cursor from sync_engine.sync_states (fallback: NOW() - 30 days).
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
