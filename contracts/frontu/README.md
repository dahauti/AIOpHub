# Frontu contract samples

Recorded real responses (body only; no request headers or tokens). One line per file: endpoint, capture date, profile.

- `task_types_details_SERVICE.json` — `GET /client/api/v2/task_types/details?systemId=SERVICE`, HTTP 200 `application/json`, 2026-10-22, profile `onprem_dev` (Frontu `alwarktest.frontu.com`). Body unchanged: no secrets found (`src/services/redaction.py`).
- `task_types_details_not_found.json` — `GET /client/api/v2/task_types/details?systemId=NONEXISTENT_TEST`, HTTP 404 `application/json`, 2026-10-22, profile `onprem_dev` (Frontu `alwarktest.frontu.com`). Body unchanged: no secrets found (`src/services/redaction.py`).
- `clients_2216.json` — `GET /client/api/v2/clients?systemId=2216`, HTTP 200 `application/json`, 2026-10-23, profile `onprem_dev` (Frontu `alwarktest.frontu.com`). Paged listing `{next_page, items[]}`, one row. Sanitized: contact/address/company-code string values replaced with `<key>` placeholders; keys, types, nulls, `guid`, `systemId`, dates unchanged.
- `clients_not_found.json` — `GET /client/api/v2/clients?systemId=NONEXISTENT_TEST`, HTTP 200 `application/json`, 2026-10-23, profile `onprem_dev` (Frontu `alwarktest.frontu.com`). Not-found is NOT an error: empty listing `{"next_page":0,"items":[]}`. Body unchanged.
- `workspaces_30971.json` — `GET /client/api/v2/workspaces?systemId=30971`, HTTP 200 `application/json`, 2026-10-23, profile `onprem_dev` (Frontu `alwarktest.frontu.com`). Paged listing `{next_page, items[]}`, one row (`type` `object`). Sanitized as `clients_2216.json` (also coordinates, `signatureCode`, `objectCode`).
- `workspaces_not_found.json` — `GET /client/api/v2/workspaces?systemId=NONEXISTENT_TEST`, HTTP 200 `application/json`, 2026-10-23, profile `onprem_dev` (Frontu `alwarktest.frontu.com`). Not-found is NOT an error: empty listing `{"next_page":0,"items":[]}`. Body unchanged.

Not-found summary: `task_types/details` -> HTTP 404 `{"error": "Entity Not Found!"}` (no systemId in the body); `clients`, `workspaces` -> HTTP 200 with empty `items`.
