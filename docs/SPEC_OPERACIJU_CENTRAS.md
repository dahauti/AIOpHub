# Operacijų centras: specifikacija (juodraštis)

2026-10-22 · Juodraštis peržiūrai. Šaltiniai: [OPERACIJU_CENTRAS.md](OPERACIJU_CENTRAS.md) (koncepcija), prototipas (`src/prototype/`, `src/templates/prototype/`), dabartinis cockpit kodas.

`[NEŽINOMA]` – negalima patvirtinti iš šio repo kodo. Kodo nuorodos – būklė commit'e `3ec3b5d`.

## 0. Dabartinė būklė trumpai

- **Procesai kode yra du.** Transporto priemonės (`MODULE = "workspaces_vehicles"`, `src/pipelines/sync_vehicles.py`) ir užduotys (`LOCK_MODULE = "tasks_sync"`, `src/services/task_runner.py`).
- **„Klientų sinchronizacija“ yra tik prototipo fixture**, kode jos nėra.
- **DB:** SQLite (`src/config.py` `ENV_DATABASE_URLS`, `src/database.py`).
- **Lentelės:**
  - ORM (`src/models/audit.py`): `sync_states`, `sync_config`, `sync_fingerprints`, `sync_audit_log`, `link_health`;
  - raw SQL (`src/storage/task_repository.py` `MIGRATIONS`): `task_sync_state`, `task_line_sync_state`, `sync_watermarks`.
- **Cockpit:** `GET /` ir `/partials/*`, `/api/*` (`src/api/routes_ui.py`, `*_handlers.py`). Autentifikacijos nėra (`src/api/` neturi jokio auth/session/role kodo).
- **Prototipas:** `/prototype/*` už `PROTOTYPE_ENABLED`, tik fixture duomenys (`src/prototype/fixtures.json`).

## 1. Ekranai: duomenys, kur jie yra dabar, ko trūksta

### 1.1 Apžvalga

| Rodoma | Kur yra dabar | Ko trūksta |
|---|---|---|
| Procesų sąrašas | Fiksuoti du moduliai kode; registro nėra | Proceso registras (id, pavadinimo raktas, savininkas, kryptis) |
| Būsena (veikia / reikia veiksmo) | `sync_states.status` (`IDLE`/`RUNNING`/`FAILED`) per modulį; `system_status()` `src/api/cockpit.py` | „Reikia veiksmo“ = atviros įrašų klaidos. Tokios sąvokos nėra |
| Režimas LIVE / DRY RUN | `sync_config.dry_run` per modulį (`src/repositories/config_repository.py`) | – |
| Paskutinis paleidimas + rezultatas | Užduotys: naujausia `sync_audit_log` eilutė `action="TASK_RUN"` su `TaskRunReport` payload (`src/repositories/task_status_repository.py`, `LastRun` `src/services/cockpit_models.py`). Transporto priemonės: **nesaugoma**; `SyncRunResult` tik log'e (`src/pipelines/scheduler.py:184`) | Paleidimų lentelė abiem procesams (žr. 2.2) |
| Kitas paleidimas | Transporto priemonės: `sync_config` grafikas + `src/pipelines/schedule.py`. Užduotys: aplikacijos grafiko nėra; paleidžiama iš UI arba CLI (`src/cli.py tasks`) | Užduočių grafikas (koncepcijoje – atviras klausimas). Ar užduotys paleidžiamos Windows Task Scheduler: `[NEŽINOMA]` (`deploy/register_task.ps1` registruoja tik `sync --batch`) |
| Reikia veiksmo: įrašų klaidos | Transporto priemonės: `sync_audit_log` `action="FAILED"`, `error_message` = techninis `str(exc)` angliškai. Užduotys: `task_sync_state.sync_status IN ('FAILED','VALIDATION_ERROR')` + `error_message` (tik paskutinė būsena per užsakymą) | Verslo priežastis, tipas (duomenų / sistemos), klaidos kodas; dalis užduočių klaidų neįrašoma visai (žr. 2.1) |

### 1.2 Proceso puslapis

