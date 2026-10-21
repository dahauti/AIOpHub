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
