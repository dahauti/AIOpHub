# ADR 0001: Vienas vizualinis sprendimas AIOpHub ir AlwarkOperationHub_Dev

- Data: 2026-10-21
- Būsena: Priimtas; 3–4 p. pakeisti [ADR 0002](0002-tema-ir-kalbos.md)

## Kontekstas

AIOpHub ir AlwarkOperationHub_Dev taps vienu produktu. Šiuo metu jų vizualiniai sprendimai skiriasi: etalonas (AlwarkOperationHub_Dev) neturi CSS kintamųjų ir remiasi Tailwind klasėmis bei `theme.css`, o AIOpHub naudoja CSS kintamuosius `src/static/hub.css` ir ranka parašytą `src/static/app.css`. Skiriasi ir akcento spalvos, UI kalba bei šviesios temos palaikymas (žr. `docs/UX_GUIDELINES.md` 10 sk.).

## Sprendimas

1. **Tokenų struktūra**: CSS kintamieji `src/static/hub.css` (AIOpHub būdas) yra tikslas abiem repo.
2. **Tokenų reikšmės**: etalono brand reikšmės (akcentas `#A6192E`) pakeičia dabartines crimson reikšmes. Laukia brand spalvų patvirtinimo.
3. **UI kalba**: lietuvių, `lang="lt"`, abiem repo.
4. **Šviesi tema**: ATVIRA – bus nuspręsta vėliau.

## Pasekmės

- AIOpHub: `hub.css` kintamųjų reikšmės derinamos su brand (po patvirtinimo); UI verčiamas į lietuvių kalbą.
- AlwarkOperationHub_Dev: įvedamas toks pat tokenų sluoksnis (CSS kintamieji).
- `docs/UX_GUIDELINES.md` 1 sk. klasių pavadinimai lieka etalono aprašu [REFERENCE]; spalvų prasmės galioja abiem repo.
- Kol brand reikšmės nepatvirtintos, naujos spalvos neįvedamos.
- Darbai įrašyti `docs/TECH_BACKLOG.md`.
