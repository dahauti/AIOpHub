# Atviri sprendimai

Būsena 2026-10-23. Kontraktas: `contracts/bc/tg_docstatuslist.json` (DEV, `Copy LT 20241223`). Nieko neįgyvendinta.

## TG_DocStatusList kaip vienintelis būsenų šaltinis

### Savininko jau priimti sprendimai

1. Po Frontu rašymo BC `Document Status` = `TG_DocStatusList` eilutės `Code`, kurios „Frontu Document Status“ lygus Frontu užduoties būsenai (pvz. 102 → `UNASSIGNED`). Pakeičia fiksuotą `TASKERYJE`.
2. `TG_DocStatusList` – vienintelis šaltinis abiem kryptims: keičia kode įrašytą `STATUS_RULES` (`src/domain/mapping.py`) ir outbound, ir inbound.
3. Skaitoma lentelė tos aplinkos, prie kurios jungiasi profilis (Dev ir Prod BC lentelės gali skirtis – tai tikėtina).
4. „No Frontu Updates“ ir „No Frontu Line Updates“ imami iš tos pačios BC lentelės (ne iš kodo):
   - „No Frontu Updates“ pažymėta → inbound praleidžia užsakymą (rezultatas `skipped`, priežastis „Galutinė būsena – Frontu pakeitimai neimportuojami“);
   - „No Frontu Line Updates“ pažymėta → antraštė gali būti atnaujinta, eilutės neimportuojamos.
5. Nėra atitinkamos eilutės → `Document Status` nekeičiamas; duomenų klaida `task.status_not_mapped`, LT „Frontu būsena {kodas} neturi atitikmens BC būsenų sąraše (TG_DocStatusList).“ (+EN).
6. BC rašymai su `If-Match: *` (lygiagretūs pakeitimai gali būti perrašyti) – į `docs/TECH_BACKLOG.md`, ta pati „pakartoti, tada įspėti“ idėja; neįgyvendinti.

### Planas (punktai 1–8, pateikta savininkui 2026-10-23)

1. **BC adapter:** new `BCDocStatusRow` model (`Code`, `Frontu_Document_Status`, `No_Frontu_Updates`, `No_Frontu_Line_Updates`, `DocumentType`, `DocumentProfile`; `extra="ignore"`) parsed per `contracts/bc/tg_docstatuslist.json`. Read-only `fetch_doc_status_list()` with explicit paging, rows filtered to `Order` + `Service`.
2. **Domain:** a `StatusTable` built from those rows replaces `STATUS_RULES` / `FALLBACK_STATUS`. It's read once per run from the profile's BC company; if that read fails, the run fails before any write.
3. **Outbound:** after the Frontu write, `DocumentStatus` = the table Code for the task's Frontu status. No row → `DocumentStatus` isn't touched and the order gets the data error `task.status_not_mapped`. Write calls and their order otherwise stay the same.
4. **Inbound:** the same table both ways.
   - No Frontu Updates → `skipped`, reason "Galutinė būsena – Frontu pakeitimai neimportuojami".
   - No Frontu Line Updates → the header updates, lines aren't imported.
   - No row → `task.status_not_mapped`.
5. **Catalog:** `task.status_not_mapped` with your LT text plus EN, and a new skip code for the final-status reason (LT/EN).
6. **/centras LIVE dialog** now says "DocumentStatus pagal TG_DocStatusList (Frontu būsena → Code)", not "TASKERYJE".
7. **Tests** use the contract file as fixture: 102→UNASSIGNED, 402 inbound skip, unmapped status, the table is read once per run, a failed table read means no writes, and an idempotent second run.
8. **Point 5 (plan only):** a BC 400 whose body contains "changed in the database between initial and JIT load" gets its own code (e.g. `system.bc_record_changed`). It's re-queued via `job_requests` at +5 min and +10 min, then alerts. This waits on your answers about the alert channel and the scope.

### Atviri klausimai (neatsakyta)

1. **Kontrakto pavyzdys:** Frontu užduoties `status` `POST /tasks` atsakyme nėra `/contracts`, o plano 3 punktas nuo jo priklauso. Kurį įrašyti (onprem_dev): `POST /client/api/v2/tasks` atsakymą ar `GET /client/api/v2/tasks?guid=<SO029748 užduoties guid>`?
2. **Outbound be atitikmens:** ar vis tiek rašyti susiejimo laukus (`Tasker_ID`, `Tasker_Document_No`, `Last_Tasker_Sinhr_Time`), kad užsakymas liktų susietas su Frontu užduotimi?
3. **Lentelės filtras:** ar `ServiceOrders` atveju teisinga filtruoti `DocumentType = Order`, `DocumentProfile = Service`?
4. **Įspėjimo kanalas** (5 punktas, BC „changed between initial and JIT load“): el. paštas per MS Graph, /centras juosta ar kita?
5. **Apimtis – paleidimai:** tik canary ar ir suplanuoti paleidimai?
6. **Apimtis – operacijos:** tik skaitymai ar ir rašymai?

### Pastebėjimai iš DEV lentelės (2026-10-23)

- 100 → `INACTIVE`, 102 → `UNASSIGNED`, 300 → `STOPPED`, 400 → `IMPORT IN PROGRESS`, 401 → `IMPORT WITH NOTE`, 402 → `APPROVED` (No Frontu Updates), 500 → `UNSIGN`.
- 101, 110, 200, 600 – eilutės nėra (pagal 5 sprendimą būtų `task.status_not_mapped`).
- „No Frontu Line Updates“ nepažymėta nė vienoje eilutėje; `FRONTU` ir `TASKERYJE` eilutės be Frontu kodo.