| Rodoma | Kur yra dabar | Ko trūksta |
|---|---|---|
| Kryptis, šaltinis → tikslas | Tik kode / SPEC.MD | Proceso registras |
| Režimo juosta | `sync_config.dry_run` | – |
| Grafikas | Transporto priemonės: `sync_config` (`schedule_mode`, `nightly_time`, `interval_minutes`, `full_sync_*`). Užduotys: nėra | Užduočių grafikas |
| Atviros klaidos | Kaip 1.1 | Kaip 1.1 |
| Paleidimų istorija | Užduotys: visos `TASK_RUN` eilutės `sync_audit_log`. Transporto priemonės: nėra | Paleidimų lentelė |
| „Paleisti“, „Testinis šūvis“ | `POST /api/sync/trigger` (transporto priemonės); `POST /api/tasks/trigger`, `POST /api/tasks/single` ir `POST /api/canary` (užduotys) | Lygio tikrinimas serveryje; patvirtinimo dialogas su įrašų skaičiumi |

### 1.3 Paleidimas

| Rodoma | Kur yra dabar | Ko trūksta |
|---|---|---|
| Pavyko / praleista / klaidos / būtų sinchronizuota | Užduotys: `TASK_RUN` payload (`SyncRunResult` per kryptį). Transporto priemonės: nesaugoma | Paleidimų lentelė |
| Trukmė | Užduotys: `DirectionRun.elapsed_seconds`. Transporto priemonės: nesaugoma | – |
| Kas paleido | Nesaugoma niekur | Paleidėjas (grafikas / vartotojas), kai bus autentifikacija |
| Watermark / kursorius | Transporto priemonės: `sync_states.cursor_timestamp` (tik dabartinė reikšmė). Užduotys: `sync_watermarks` (`operative_tasks_bc` – laikas; `operative_tasks_frontu` – Frontu versija, Unix sekundės) | Reikšmė paleidimo pradžioje ir pabaigoje |
| HTTP kvietimai | Nesaugoma; tik `link_health` (paskutinis rezultatas per servisą) | Jei reikia: kvietimų santrauka per paleidimą `[NEŽINOMA: ar verta saugoti]` |
| Trace ID | Nėra. Log'e yra tik `run_id` (`uuid4().hex`, `extra`) | Trace ID per paleidimą ir įrašą (CLAUDE.md §4) |
| Paleidimo įrašai | Transporto priemonės: `sync_audit_log` eilutės per `entity_key` (SerialNo), **be ryšio su paleidimu** (stulpelio `run_id` nėra). Užduotys: tik `DRY_RUN_TASK` eilutės dry-run režimu | Įrašo rezultatas su `run_id` |

### 1.4 Įrašas

| Rodoma | Kur yra dabar | Ko trūksta |
|---|---|---|
| Objektas (SerialNo / SO nr.) | `sync_audit_log.entity_key`; `task_sync_state.service_order_no` | – |
| Verslo priežastis, ką daryti | Nėra | Klaidų katalogas (kodas → tipas → tekstas per kalbą) |
| Tipas (duomenų / sistemos) | Nėra | Klasifikacija |
| Techninė detalė: HTTP, URL, tiekėjo atsakymas | `ExternalServiceError` (`service`, `status_code`, `detail`, `url`; `src/adapters/resilience.py`) – išsaugomas tik `str(exc)` | Struktūruoti laukai vietoj vieno teksto |
| Mapping taisyklė | Nėra | `[NEŽINOMA]`: ar mapping klaidos išvis turi taisyklės identifikatorių |
| Payload | Transporto priemonės: `sync_audit_log.payload`. Užduotys: tik dry-run (`DRY_RUN_TASK`) | Užduočių LIVE payload |
| „Pakartoti“ | Transporto priemonės: `POST /api/sync/vehicle` (vienas SerialNo). Užduotys: `POST /api/tasks/single` (vienas SO / task) | Lygio tikrinimas; kartojimas pagal įrašą iš ekrano |

### 1.5 Žurnalas

