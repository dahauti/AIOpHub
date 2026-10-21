Markdown
# SPEC_TASKS.md: Module 2 - Bi-directional BC Service Order ⇄ Frontu Task Sync

## 1. System Context & Architecture Principles
Module 2 synchronizes Service Orders and field execution between Microsoft Business Central (OData v4) and Frontu FSM API v2:
1. **Outbound Dispatch (BC -> Frontu)**: Triggered by BC `DocumentStatus eq 'FRONTU'`. Pushes SO Header and unlinked `TG_Lines` (where `Frontu_Integration_Line_GUID` is empty). Sets BC status to `TASKERYJE` and stores Frontu `guid`/`code`.
2. **Outbound Incremental Lines (BC -> Frontu Task Update)**: For existing orders, only `TG_Lines` lacking `Frontu_Integration_Line_GUID` are pushed as new Materials/Works. Existing lines with a GUID are immutable and never updated.
3. **Inbound Execution (Frontu -> BC)**:
   - Synchronizes Task status transitions using the numeric code mapping defined in BC's `Document Status List`. Respects `No Frontu Updates` and `No Frontu Line Updates` guardrails.
   - Pushes actual registered labor (`works`) and materials into BC `TG_Lines`.
4. **Frontu-Originated Creation**: Tasks created in Frontu without `systemId` trigger `POST /ServiceOrders` in BC. The generated `SOxxxxx` is written back to Frontu `systemId`. The BC order is initialized with the status mapped from Frontu (never `FRONTU`).
5. **95/5 Unmapped Fallback**: Materials/works not matched to BC catalog items are inserted as `Type = ' '` (Comment lines) with descriptive headers to avoid accounting corruption.
6. **Cursor Overlap & Domain Separation**: Operative Data (Tasks) and Master Data use isolated watermark streams in SQLite with a configurable sliding safety buffer (default: 15 minutes) to prevent missed inflight transactions.

---

## 2. Technical Stack
- **Runtime**: Python 3.12+ (`mypy --strict`)
- **HTTP Client**: `httpx` with `tenacity` retries (429, 502, 503, 504)
- **Data Validation**: `pydantic` v2
- **State Store**: SQLite (`data/aio_state.db`) for idempotency, GUID registries, and stream watermarks
- **Execution Mode**: Decoupled Headless CLI (`python -m src.cli tasks --sync`) + HTMX Cockpit telemetry

---

## 3. Data Contracts & State Mapping

### 3.1 Status Mapping (Frontu Numeric Code -> BC DocumentStatus)
Derived directly from BC `Document Status List`:

| Frontu Status Code | Target BC `DocumentStatus` | Description | Line Updates Allowed |
|---|---|---|---|
| `100` | `INACTIVE` | Neaktyvus | No (`No Frontu Line Updates` checked) |
| `101` | `SYNCED` | Laikinas | Yes |
| `102` | `UNASSGINED` | Nepriskirtas | Yes |
| `110` | `REOPEN` | Atidarytas | Yes |
| `200` | `IN PROGRESS` | Vykdoma | No (`No Frontu Line Updates` checked) |
| `300` | `TASKER STOPPED` | Sustabdyta Taskeryje | No (`No Frontu Line Updates` checked) |
| `400` | `DONE` | Įvykdyta | No (`No Frontu Line Updates` checked) |
| `401` | `DONE WITH NOTE`| Įvykdyta su pastaba | No (`No Frontu Line Updates` checked) |
| `402` | `APPROVED` | Patvirtinta | Frozen (`No Frontu Updates` checked) |
| `500` | `TASKER UNSIGN` | Nepasirašyta Taskeryje | No (`No Frontu Line Updates` checked) |
| `600` | `ABORTED` | Atšaukta | Frozen (`No Frontu Updates` checked) |
| *Fallback* | `TASKERYJE` | Sukurta užduotis | Yes |

