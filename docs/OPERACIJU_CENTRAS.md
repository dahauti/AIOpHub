# Operacijų centras: produkto koncepcija

Oct 21, 2026 · @Tomas

## Tikslas ir apimtis

Operacijų centras – viena vieta, kur kiekvienas operacinis procesas (integracija, auditas, perskaičiavimas, BC veiksmų trigeris) rodomas ir valdomas pagal tą patį šabloną. Pirmas modulis – BC ⇄ Frontu integracija.

Centras atsako į tris klausimus:

1. Ar mano procesai veikia?
2. Jei kas nepavyko – kodėl ir kas turi ištaisyti?
3. Kaip saugiai paleisti, pakartoti ar pakeisti procesą?

**Į apimtį įeina:** bendras procesų modelis, ekranų šablonas, teisių lygiai, klaidų modelis, aplinkų ir režimų taisyklės.

**Į apimtį neįeina:** BC ir Frontu adapterių perrašymas (esamas backend lieka), Hub'o skyrių ataskaitų ekranai (pvz., Prekių rezervacijos), vizualinė sistema (ją sprendžia ADR 0001 ir `docs/UX_GUIDELINES.md`).

## Vartotojai ir teisių lygiai

Basic ir advanced yra ne du atskiri vaizdai, o teisių lygiai tame pačiame ekrane: aukštesnis lygis mato daugiau sekcijų ir gali daugiau veiksmų.

| Lygis | Kas | Mato | Gali daryti |
| --- | --- | --- | --- |
| Matyti (basic) | Operacijų vadovai, skyriai | Būseną paprasta kalba, savo nepavykusius įrašus su verslo priežastimi ir ką daryti | Pakartoti įrašą pataisius duomenis |
| Valdyti (advanced) | Tomas, IT | Paleidimus, payload'us, HTTP, watermark'us, žurnalą | Paleisti procesą, testinis šūvis |
| Administruoti | Tik Tomas | Viską | Režimas LIVE, kursorius, grafikas, teisės |

Lygis priskiriamas per esamą teisių modulį: prie serviso pridedamas stulpelis `lygis`. Rolė (pvz., `logistics`) gali suteikti servisą Matyti lygiu, o Valdyti ir Administruoti suteikiami tik pavieniui.

Serveris tikrina lygį kiekvienam veiksmui nepriklausomai nuo to, ką rodo UI.

## Domeno modelis

Kiekvienas modulis turi atitikti šį modelį. Tada visi moduliai gauna tuos pačius ekranus be papildomo darbo.

| Sąvoka | Kas tai | BC–Frontu pavyzdys | Basic mato |
| --- | --- | --- | --- |
| Procesas | Valdoma procedūra su savininku ir grafiku | Užduočių sinchronizacija BC → Frontu | Taip |
| Paleidimas | Vienas proceso vykdymas: kas paleido, kada, kokiu režimu, rezultatas | Naktinis sync 02:00, LIVE | Tik rezultatą |
| Įrašas | Vienas apdorotas objektas su būsena (pavyko / praleista / klaida) | Užsakymas SO-DEMO-001 (išgalvotas pavyzdys) | Tik savo skyriaus klaidas |
| Priežastis | Kodėl įrašas nepavyko: verslo sakinys + techninė detalė | „Nenurodytas technikas“ + Frontu HTTP 422 (išgalvotas pavyzdys) | Tik verslo sakinį |
| Režimas | Ar procesas rašo: DRY RUN arba LIVE | LIVE | Taip |
| Aplinka | Į kurias sistemas jungiamasi: DEV arba PROD | DEV: BC Copy LT + alwarktest | Taip |

Kiekvienas paleidimas ir įrašas saugomas su trace ID, kad bet kurią klaidą būtų galima atsekti nuo ekrano iki log'o eilutės.

## Klaidų modelis