| Rodoma | Kur yra dabar | Ko trūksta |
|---|---|---|
| Įvykiai | `sync_audit_log` (`GET /partials/audit` su filtrais, `src/schemas/audit_filter.py`); Glass Box konsolė `GET /partials/debug-console` (`src/services/debug_console.py`) – tas pats šaltinis | Vienas žurnalas pagal procesą; lygis (INFO/WARN/ERROR) |
| Režimo, kursoriaus, grafiko keitimai | **Neįrašomi**: `config_handlers.py`, `schedule_handlers.py`, `/partials/cursor/*` keičia būseną be audito | Įvykis su kas / kada / iš → į / priežastis |
| Kas | Nesaugoma | Autentifikacija |

### 1.6 Nustatymai

| Rodoma | Kur yra dabar | Ko trūksta |
|---|---|---|
| Režimas | `POST /partials/config/toggle-dry-run`, `POST /partials/tasks/toggle-dry-run` (perjungia be dialogo ir be priežasties) | Patvirtinimas, priežastis, auditas, Administruoti lygis |
| Kursorius | `POST /partials/cursor/set`, `/partials/cursor/reset-now` (tik transporto priemonės) | Tas pats; užduočių watermark keitimo UI nėra |
| Grafikas | `POST /partials/config/schedule` (tik transporto priemonės) | Užduočių grafikas |
| Teisės | Nėra | Teisių modulis (žr. 2.4) |
| Pašalinti | „Force live“ (`force_live` `POST /api/sync/vehicle`) – koncepcija liepia pašalinti | – |

## 2. Backend spragos (priklausomybių tvarka)

1. **Klaidos priežasties įrašymas.**
   - Užduotys: kai klaida įvyksta prieš sukuriant Frontu task (`task is None`), `FAILED` neįrašomas niekur, tik log'as (`src/services/task_outbound.py:114-118`).
   - „Order has no Frontu client match“ grąžina `VALIDATION_ERROR` be įrašo (`:97-98`).
   - Transporto priemonės įrašo tik `str(exc)`.
   - Reikia: struktūruotas klaidos įrašas (kodas, parametrai, HTTP, URL, tiekėjo atsakymas) kiekvienam nepavykusiam įrašui.
2. **Klasifikacija ir klaidų katalogas.**
   - Reikia: kodas → tipas (duomenų / sistemos) → tekstas per kalbą (ADR 0002 katalogo formatas). Nežinoma klaida = sistemos.
   - Klasifikavimo taisyklės kiekvienam kodui: `[NEŽINOMA]`, reikia savininko pavyzdžių (kokios klaidos kartojasi).
3. **Paleidimų ir įrašų rezultatų modelis.**
   - CLAUDE.md §6 reikalauja `job_runs` ir `job_requests`; jų nėra.
   - Reikia: `job_runs` (procesas, pradžia, pabaiga, būsena, režimas, paleidėjas, skaitikliai, watermark iki/po, trace ID) ir įrašo rezultato lentelės su `run_id`.
   - Tai taip pat išsprendžia skaitiklių prieštaravimą. „Errors today“ skaičiuoja tik `action="FAILED"` (`src/repositories/sync_repository.py`, `dashboard_snapshot`), o užduotys tokių eilučių nerašo – jų klaidos yra `TASK_RUN` payload ir `task_sync_state`.
4. **Trace ID.** Nėra niekur (`src/` neturi „trace“). Reikia: generuoti per paleidimą, rašyti į log'us ir rezultatus.
5. **Cockpit autentifikacija.**
   - Dabar jokios; Glass Box radinys (`docs/TECH_BACKLOG.md`) – būtina prieš prieigą ne iš localhost.
   - Būdas (Entra ID, legacy Hub sesija, kita): `[NEŽINOMA]`.
6. **Teisių lygiai Matyti / Valdyti / Administruoti.**
   - Koncepcija mini „esamą teisių modulį“ su stulpeliu `lygis`. Tas modulis yra legacy Hub'e, ne šiame repo: `[NEŽINOMA]` jo schema ir ar AIOpHub gali jį skaityti.
   - Priklauso nuo 5.
