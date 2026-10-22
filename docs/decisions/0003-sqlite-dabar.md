# ADR 0003: Naujos lentelės SQLite dabar, per numeruotas migracijas

- Data: 2026-10-22
- Būsena: Priimtas
- Susiję: `docs/SPEC_OPERACIJU_CENTRAS.md` 3 sk. (atviras DB klausimas), CLAUDE.md §2, §6

## Kontekstas

Operacijų centrui reikia paleidimų (`job_runs`), įrašų rezultatų (`record_outcomes`) ir procesų registro. Tikslinė DB yra PostgreSQL 14, bet migracija į ją reikalauja serverio, tvarkyklės (`requirements.txt` jos neturi) ir esamo SQLite specifinio kodo keitimo. Laukti jos reiškia atidėti Operacijų centrą.

## Sprendimas

1. **Naujos lentelės kuriamos SQLite dabar**, tik per numeruotus failus `db/migrations/NNNN_*.sql`. Juos taiko `src/repositories/migration_runner.py` paleidimo metu (`init_db()`), taip pat `python -m src.database`.
2. **Migracijų taikymas**: kiekvienas failas taikomas vieną kartą, vienoje transakcijoje kartu su įrašu `schema_migrations` (versija, SHA-256, laikas). Pakartotinis paleidimas nieko nekeičia. Jei jau pritaikyto failo SHA-256 pasikeitė, paleidimas sustabdomas su aiškia klaida: pritaikytos migracijos nekeičiamos, keitimas – tik nauju failu.
3. **Portabilus SQL**, kad perėjimas į PostgreSQL 14 būtų mechaninis:
   - ID – `TEXT` (uuid hex), be `AUTOINCREMENT` / `SERIAL`;
   - laikas – `TIMESTAMP WITH TIME ZONE`, saugomas UTC;
   - reikšmių aibės – `TEXT` + `CHECK (... IN (...))`, ne enum tipai;
   - `INSERT ... ON CONFLICT DO NOTHING`, `CREATE ... IF NOT EXISTS`;
   - jokių `PRAGMA`, `WITHOUT ROWID` ir kitos SQLite sintaksės; failuose nėra `;` sakinių viduje.
4. **Saugomos reikšmės nepriklauso nuo kalbos**: būsenos, paleidimo būdai, rezultatai, klaidų tipai ir kodai saugomi angliškais ASCII kodais (pvz. `succeeded`, `manual`, `error`, `data`, `task.no_client_match`). LT/EN tekstas yra tik klaidų kataloge (`src/services/error_catalog.py`), ne DB.
5. **Techninė informacija išvaloma prieš įrašant**: `technical_detail` ir `error_summary` neturi Authorization/Bearer antraščių, žetonų, slaptažodžių ir prisijungimo eilučių; ilgis ne daugiau 500 simbolių.

## Pasekmės

- GRANT'ai programos rolėms (CLAUDE.md §6) neįmanomi SQLite; jie pridedami migruojant į PostgreSQL 14.
- Esamos lentelės (`create_all` `src/database.py`, raw DDL `src/storage/task_repository.py`) lieka kaip yra; jų perkėlimas į `db/migrations/` – atskira užduotis.
- SQLite turi vieną rašytoją: `hub` ir CLI/`worker` rašymai į naujas lenteles yra trumpi, bet lygiagretumas lieka ribotas iki PostgreSQL.
- Perėjimas į PostgreSQL 14 lieka atskira patvirtinta užduotis (CLAUDE.md §2).
