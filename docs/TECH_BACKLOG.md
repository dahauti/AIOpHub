# Inžinerinis backlog

## Etalono (AlwarkOperationHub_Dev) radiniai

Perkelta iš `docs/UX_GUIDELINES.md` 8 sk. („Inžinerinis backlog (ne UX)“). Radiniai iš etaloninio repo; prie kiekvieno – ar taikoma AIOpHub.

- **#12** Prisegti Tailwind versiją arba pereiti nuo CDN prie build'o. Nefiksuota CDN versija yra stabilumo ir saugumo rizika. Taisyklė priklauso CLAUDE.md.
  - Taikoma AIOpHub: **ne**. Tailwind CDN nenaudojamas; htmx (`2.0.4`) ir idiomorph (`0.3.0`) versijos prisegtos `src/templates/base.html`. Taisyklė įrašyta į `CLAUDE.MD` 2 sk.
- **#9** Įvesti tokenų sluoksnį (CSS kintamieji arba Tailwind config) vietoje `[#...]` klasių.
  - Taikoma AIOpHub: **iš dalies**. `src/static/hub.css` turi CSS kintamuosius, bet lieka `bg-[#0a0e17]` klasė ir hex reikšmės inline `<style>` `src/templates/base.html`.
- **#13** Iškelti `createMultiSelect`, `copyText`, `card()` į `static/js/` kaip bendrus komponentus.
  - Taikoma AIOpHub: **ne**. Šių komponentų nėra; bendri Jinja makrosai jau yra `src/templates/partials/macros.html`.

## ADR 0001 migracija

Žr. `docs/decisions/0001-vienas-vizualinis-sprendimas.md`.

- **(a)** Suderinti `src/static/hub.css` tokenų reikšmes su brand (akcentas `#A6192E` vietoje crimson). Laukia brand spalvų patvirtinimo.
- **(b)** Išversti AIOpHub UI į lietuvių kalbą, `lang="lt"`.
- **(c)** Įvesti tą patį tokenų sluoksnį (CSS kintamieji) AlwarkOperationHub_Dev.

## Duomenų bazė

- SQLite → PostgreSQL 14 migracija (CLAUDE.md tikslas); sync_repository.py docstring'ai klaidingai mini Postgres.

## Glass Box peržiūros radiniai (2026-10-21)

- **Cockpit be autentifikacijos.** Dashboard'as rodo vidinius URL (pvz., BC serverio vardą), užsakymų numerius ir neapdorotą klaidų tekstą be prisijungimo. Būtina ištaisyti prieš bet kokią prieigą tinkle už localhost ribų.
- **Ne HTTP klaidų tekstas ekrane.** Nepavykus Canary dėl ne HTTP klaidos, ekrane rodomas pačios klaidos tekstas (gali būti vidinių detalių, pvz., SQL). Pakeisti bendru pranešimu + trace ID; pilnas tekstas tik log'uose.
- **Canary žingsnių laiko langas.** Langas apvalinamas iki sekundės ir skaitomas po užrakto atleidimo, todėl į žingsnių detalę gali patekti gretimo task paleidimo `DRY_RUN_TASK` įrašai (tik rodymas, rašymas nepaliestas).
- **Testų spragos:**
  - `ExternalServiceError.url` užpildymas siuntimo funkcijoje (`send_with_retry`); kitose klaidų vietose URL nenustatomas.
  - FAILED Canary kelias: kas išsaugoma ekranui, kai `TaskSyncControl` gauna tikrą klaidą.
  - Dry-run Canary su tikrais audito įrašais: ar žingsniuose atsiranda būtent jo rašymai.

## Operacijų centras (ADR 0003)

- **[Aukštas prioritetas] Network errors (httpx.TransportError) not retried — violates CLAUDE.md §5.** Reads: retry with backoff. Writes: only if idempotent or after lookup by external key.
  - `src/adapters/resilience.py` `send_with_retry`: tenacity kartoja tik `_RetryableStatusError` (429/502/503/504, 5 bandymai, `wait_exponential_jitter(1, max=30)`, `Retry-After`); `httpx.TransportError` iškart tampa `ExternalServiceError`, be pakartojimo.
  - DEV 2026-10-22, canary SO029748: vienas `ConnectError` po ~21 s (Windows TCP SYN 3+6+12 s, mažiau nei `HTTP_TIMEOUT_SECONDS = 30` `src/services/task_runner.py`), 0 „Retrying“ eilučių; kitas paleidimas po 6 s pasiekė BC.
- **[Aukštas prioritetas] Tasks: no backfill; existing FRONTU orders never synced with empty watermark.** Proposed: periodic full reconciliation of all FRONTU-status orders (e.g. nightly), idempotent via mapping table, like the weekly vehicle full sync (CLAUDE.md §6). Must be solved before tasks cutover from legacy.
  - Tuščias watermark: `since = now − SYNC_INITIAL_LOOKBACK_DAYS` (`src/services/task_wiring.py` `wire`), DEV = 0 d.; skaičiuojama iš naujo kiekvieno paleidimo metu, 900 s buferis taikomas tik išsaugotam watermark (`src/storage/task_repository.py` `get_watermark`).
  - Watermark nustatomas tik kai paleidimas mato užsakymą (`src/services/task_outbound.py` `_advance`), todėl filtras `SystemModifiedAt ge <paleidimo pradžia>` nemato esamų užsakymų.
  - Pilno sinchronizavimo kelio nėra: CLI `tasks` turi `--sync/--outbound/--inbound/--single-so/--single-task/--bootstrap-watermark` (pastarasis – tik Frontu versijos bazė). Sprendimas: watermark tuščiais paleidimais nesėjamas.
- **Saugojimo politika `job_runs` / `record_outcomes`.** Lentelės auga su kiekvienu paleidimu; reikia nuspręsti, kiek laiko laikyti (pvz. N dienų arba N paleidimų per procesą) ir kas valo (worker job).
- **Transporto priemonių `record_outcomes`.** Dabar transporto priemonės rašo tik `job_runs`; įrašų rezultatams reikia pirma išskaidyti `src/pipelines/sync_vehicles.py` (~200 LOC).
- **Prisegti `requirements.txt` versijas** (CLAUDE.md §4 reikalauja prisegtų): dabar `>=` intervalai.
- **BC rašymai su `If-Match: *` – lygiagretus pakeitimas gali būti perrašytas.** `update_order_header` / `update_order_line` (`src/clients/bc_tasks_client.py`) visada siunčia `If-Match: *`; skaitymas `@odata.etag` nesaugo, kvietėjai ETag neperduoda. Siūloma: siųsti perskaityto įrašo ETag; BC 412 (įrašas pakeistas) → pakartoti po 5 min., po 10 min., tada įspėjimas (ta pati schema kaip BC skaitymo „changed between initial and JIT load“ klaidai). Neįgyvendinta (savininko sprendimas 2026-10-23).

## Diegimas

- **Server must reach BC on-prem (lt.alwark.eu:7048) without a personal VPN; disappears with BC SaaS.**
  - DEV 2026-10-22: BC nepasiekiamas be VPN (`ConnectError`, canary SO029748); tokia klaida dabar saugoma kaip `system.bc_unreachable` (`src/services/error_catalog.py`).