7. **Valdymo veiksmų auditas.** Režimo, kursoriaus, grafiko keitimai be audito ir be priežasties (1.5–1.6).
8. **Proceso registras.** Pavadinimas, savininkas, kryptis, grafikas vienoje vietoje, kad ekranai būtų bendri visiems moduliams.
9. **Schemos keitimai ne per migracijas.**
   - `src/database.py` kuria lenteles `create_all` ir prideda trūkstamus stulpelius `ALTER TABLE` paleidimo metu.
   - `task_repository.py` `MIGRATIONS` vykdo raw DDL.
   - CLAUDE.md §6 reikalauja numeruotų failų `db/migrations/` (katalogo nėra). Naujos lentelės turėtų eiti jau per migracijas.
10. **Kiti koncepcijos punktai, kodu nepatvirtinti.**
    - „Latency vienoda BC ir engine“: `[NEŽINOMA]`.
    - „Aplinka koduojama lentelių pavadinimuose (`_dev`)“: AIOpHub kode `_dev` lentelių nėra – aplinka parenka atskirą DB failą (`aio_dev.db` / `aio_prod.db`); ar tai liečia legacy – `[NEŽINOMA]`.
    - „TASK_RUN kas kelias minutes“: kas paleidžia – `[NEŽINOMA]`.

## 3. Atviras klausimas: duomenų bazė

**Ar naujas lenteles (2.1–2.3, 2.7) kurti SQLite dabar, ar pirma migruoti į PostgreSQL 14?** Sprendimo čia nėra.

| | A. Naujos lentelės SQLite dabar | B. Pirma migracija į PostgreSQL 14 |
|---|---|---|
| Už | Nereikia naujų priklausomybių ar serverio darbų; greičiausiai pasiekiami ekranai; esamas kodas ir testai (in-memory/failo SQLite) lieka | Atitinka CLAUDE.md tikslą (§2, §6: GRANTs, `hub` + `worker` procesai); naujos lentelės kuriamos vieną kartą tikroje DB; nebereikės perkelti istorijos du kartus |
| Prieš | Naujos lentelės vėliau migruojamos kartu su likusiomis; SQLite – vienas rašytojas, todėl du procesai (`hub` + `worker`) riboti; nukrypimas nuo CLAUDE.md tikslo ilgėja | Nauji ekranai atidedami; reikia DB serverio ir paskyrų; testams reikia sprendimo (testinė PG ar abi dialektai) |
| Darbo apimtis (iš kodo) | Nauji numeruoti migracijų failai SQLite dialektu; lieka visi SQLite specifiniai dalykai | Reikia keisti: `src/repositories/sync_statements.py` (`sqlalchemy.dialects.sqlite.insert`), `src/models/audit.py` (`UtcDateTime` naive tekstas, `AUDIT_ID` variantas), `src/database.py` (`PRAGMA`, `create_all`/`ALTER`), `src/config.py` validatorius (leidžia tik `sqlite+aiosqlite`), `src/storage/task_repository.py` raw DDL; PG tvarkyklė (`asyncpg` ar `psycopg`) nėra `requirements.txt` – reikia patvirtinimo; duomenų perkėlimas iš `data/aio_*.db` |
| Nežinoma | – | PostgreSQL 14 diegimas serveryje, valdymas, atsarginės kopijos: `[NEŽINOMA]` |

## 4. Etapų planas

Taisyklės visiems etapams:

- Nauji ekranai – atskiru maršrutu šalia seno cockpit (pvz. `/operacijos/`); senas cockpit lieka nepakeistas.
- Senas cockpit pašalinamas tik po parity (CLAUDE.md §9: shadow run → parity gate su ADR → pilot → cutover → retire).
- Vienas procesas vienu metu; pirmas – transporto priemonės (viena kryptis).