### 3.2 Outbound Line Push Filter
When pushing lines for an existing SO to Frontu:
```python
lines_to_push = [
    line for line in bc_lines
    if not line.Frontu_Integration_Line_GUID or line.Frontu_Integration_Line_GUID.strip() == ""
]
4. SQLite Storage Schema (data/aio_state.db)
SQL
CREATE TABLE IF NOT EXISTS task_sync_state (
    service_order_no TEXT PRIMARY KEY,
    frontu_task_guid TEXT UNIQUE NOT NULL,
    frontu_task_code TEXT,
    header_hash TEXT NOT NULL,
    origin_system TEXT CHECK(origin_system IN ('BC', 'FRONTU')) NOT NULL,
    sync_status TEXT CHECK(sync_status IN ('SYNCED', 'PENDING', 'FAILED', 'VALIDATION_ERROR')),
    error_message TEXT,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS task_line_sync_state (
    service_order_no TEXT NOT NULL,
    line_no INTEGER NOT NULL,
    frontu_entity_guid TEXT NOT NULL,
    entity_type TEXT CHECK(entity_type IN ('MATERIAL', 'WORK', 'COMMENT')),
    line_hash TEXT NOT NULL,
    sync_status TEXT CHECK(sync_status IN ('SYNCED', 'FAILED')),
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (service_order_no, line_no, entity_type)
);

CREATE TABLE IF NOT EXISTS sync_watermarks (
    stream_name TEXT PRIMARY KEY, -- 'operative_tasks_bc', 'operative_tasks_frontu', 'master_vehicles', etc.
    watermark_value TEXT NOT NULL,
    safety_buffer_seconds INTEGER DEFAULT 900, -- 15 mins default for operative streams
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
5. File Structure (<200 LOC per file)
src/
├── core/
│   ├── config.py              # Pydantic v2 settings (lookback buffers, tokens, endpoints)
│   ├── auth.py                # Entra ID token cache with auto-refresh
│   └── retry.py               # Tenacity retries
├── domain/
│   ├── bc_models.py           # Pydantic models for BC ServiceOrders & TG_Lines
│   ├── frontu_models.py       # Pydantic models for Frontu Tasks, Materials, Works
│   └── mapping.py             # BC Document Status mapping, comment line fallback, SHA-256
├── clients/
│   ├── bc_tasks_client.py     # OData v4 client for ServiceOrders and TG_Lines
│   └── frontu_tasks_client.py # Frontu v2 tasks, materials, works client
├── services/
│   ├── task_outbound.py       # BC -> Frontu (Order push & unlinked line sync)
│   ├── task_inbound.py        # Frontu -> BC (Status sync, labor/material ingest, Frontu-born SO)
│   └── line_reconciler.py     # Line diffing: existing GUID bypass & comment line builder
└── storage/
    └── task_repository.py     # Isolated streams, watermarks with safety buffer, state CRUD

---

### Turnkey Claude Prompt (Step 1: Domain, Watermarks & Mapping Engine)

Nukopijuok šį chirurginį promptą tiesiai į Claude Code:

```text
Act as Principal Systems Architect. Execute Module 2 Step 1 for AIOpHUB in C:\AIOpHub strictly following SPEC_TASKS.md.

Strict Execution Rules:
1. Every modified or created file MUST remain strictly under 200 LOC.
2. 100% strict mypy compliance (no untyped dicts, complete Pydantic v2 validation).
3. Do not explain changes or write summaries. Execute tests and report only test status and modified paths.

Tasks:
1. Update `src/domain/bc_models.py`:
   - `BCServiceOrderHeader`: Add all OData fields (No, SelltoCustomerNo, VehicleSerialNo, DealType, DocumentStatus, Tasker_ID, Tasker_Document_No, Last_Tasker_Sinhr_Time, PlannedServiceDate, OrderTime, Description, WorkDescription, SystemModifiedAt).
   - `BCServiceOrderLine`: Add all line fields (DocumentNo, LineNo, Type, No, Description, Description_2, Quantity, UnitofMeasureCode, UnitPrice, LineAmount, LocationCode, Service_Order_Job, Frontu_Integration_Line_GUID).
2. Update `src/domain/frontu_models.py`:
   - `FrontuTask`: Fields (guid, code, systemId, title, description, plannedTime, status, statusLiteral, client, workspace, taskType, user).
   - `FrontuMaterial` and `FrontuWork`.
3. Update `src/domain/mapping.py`:
   - Map numeric Frontu status codes (100, 101, 102, 110, 200, 300, 400, 401, 402, 500, 600) to BC DocumentStatus codes strictly per SPEC_TASKS.md section 3.1.
   - Implement `is_line_update_allowed(status_code: int) -> bool` honoring BC's "No Frontu Line Updates" flag.
   - Implement `filter_unlinked_bc_lines(lines: list[BCServiceOrderLine]) -> list[BCServiceOrderLine]` (skips lines where Frontu_Integration_Line_GUID is present).
   - Implement `build_unmapped_comment_line(...)` returning BCServiceOrderLine with Type=' ' and descriptive note.
   - Implement deterministic SHA-256 hashing functions.
4. Update `src/storage/task_repository.py` (or create if missing):
   - Implement SQLite migrations for `task_sync_state`, `task_line_sync_state`, and `sync_watermarks`.
   - Implement watermark retrieval with configurable `safety_buffer_seconds` subtracted from the stored timestamp.
5. Create unit tests in `tests/test_task_domain.py` and `tests/test_task_repository.py` validating:
   - Status mapping and `is_line_update_allowed` checks.
   - Line filtering (skipping lines with GUID).
   - Watermark calculation with safety buffer.
   - Unmapped item comment fallback.
6. Verify:
   pytest tests/test_task_domain.py tests/test_task_repository.py && mypy --strict src/domai
