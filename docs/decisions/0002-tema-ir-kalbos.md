# ADR 0002: Šviesi ir tamsi tema, daugiakalbė sąsaja

- Data: 2026-10-21
- Būsena: Priimtas (įgyvendinta prototipe `/prototype`)
- Keičia: [ADR 0001](0001-vienas-vizualinis-sprendimas.md) 3 p. („UI kalba: lietuvių“) ir 4 p. („Šviesi tema: ATVIRA“)

## Kontekstas

ADR 0001 paliko šviesią temą atvirą ir nustatė vien lietuvišką UI. Hub naudos ir ne lietuviškai kalbantys kolegos (LV, ET, UK padaliniai), o dalis vartotojų dirba šviesioje aplinkoje. Etalonas (AlwarkOperationHub_Dev) jau turi tamsią ir šviesią temą.

## Sprendimas

1. **Tema**: tamsi numatyta, šviesi – pasirenkama. Įgyvendinama antru `src/static/hub.css` kintamųjų rinkiniu po `:root[data-theme="light"]`; komponentai spalvas ima tik iš `--hub-*` kintamųjų. Šviesios reikšmės laikinos, kol bus patvirtintos brand reikšmės (ADR 0001 2 p.).
2. **Kalbos**: LT numatyta, EN palaikoma; LV, ET, UK planuojamos. `<html lang>` atitinka pasirinktą kalbą.
3. **Pasirinkimas**: tema ir kalba saugomos slapuke; serveris iš karto piešia teisingą `data-theme` ir `lang`, todėl nėra mirgėjimo.
4. **Pranešimų katalogai**: vienas katalogas kiekvienai kalbai (JSON, vienodi raktai visose kalbose). Šablonuose nėra vartotojui matomo teksto, tik raktai.
5. **Daugiskaita**: CLDR kategorijos (`one`, `few`, `many`, `other`) ir taisyklė kiekvienai kalbai. LT sveikiesiems skaičiams: `one` (n%10=1, n%100 ne 11–19), `few` (n%10=2–9, n%100 ne 11–19), `other` (visa kita: 0, 10–20, 30 ...). `many` – tik trupmenoms, sveikiesiems negrąžinama. EN: `one` / `other`. Kiekviena nauja kalba ateina su savo taisykle ir testais (pvz. UK `few` = 2–4).
6. **Datos ir skaičiai**: formatas pagal kalbą (dešimtainis ženklas, tūkstančių skyriklis, datos šablonas). LT laikomasi `UX_GUIDELINES.md` 7 sk.: `YYYY-MM-DD HH:MM`, „0,7 s“.
7. **Klaidų katalogas**: verslo klaidos ir žurnalo įvykiai saugomi kaip kodas + parametrai (pvz. `uzduotis.nera_techniko`, `{objektas}`), tekstas imamas iš kalbos katalogo. Laisvas vartotojo tekstas (priežastis) ir tiekėjo atsakymai neverčiami.
8. **Priklausomybės**: be naujų bibliotekų – katalogai ir taisyklės savo kode (`src/prototype/i18n.py`). **Babel** yra numatytas atnaujinimo kelias, kai tikrame hub bus įvedamos LV, ET ar UK: CLDR daugiskaitos, datų ir skaičių taisyklės visoms kalboms ir gettext įrankiai.

## Pasekmės

- ADR 0001 3–4 p. pakeisti šiuo ADR.
- Šviesios temos reikšmės peržiūrimos kartu su brand patvirtinimu.
- Prototipe slapukai galioja tik `/prototype`; tikrame hub – visai svetainei.
- Kiekvienam naujam tekstui reikia rakto visuose kataloguose; tai tikrina testai.