| Etapas | Turinys | Priklauso nuo |
|---|---|---|
| 0 | DB sprendimas (3 sk.) ir `db/migrations/` įvedimas | Savininko sprendimas |
| 1 | Backend transporto priemonėms: `job_runs`, įrašų rezultatai su `run_id`, trace ID, struktūruota klaida + klasifikacija + katalogas (LT, EN) | 0 |
| 2 | Autentifikacija ir lygiai (tik skaitymas): kas yra vartotojas, koks lygis | 2.5–2.6 `[NEŽINOMA]` atsakymai |
| 3 | Skaitymo ekranai transporto priemonėms: Apžvalga, Proceso puslapis, Paleidimas, Įrašas, Žurnalas – be jokių veiksmų | 1, 2 |
| 4 | Tas pats užduotims (1 ir 3 etapų turinys `tasks_sync`), įskaitant trūkstamų klaidų įrašymą (2.1) | 3 |
| 5 | Saugūs veiksmai: „Pakartoti“ įrašą (Matyti, jei atvirą klausimą išspręs savininkas), „Paleisti“, testinis šūvis (Valdyti) | 4 |
| 6 | Pavojingi veiksmai: LIVE, kursorius, grafikas (Administruoti) su dialogu, priežastimi ir auditu (UX_GUIDELINES 6 sk.); „Force live“ nepernešamas | 5, 2.7 |
| 7 | Migracijos ciklas: shadow run (≥ 2 verslo ciklai, abu UI) → parity checklist `docs/migration/` → ADR → pilot → cutover → seno cockpit pašalinimas | 6 |

## 5. Kas įrodo, kad etapas veikia (testai)

| Etapas | Testai |
|---|---|
| 0 | Migracijos taikomos tuščiai DB ir esamai DB kopijai; pakartotinis taikymas nieko nekeičia; jei PG – tie patys repository testai abiem dialektais arba PG |
| 1 | Pagal CLAUDE.md §7 kiekvienam sync job: happy path (`job_runs` = `succeeded`, skaitikliai sutampa su įrašų rezultatais); idempotency (du paleidimai – antras be naujų rašymų, rezultatai `skipped`); failure path (API klaida → `job_runs` = `failed`, jokios dalinės korupcijos, kursorius nepajuda už nepavykusio įrašo). Klaidų katalogas: kiekvienas kodas turi tekstą visose kalbose; nežinoma klaida → sistemos tipas; trace ID yra log'e ir rezultate |
| 2 | Be sesijos – 401 / nukreipimas; kiekvienas maršrutas turi lygio tikrinimą (testas pereina visus maršrutus); lygis tikrinamas serveryje net jei UI mygtuko nerodo (kaip `test_actions_check_the_level_on_the_server` prototipe) |
| 3 | Ekranai rodo tik DB duomenis (be išorinių kvietimų, CLAUDE.md §3); Matyti nemato sistemos klaidų ir techninės detalės; tekstai iš katalogų (prototipo `test_no_hardcoded_user_facing_text...` principas); WCAG kontrastas (prototipo testas) |
| 4 | Kaip 1 ir 3 užduotims; atskirai: klaida prieš Frontu task sukūrimą ir „no client match“ dabar sukuria įrašo rezultatą su kodu |
| 5 | Veiksmas be reikiamo lygio – 403; „Pakartoti“ apdoroja tik tą įrašą; paleidimas, kai kitas vyksta – BUSY, be antro paleidimo |
| 6 | Be priežasties – 422; sėkmingas veiksmas sukuria žurnalo įvykį (kas, kada, iš → į, priežastis); PROD negrįžtamam veiksmui reikia įvesti aplinkos pavadinimą; DRY RUN režimu išorinių rašymų nėra (fake adapteriai) |
| 7 | Parity checklist: kiekvienas seno cockpit veiksmas ir rodinys turi atitikmenį arba paaiškintą pašalinimą; shadow run palyginimo ataskaita |

## 6. Klausimai savininkui

- DB sprendimas (3 sk.).
- Autentifikacijos būdas ir teisių modulio šaltinis (2.5–2.6).
- Klaidų pavyzdžiai klasifikacijai (2.2): kurios klaidos dažniausios ir kas jas taiso.
- Užduočių grafikas ir kas dabar paleidžia `TASK_RUN` (2.10).
- Ar Matyti lygis gali pats spausti „Pakartoti“ (koncepcijos atviras klausimas).
