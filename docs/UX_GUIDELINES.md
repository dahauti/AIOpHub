# Operation Hub: UX gairės

> Reference implementation: AlwarkOperationHub_Dev (templates/reservations.html). This repo must follow these guidelines; it does not yet implement all of them.
>
> Juodraštis 2026-10-21. Išgauta iš „Prekių rezervacijos“ ekrano ir bendro layout'o; tik tai, kas tinka kitiems ekranams.
> **[STANDARD]** = galioja šiame repo (iš etaloninės implementacijos). **[REFERENCE]** = etalono aprašas, šiame repo netaikomas tiesiogiai. **[PROPOSED]** = siūlymas, kode dar nėra.
> Keliai nuo etalono repo šaknies (`AlwarkOperationHub_Dev`).
> Šaltiniai: `templates/reservations.html`, `templates/_header.html`, `static/theme.css`; palyginimui `frontu_sync_errors.html`, `spare_parts.html`, `vehicles.html`.
> Sprendimai: [ADR 0001](decisions/0001-vienas-vizualinis-sprendimas.md).

## 1. Design tokens

> Šiame repo galioja src/static/hub.css kintamieji ir src/static/app.css (ADR 0001). Šis skyrius – etalono aprašas: spalvų prasmės lentelė galioja, konkrečios Tailwind klasės – ne. Naujų spalvų neįvesti, kol nepatvirtintos brand reikšmės.

Šaltinis: `templates/reservations.html`, `templates/_header.html`, `static/theme.css`.

**Kaip veikia tema.** [REFERENCE] CSS kintamųjų nėra. Tamsi tema yra numatyta ir aprašyta Tailwind klasėmis šablonuose; `static/theme.css` tik perrašo konkrečias klases, kai `<html data-theme="light">`. Tema įjungiama prieš piešimą iš `localStorage['hub.theme']`, perjungiama `_header.html`.

- [REFERENCE] Todėl **tokenas = Tailwind klasė, kurią `theme.css` atvaizduoja**. Klasė, kurios `theme.css` neturi, šviesioje temoje liks tamsi.
- [PROPOSED] Naują spalvos klasę naudoti tik pridėjus jos šviesų atitikmenį į `theme.css`. Reikšmių čia nekartoju: šaltinis yra `theme.css`.

**Spalvos pagal prasmę** [STANDARD] prasmė ir spalvų šeima; [REFERENCE] klasių pavadinimai

| Prasmė | Klasės (tamsi tema) |
|---|---|
| Puslapio fonas | `bg-[#0B0F17]` |
| Paviršius (kortelė, sekcija, header, panelė) | `bg-[#111827]` + `border-slate-800` |
| Įdubęs paviršius (thead, išskleista eilutė) | `bg-[#0F172A]` |
| Laukas | `.field` (`#1E293B`, rėmelis `#334155`) |
| Pagrindinis veiksmas, aktyvus nav, fokusas, checkbox | `bg-[#A6192E]`, hover `#BE1E35`, `accent-[#A6192E]` |
| Antrinis veiksmas | `bg-[#1E293B] border-slate-700 text-slate-300` |
| Teksto lygiai | `text-white` (antraštės, ID) > `slate-200` (body) > `slate-300` (aprašai) > `slate-400` (pagalbinis) |
| Gerai / pilnai / vietoje | emerald `950/70` fonas, `300` tekstas, `800` rėmelis |
| Dalinai / kelyje | amber, ta pati schema |
| Blogai / nėra | rose `950/50`, `300`, `800` |
| Teksto veiksmas (nuoroda) | `text-sky-400 hover:text-sky-300` |

**Tipografija** [REFERENCE]

