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

- **Saugojimo politika `job_runs` / `record_outcomes`.** Lentelės auga su kiekvienu paleidimu; reikia nuspręsti, kiek laiko laikyti (pvz. N dienų arba N paleidimų per procesą) ir kas valo (worker job).
- **Transporto priemonių `record_outcomes`.** Dabar transporto priemonės rašo tik `job_runs`; įrašų rezultatams reikia pirma išskaidyti `src/pipelines/sync_vehicles.py` (~200 LOC).
- **Prisegti `requirements.txt` versijas** (CLAUDE.md §4 reikalauja prisegtų): dabar `>=` intervalai.