Klaida turi nukeliauti pas tą, kas gali ją ištaisyti. Tam kiekviena klaida klasifikuojama į vieną iš dviejų tipų.

| Tipas | Pavyzdys | Kas taiso | Kur matoma | Veiksmas |
| --- | --- | --- | --- | --- |
| Duomenų klaida | BC užsakyme nenurodytas technikas; nežinomas darbuotojas Frontu | Skyrius, BC'e | Basic ir advanced | Pataisyti BC → „Pakartoti“ |
| Sistemos klaida | Frontu HTTP 500; pasibaigęs token'as; BC nepasiekiamas | Tomas / IT | Tik advanced | Ištirti → pakartoti paleidimą |

**Du priežasties sluoksniai.** Kiekviena klaida turi:

- verslo sakinį – trumpą, lietuvišką, su nurodymu, ką daryti;
- techninę detalę – HTTP statusą, tiekėjo atsakymą, mapping taisyklę, trace ID.

**Nežinoma klaida** pagal nutylėjimą laikoma sistemos klaida ir rodoma tik advanced. Kai pasikartoja, jai sukuriamas verslo sakinys ir ji perklasifikuojama.

**Klaidų katalogas** gyvena backend'e: klaidos kodas → tipas → verslo sakinys. UI tekstų negeneruoja pats.

## Aplinkos ir režimai

Aplinka ir režimas yra dvi nepriklausomos ašys. UI visada rodo abi.

|  | DRY RUN (nerašo) | LIVE (rašo) |
| --- | --- | --- |
| DEV (BC Copy LT + alwarktest) | Kūrimas, testai | Integracijos testai su test sistemomis |
| PROD (produkcinės sistemos) | Patikra prieš įjungiant | Darbinis režimas |

**Aplinkos perjungiamos ne UI'e, o instancijomis.** DEV ir PROD yra atskiros instancijos su atskirais `.env`, DB, watermark'ais, kursoriais ir audit log'u. UI'e gali būti tik nuoroda „Atidaryti PROD“, vedanti į kitą instanciją. Lentelių pavadinimai be `_dev` priesagų: aplinką nustato instancija.

**Aplinkos žyma** – ženklelis (DEV / PROD) šalia aplikacijos pavadinimo header'yje; pilnas aplinkos aprašas – jo tooltip'e. PROD aplinkoje visa header eilutė raudona, DEV – įprasta. Atskiros aplinkos juostos nėra.

**Režimas:**

- nustatomas kiekvienam procesui atskirai ir užtikrinamas serveryje;
- naujas procesas visada pradeda DRY RUN režimu;
- perjungimas į LIVE – tik Administruoti lygiu, su patvirtinimu ir priežastimi, įrašomas į audit log;
- testinis šūvis laikosi proceso režimo; jokių „bypass“ varnelių.

**Pavojingi veiksmai** (rašymas į išorę, LIVE įjungimas, kursoriaus keitimas, masiniai veiksmai) vykdomi tik su patvirtinimo dialogu: ką padarys, kiek įrašų, kurioje aplinkoje, ar grįžtama. Detalės – `docs/UX_GUIDELINES.md` 6 skyriuje.

## Ekranų struktūra

Apžvalgoje rodoma tik tai, kas reikalauja veiksmo; visa kita – vienu paspaudimu giliau. Visi moduliai naudoja tuos pačius šešis ekranus.

&#91;embedded content: ekranų struktūra · 6 ekranai\]

Pagrindinis kelias eina iš viršaus žemyn iki įrašo, kuriame matoma klaidos priežastis. Žurnalas ir nustatymai pasiekiami iš proceso puslapio.