| Vaidmuo | Klasės |
|---|---|
| Skyriaus etiketė virš h1 | `text-xs font-bold uppercase tracking-wider text-[#F43F5E]` |
| h1 | `text-xl font-extrabold text-white leading-tight` |
| Sekcijos h2 | `text-sm font-extrabold text-white` |
| Paskirties sakinys | `text-xs text-slate-300 leading-snug max-w-3xl` |
| Lauko etiketė, thead | `text-[11px] font-bold uppercase tracking-wider` |
| Lentelės tekstas | `text-xs` |
| Mašininės reikšmės (užsakymo nr., kodai, laikas, User ID) | `font-mono` |

**Atstumai ir formos** [REFERENCE] (kompaktiškas standartas iš `projects/hub-ui-kompaktiska/PLANAS.md`)
- `main`: `px-8 py-4 space-y-3`; header `px-8 py-2`.
- Filtrų juosta `px-4 py-3 gap-3`; lentelės antraštės juosta `px-4 py-2`; celė `px-3 py-1.5`.
- Spinduliai: sekcijos `rounded-lg`; laukai, mygtukai, panelės `rounded-md`; badge'ai `rounded`.
- Išskleidžiamos panelės: `z-40 shadow-2xl border-slate-700`; header `sticky z-50`.
- Išimtis [REFERENCE]: skenavimo laukai `dashboard.html` nesumažinti (skeneris, lietimas).

## 2. Puslapio anatomija

Šaltinis: `templates/reservations.html`, `templates/_header.html`.

[STANDARD] Tvarka iš viršaus:

