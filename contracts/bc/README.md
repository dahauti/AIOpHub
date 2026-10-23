# BC contract samples

Recorded real responses (body only; no request headers or tokens). One line per file: endpoint, capture date, profile.

- `tg_docstatuslist.json` — `GET .../ODataV4/Company('<DEV company>')/TG_DocStatusList` (no filter), HTTP 200 `application/json; odata.metadata=minimal`, 2026-10-23, profile `onprem_dev` (company `Copy LT 20241223`). 63 rows, one page (no `@odata.nextLink`). Sanitized: server host removed from `@odata.context`; rows unchanged (status codes and descriptions only; no secrets found by `src/services/redaction.py`). Dev and Prod tables may differ.