| Ekranas | Basic (Matyti) | Advanced (Valdyti / Administruoti) |
| --- | --- | --- |
| Apžvalga | Savo procesų būsena, savo klaidos | Visi procesai, sistemos klaidos |
| Proceso puslapis | Būsena, grafikas, atviros klaidos | + paleidimų istorija, „Paleisti“, testinis šūvis |
| Paleidimas | Rezultatas skaičiais | + trukmė, kas paleido, watermark, HTTP |
| Įrašas | Verslo priežastis, ką daryti, „Pakartoti“ | + techninė detalė, payload, trace ID |
| Žurnalas | Nerodomas | Visas |
| Nustatymai | Nerodomi | Tik Administruoti lygiu |

## BC–Frontu kaip pirmas atvejis

Integracija tampa dviem procesais pagal bendrą modelį. Esami adapteriai ir sync logika lieka; keičiasi tai, kas įrašoma ir kaip rodoma.

| Procesas | Kryptis | Šaltinis → tikslas | Grafikas |
| --- | --- | --- | --- |
| Užduočių sinchronizacija | BC ⇄ Frontu | ServiceOrders, TG\_Lines → tasks | Atviras klausimas |
| Transporto priemonių sinchronizacija | BC → Frontu | VehicleCard → workspaces | Kas naktį 02:00 + savaitinis pilnas |

**Spragos backend'e, kurias reikia uždaryti prieš naują UI:**

- [ ] Klaidos priežastis neįrašoma: audit log'e ERROR stulpelis tuščias, nors procesas rodo klaidą.
- [ ] Nėra klaidų klasifikacijos (duomenų / sistemos) ir klaidų katalogo.
- [ ] Skaitikliai prieštarauja: Tasks rodo 1 klaidą, „Errors today“ – 0.
- [ ] Latency reikšmė vienoda BC ir engine – patikrinti, ar matuojama atskirai.
- [ ] Watermark saugomas kaip Unix timestamp, BC task watermark tuščias.
- [ ] TASK\_RUN įvykiai kas kelias minutes – agreguoti į paleidimus.
- [ ] Du log'ai (audit ir diagnostic) dubliuoja tuos pačius įvykius – sujungti į vieną.
- [ ] Aplinka koduojama lentelių pavadinimuose (`_dev`) – perkelti į instancijos konfigūraciją.

**Iš UI pašalinama:** „Force live (bypass dry-run)“ varnelė, dubliuojančios būsenos kortelės, angliški tekstai (ADR 0001).

## Sprendimai, atviri klausimai ir kiti žingsniai

**Priimti sprendimai:**

| Sprendimas | Kodėl |
| --- | --- |
| UX kuriamas iš naujo, backend lieka | Adapteriai ir sync logika – vertė; UX – pigiausia keisti dabar |
| Bendras proceso modelis visiems moduliams | Nauji moduliai gauna ekranus be papildomo darbo |
| Basic/advanced = teisių lygiai, ne atskiri vaizdai | Vienas UI palaikymui |
| DEV/PROD – atskiros instancijos | Nesusimaišo watermark'ai, kursoriai, audit; neįmanoma per klaidą rašyti į PROD |
| UI lietuviškai, viena vizualinė sistema | ADR 0001 |

**Atviri klausimai:**

- Kokio skyriaus žmonės pirmieji gaus Matyti lygį BC–Frontu procesui?
- Ar basic vartotojas gali pats paspausti „Pakartoti“, ar tik pranešti?
- Užduočių sinchronizacijos grafikas: periodinis ar įvykiais?
- Kokie bus kiti 2–3 moduliai po BC–Frontu (kad modelis būtų patikrintas ne vienu atveju)?
- Šviesi tema (ADR 0001 – atvira).

**Kiti žingsniai:**

1. Peržiūrėti ir patvirtinti šią koncepciją.
2. Backend: klaidų priežasčių įrašymas ir klaidų katalogas (be jų UI neturi ką rodyti).
3. Apžvalgos ir įrašo detalės mockup'as.
4. `SPEC.md` Claude Code'ui pagal patvirtintą koncepciją ir mockup'ą.
5. Įgyvendinimas AIOpHub repo, pradedant transporto priemonių procesu (paprastesnis, viena kryptis).