1. **`_header.html`**: logotipas, skyrių nav (tik leidžiami skyriai), tema, vartotojas (teisės tooltip'e), atsijungti. Antra juosta: skyriaus puslapiai, kai jų > 1.
2. **`header_badges`**: duomenų šaltinis `font-mono` badge (pvz. „BC Sales + Service Lines · OAuth“) + brandos žyma „Beta“ amber.
3. **Antraštės blokas kairėje**: skyriaus etiketė („Aftersales · Pardavimo ir serviso užsakymai“) → h1 → paskirties sakinys → šviežumo eilutė `#last_run`.
   - Paskirties sakinys: kas rodoma + spalvų legenda tekste + kur ieškoti kito (`Technika – Komercijos skyriuje`) + **„BC niekas nekeičiama.“** read-only ekranams.
4. **KPI kortelės dešinėje** `#summary_cards`, `card()`: spalvota `text-[10px]` uppercase etiketė, baltas `text-lg` skaičius, `min-w-[7.5rem]`. Pirma kortelė visada viso kiekis, paskui būsenos ta pačia spalva kaip badge'ai, gale procentas su `title` paaiškinimu.
5. **Pranešimai**: `#status_box` (ok/err, vienas, perrašomas) + `#warnings` sąrašas amber juostomis `text-[11px]`. Įspėjimai pasako, kas **neįtraukta ir kodėl**.
6. **Filtrų juosta** `section#filters`: multi-select `w-40`, paieška `flex-1 min-w-[12rem]`, veiksmų mygtukai paskutiniai. Etiketės su `title` paaiškinimu, kai logika neakivaizdi.
7. **Lentelė** `section#results`: antraštės juosta su `h2` „N iš M užsakymų · laikas“, užuomina „Paspauskite eilutę...“, stulpelių meniu dešinėje.
8. **Būsenos**: pradinė `#intro` su dashed rėmeliu („Kraunama iš BC...“ + kiek užtruks), klaidos atveju perrašoma; tuščia `#empty_rows` skiria „nėra duomenų“ ir „filtrų neatitinka“.

[PROPOSED] Kiekvienas naujas sąrašo ekranas laikosi 1–8 tvarkos; nereikalingus blokus praleisti, bet nekeisti tvarkos.

[STANDARD] **Pilnas plotis.** Turinys neturi `max-width` konteinerio: visas ekrano plotis, šonuose 32 px (siauruose ekranuose 24 px). Lentelės, žurnalai, kortelės ir sąrašai užima visą plotį. Prozos tekstas (paskirties sakiniai, paaiškinimai, dialogo tekstas) – ne daugiau ~75 simbolių eilutėje (`max-width: 75ch`).

## 3. Sąveikos šablonai

Šaltinis: `templates/reservations.html`.

**Multi-select filtras** [STANDARD]; etalone [REFERENCE] `createMultiSelect()`, kopija ir Sandėlio puslapyje:
- Paieška panelėje, „Pažymėti visus“ / „Nuimti visus“ (veikia tik matomiems), **„Taikyti“** pritaiko (ne iškart).
- Prie kiekvienos reikšmės skaičius `font-mono`, skaičiuojamas iš aktualios aibės.
- Mygtuko etiketė: „Visi ... (N)“ / iki 2 pavadinimų / „K iš N“ / „Nieko nepasirinkta“. Tuščia reikšmė rodoma „(tuščia)“.
- Tuščias pasirinkimas neleidžiamas („Pasirinkite bent vieną“). Uždaroma paspaudus šalia arba Esc.
- Numatytai paslėptos reikšmės ateina iš serverio; [REFERENCE] `default_hidden_statuses`.
- Būsenų filtrų reikšmės rikiuojamos nuo blogiausios, ne abėcėliškai.

**KPI ir filtrai** [STANDARD] Kortelės skaičiuoja pagal matomas eilutes; filtruojant etiketė „(filtr.)“ ir „iš N“.

**Lentelė**
- [STANDARD] Rikiavimas paspaudus `th`, ▲/▼, kategorijos pagal prasmės tvarką.
- [REFERENCE] Stulpeliai aprašyti vienu `COLUMNS` masyvu antraštei, celėms ir meniu.
- [STANDARD] `fixed` stulpelis visada pirmas, neslepiamas.
- [STANDARD] Stulpelių rodymas ir tvarka (drag meniu ir antraštėje) įsimenami; „Atkurti numatytus“.
- [REFERENCE] Raktas `localStorage['hub.<puslapis>.columns.<kind>']` (+ `.order`); `localStorage` visada `try/catch`.
- [STANDARD] Skaičiai lygiuojami dešinėn; santykiai „3 / 5“, procentai „42.5 %“. [REFERENCE] `right: true`.

**Eilutės išskleidimas** [STANDARD] Paspaudus eilutę rodoma vidinė eilučių lentelė įdubusiame paviršiuje ([REFERENCE] `bg-[#0F172A]`); žymeklis ▸/▾ prie ID. Jei pažymėtas tekstas, neišskleidžiama, kad veiktų Ctrl+C. Kopijavimo mygtukas prie ID/VIN, rezultatas ✓ 1,5 s.

**Atnaujinimas** [STANDARD] Duomenys kraunami automatiškai atidarius; mygtukas „Atnaujinti iš BC“ užrakinamas, tekstas „Kraunama iš BC...“. Po sėkmės atnaujinama šviežumo eilutė.

**Eksportas**
- [STANDARD] „Excel“: **tik matomos (filtruotos) eilutės**, 2 lapai (antraštės + eilutės), failas `<Objektas>_<data>.xlsx`. Išjungtas, kol nėra duomenų.
- [REFERENCE] Generuojama kliente su SheetJS.
- [STANDARD] „Siųsti man“: serveris iš naujo nuskaito BC su tais pačiais filtrais ir siunčia laišką su Excel; tai pasakoma ir `title`, ir sėkmės pranešime. [REFERENCE] Filtrai perduodami per `currentFilters()`.

**Klaidos** [STANDARD] 401 → „Sesija baigėsi. Atnaujinkite puslapį ir prisijunkite iš naujo.“; kitaip `detail` iš serverio arba „Serverio klaida (HTTP N).“. Formatas: `<strong>Klaida:</strong> tekstas`.

## 4. Tekstų konvencijos

Šaltinis: `templates/reservations.html`.

- [STANDARD] UI kalba lietuvių, `lang="lt"` (ADR 0001), kreipinys **jūs** („Paspauskite“, „Pasirinkite“, „išsiunčia jums“). Kabutės „...“.
- [STANDARD] BC terminai lieka originalūs, kai vartotojai juos taip mato BC: `User ID`, `ABORTED`, `Non-Inventory`, `TO / PO`.
- [STANDARD] Šaltinis įvardijamas trijose vietose: header badge (sistema + autentifikacija), paskirties sakinys (ar rašoma į sistemą), šviežumo eilutė.
- [STANDARD] Šviežumo eilutė `font-mono text-[11px]`: „Nuskaityta iš BC: {laikas} · {N} užsakymų · {s} s“; tas pats laikas lentelės h2.
- [STANDARD] Sutrumpinimai su tašku tik ankštose vietose: „Artim. siuntimas“, „Pilnai rez.“, „Pardavėjas / konsult.“.
- [PROPOSED] Read-only ekranų paskirties sakinys visada baigiasi „{Sistema} niekas nekeičiama.“

> 5–8 skyriai: normatyvinės taisyklės. Visi punktai [PROPOSED] – kode jų dar nėra. Šie skyriai yra privalomi visiems naujiems ekranams, ypač integracijų ir valdymo.

## 5. Ekrano tipai ir rizikos deklaravimas

Kiekvienas ekranas priklauso vienam tipui. Tipas nustato, ką privaloma deklaruoti.

| Tipas | Pavyzdys | Paskirties sakinyje privaloma |
|---|---|---|
| Skaitantis | Prekių rezervacijos | „{Sistema} niekas nekeičiama.“ |
| Snapshot / skaičiuojantis | Dalių laukimas, auditai | Iš kur ir kada perskaičiuota |
| Rašantis į išorę | Integracijos (BC ⇄ Frontu) | Į kurią sistemą rašoma ir kokiu režimu |

**Rašantiems ekranams:**
- Po antrašte visada matoma **režimo juosta**:
  - `DRY RUN`: neutrali (slate) su amber ženkliuku: „Režimas: DRY RUN – į Frontu niekas nerašoma.“
  - `LIVE`: ryški rose juosta: „Režimas: LIVE – pakeitimai rašomi į Frontu ({aplinka}).“
- Režimą nustato ir užtikrina **serveris**. UI rodo tik serverio grąžintą būseną ir niekada nelaiko režimo vien kliente.
- Veiksmo mygtuko tekstas pasako režimą: „Sinchronizuoti (dry run)“ / „Sinchronizuoti į Frontu“.
- Kai automatinis grafikas ir režimas susiję, tai pasakoma kartu: „Kitas paleidimas 02:00 · DRY RUN“.

## 6. Pavojingi veiksmai

**Pavojingas veiksmas** yra bet kuris iš šių: rašymas į išorinę sistemą, režimo keitimas į LIVE, kursoriaus/watermark keitimas, trynimas, masinis veiksmas.

- Niekada neatliekamas vienu paspaudimu ar varnele. Visada būtinas patvirtinimo dialogas, kuriame nurodyta:
  - ką veiksmas padarys,
  - kiek įrašų paveiks,
  - kurioje sistemoje ir aplinkoje (DEV/TEST/PROD),
  - ar jis grįžtamas.
- LIVE įjungimui ir kursoriaus atstatymui reikia **privalomo priežasties lauko**. Įrašas eina į Audit Log: kas, kada, iš kokios reikšmės į kokią, priežastis.
- Negrįžtamiems veiksmams PROD aplinkoje patvirtinimui reikia įvesti objekto ar aplinkos pavadinimą.
- Stilius: outline rose + ⚠ ikona. Pavojingas veiksmas **nenaudoja** pagrindinio `#A6192E` užpildo ir niekada nėra vizualiai dominuojantis ekrano veiksmas.
- Varnelė niekada neperjungia saugumo režimo (pvz., „Dry-run bypass“). Tam skirtas atskiras, aiškiai pavadintas veiksmas su dialogu.
- Pavojingi veiksmai rodomi tik vartotojams, turintiems reikiamą rolę. **Serveris tikrina rolę nepriklausomai nuo UI.**
- Aplinkos žyma (DEV/TEST/PROD) visada matoma header'yje. PROD žyma ryški, kitos – neutralios.

## 7. Mašininės reikšmės ir terminai

- Laikas visada rodomas žmogui suprantamai: `YYYY-MM-DD HH:MM` vietos laiku. Unix timestamp, GUID ir watermark žalios reikšmės rodomos tik detalėse arba tooltip'e, su kopijavimo mygtuku.
- Trukmė ir laiko momentas turi skirtingas etiketes: „Trukmė: 0,7 s“ ≠ „Paskutinis paleidimas: 14:51“.
- Viena sąvoka turi vieną terminą visoje sistemoje (pvz., renkamės vieną iš „watermark“ / „cursor“ / „žymeklis“). Terminų žodynas pateiktas 8 skyriuje.
- Dry-run rezultatai rodomi kaip numatomi: „Būtų sinchronizuota: 12“, o ne „Synced: 0“.
- Skaičių formatas lietuviškas: dešimtainis kablelis („42,5 %“, „0,7 s“), tūkstančiai be skyriklio arba su tarpu.

## 8. Sprendimai dėl Open issues

Pateikiami patvirtinimui. Patvirtinus, kiekvienas sprendimas tampa taisykle, o issue išbraukiamas.

| # | Sprendimas |
|---|---|
| 1 | Vienas akcentas `#A6192E` (veiksmai, aktyvus nav, skyriaus etiketė). `#F43F5E` ir `#E11D48` atsisakoma. |
| 2 | Rose lieka abiem prasmėms, bet jas atskiria forma: duomenų būsena = badge, sistemos klaida = `#status_box` juosta su „Klaida:“. |
| 3 | Amber naudojamas tik duomenų būsenai (Dalinai/Kelyje) ir DRY RUN žymai. Įspėjimų juostos tampa neutralios (slate) su amber kairiu kraštu. „Beta“ tampa slate outline. |
| 4 | Visur „Būsena“. |
| 5 | Visur lietuviškai, Excel lapai su diakritikais („Užsakymai“, „Eilutės“). |
| 6 | Vienas šablonas: „Nuskaityta iš {sistema}: {laikas}“ (gyvi duomenys) / „Perskaičiuota: {laikas}“ (snapshot), o po to „· {N} {objektai} · {s} s“. |
| 7 | „Atnaujinti iš {sistema}“. Eksporto mygtukas visada „Excel“, o apimtis nurodoma `title`. |
| 8 | Vienas būsenų žodynas: „Pilnai rezervuota / Dalinai rezervuota / Nerezervuota“. Ankštose vietose naudojama trumpa forma „Pilnai / Dalinai / Ne“. |
| 10–11 | Taisoma kartu su #9: `text-[11px]` visoms etiketėms. |
| 9, 12, 13 | Tai ne UX taisyklės, todėl perkeliama į inžinerinį backlog (žr. `docs/TECH_BACKLOG.md`). |

## 9. Open issues (nekodifikuota)

Šaltinis: `templates/reservations.html`, `templates/_header.html`, `static/theme.css`, `frontu_sync_errors.html`, `spare_parts.html`, `vehicles.html`.

1. **Trys akcento raudonos**: `#A6192E` (veiksmai), `#F43F5E` (skyriaus etiketė), `#E11D48` (`_header.html`). Šviesioje temoje visos suvedamos į `#A6192E` (`theme.css`), tamsioje skiriasi. → žr. 8 sk.
2. **Rose dviprasmiška**: ir duomenų būsena („Be rezervacijos“, „Nerezervuota“), ir sistemos klaida (`#status_box` err). → žr. 8 sk.
3. **Amber perkrauta**: „Dalinai“/„Kelyje“, įspėjimų juostos ir „Beta“ žyma. → žr. 8 sk.
4. **Būsena vs Statusas**: filtras „Būsena“, stulpelis „Statusas“. → žr. 8 sk.
5. **Kalbų mišinys**: stulpelis „Reserve“, Excel lapai be diakritikų „Uzsakymai“, „Eilutes“. → žr. 8 sk.
6. **Šviežumo formuluotės skiriasi**: „Nuskaityta iš BC“, „Paskutinis atnaujinimas“ (`frontu_sync_errors.html`), „Perskaičiuota iš BC“ / „Paskutinis suplanuotas auditas“ (`spare_parts.html`). → žr. 8 sk.
7. **Mygtukų pavadinimai**: „Atnaujinti iš BC“ vs „Atnaujinti“ (`frontu_sync_errors.html`); „Excel“ vs „Excel - visos atfiltruotos“ (`vehicles.html`) vs „Excel (be techninės informacijos)“ (`frontu_sync_errors.html`). → žr. 8 sk.
8. **Būsenos žodžiai nesutampa**: eilutėje „Taip / Dalinai / Ne“, užsakyme „Pilnai rezervuota / Dalinai rezervuota / Be rezervacijos“, Excel „Pilnai rez.: Taip/Ne“. → žr. 8 sk.
10. **Klasės be šviesaus atitikmens**: pvz. `text-slate-500` `theme.css` neatvaizduota. → žr. 8 sk.
11. **Etikečių dydžiai**: KPI `text-[10px]`, laukų ir thead `text-[11px]`. → žr. 8 sk.

## 10. Neatitikimai su AIOpHub

Šaltinis: `src/templates/base.html`, `src/static/app.css`, `src/static/hub.css`. Lyginama su 1 skyriumi.

1. **Kalba**: `base.html` turi `lang="en"`, UI tekstai daugiausia angliški; etalonas – `lang="lt"`. → migruoti pagal ADR 0001
2. **CSS kintamieji**: `hub.css` `:root` turi kintamuosius (`--hub-bg`, `--hub-surface`, `--hub-crimson`, …); etalone „CSS kintamųjų nėra“. → AIOpHub jau atitinka tikslą; migruoti etalone (backlog c)
3. **Akcento raudona**: `--hub-crimson` `#991b1b`, `--hub-crimson-2` `#b91c1c`, hover `#dc2626`; etalone `#A6192E`, hover `#BE1E35`. → migruoti pagal ADR 0001 (laukia brand patvirtinimo)
4. **Puslapio fonas**: `#0a0e17` (`--hub-bg`, `bg-[#0a0e17]`); etalone `#0B0F17`. → migruoti pagal ADR 0001
5. **Hex reikšmės inline `<style>`**: `base.html` `.badge-*` klasės su hex/rgba reikšmėmis, ne kintamaisiais. → migruoti pagal ADR 0001
6. **Šviesi tema**: nėra `theme.css`, `data-theme` ar `localStorage` perjungimo; `base.html` fiksuotai `class="dark"`. → atviras
7. **Tailwind**: Tailwind nenaudojamas; `app.css` yra ranka parašytas Tailwind stiliaus klasių rinkinys (testas reikalauja, kad kiekviena šablono klasė turėtų taisyklę, ir draudžia Tailwind CDN). → išspręsta ADR 0001 – tikslas app.css + hub.css; Tailwind lieka tik etalone
8. **Būsenų spalvos**: būsenos rodomos `.pill-*` klasėmis (`hub.css`), ne emerald/amber/rose klasėmis. → migruoti – .pill-* spalvas suderinti su 1 sk. prasmių lentele; .pill-* klasės lieka
9. **`main` atstumai**: `mx-auto max-w-7xl px-4 py-6 sm:px-6 lg:py-8`; etalone `px-8 py-4 space-y-3`. → atviras
10. **Header**: nėra `_header.html`; header yra `src/templates/partials/header.html`. → atviras
