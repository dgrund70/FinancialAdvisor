# UX-brief — Beleggingsdashboard

Opdrachtdocument voor een coding agent (Codex). Bedoeld om per onderdeel als
losse opdracht te geven, niet als één grote klus.

Peildatum: 06-09-2026, op commit `15db0ea`.

---

## 0. Harde randvoorwaarden

Deze gelden voor **elke** opdracht hieronder. Neem ze letterlijk over in de prompt.

- Bootstrap 5.3.8 + Bootstrap Icons via CDN, zoals nu. **Geen** Tailwind, geen
  npm, geen buildstap, geen extra CDN-afhankelijkheden zonder overleg.
- Server-rendered Jinja. Geen frontend-framework, geen SPA-gedrag.
- Routes, endpoint-namen, templatenamen en de bestaande `data-taak`-flow
  blijven ongewijzigd.
- `app.py` alleen aanpassen waar de opdracht dat expliciet zegt. Elke andere
  wijziging in `app.py` melden, niet stilzwijgend doorvoeren.
- `pytest` moet groen blijven (nu 64 tests). Nieuwe logica in `app.py` krijgt
  een test in `tests/`.
- UI-taal is Nederlands. Geen Engelse labels, geen halve vertalingen.
- Eén onderdeel per commit, met een commitbericht dat zegt wat er verandert.

---

## P1 — Fouten die je elke dag ziet

### 1.1 Getalnotatie is Engels

Overal staat `"%.2f"|format(v)`, dus een portefeuille van ruim twee ton toont als
`€ 234567.89`. Voor een Nederlandse app hoort dat `€ 234.567,89` te zijn.

**Opdracht:** voeg in `app.py` een Jinja-filter `bedrag_nl` toe (duizendtalpunt,
decimale komma, twee decimalen, `—` bij `None`) en een `pct_nl` voor percentages
(decimale komma, één decimaal, expliciet `+` bij positief). Vervang de macro's
`eur`, `geld`, `bedrag` en `pct` in `dashboard.html`, `analyse.html` en
`transacties.html` zodat ze het filter gebruiken. Aantallen die nu via
`compact_float` lopen, ook langs de komma-notatie.

Let op: `compact_float` heeft eigen afrondingslogica voor fractionele fondsstukken
(zie `app.py` regel 149) — die logica blijft, alleen de weergave verandert.

**Test:** unittest op het filter, inclusief `None`, 0, negatief, en een bedrag
boven de miljoen.

### 1.2 Tabellen zijn onbruikbaar op een telefoon

`dashboard.html` heeft een positietabel met 11 kolommen. Op mobiel blijven er via
`d-none`-klassen zes over, en dat is nog steeds te veel voor 375 px breed.
`transacties.html` heeft hetzelfde met negen kolommen.

**Opdracht:** onder de `md`-breakpoint geen tabel maar een kaartenlijst: per regel
ticker + huidige waarde prominent, daaronder aantal, koers en rendement als
labelparen. Boven `md` blijft de tabel exact zoals hij is. Bouw de kaartweergave
als macro in `macros.html`, zodat dashboard en grootboek dezelfde opmaak delen —
geen kopieerwerk tussen templates.

Meenemen: de `tfoot` in `dashboard.html` gebruikt nu drie verschillende
`colspan`-varianten achter `d-none`-klassen om de totaalregel uit te lijnen. Dat
is fragiel — één kolom erbij en de uitlijning klopt stil niet meer. In de
kaartweergave wordt dat een gewone totaalkaart; de tabelvariant blijft zoals hij is.

---

## P2 — Hiërarchie en oriëntatie

### 2.1 Het dashboard opent met zeven gelijkwaardige cijfers

Totale waarde, dag-P&L, ongerealiseerd, gerealiseerd, cash, XIRR en TWR staan in
twee rijen, allemaal even groot, allemaal `h4 fw-bold`. Er is geen antwoord op
"waar kijk ik als eerste". Op mobiel is de bovenste rij `col-12`, dus je scrollt
langs drie schermvullende kaarten voor je iets anders ziet.

**Opdracht:** één heronderdeel bovenaan met totale waarde en dagmutatie
(groot, met de belegd/cash-uitsplitsing die er al staat). De overige vijf cijfers
daaronder als compacte strip, met dezelfde grijze secties-koppen die
`analyse.html` al gebruikt (`text-uppercase text-muted small fw-semibold`):
**Vermogen** en **Rendement**. Op mobiel `col-6`, niet `col-12`.

De uitleg-popovers (`uitleg(...)` uit `macros.html`) blijven op elk cijfer staan —
die zijn juist waardevol nu er vier rendementsmaten naast elkaar staan.

### 2.2 Grafiek zonder tekstalternatief

De canvas heeft een `aria-label` en een fallbackzin, maar de cijfers zelf zijn
nergens leesbaar zonder muis.

**Opdracht:** onder de grafiek een ingeklapte `<details>` met een compacte tabel
van maandeindwaarden uit `waarde_serie`. Dient meteen als tekstalternatief en als
manier om een waarde af te lezen zonder te hoveren. Zet `aria-pressed` op de
periodeknoppen (1W/1M/3M/Alles) — dat is nu een knoppengroep zonder toestand.

### 2.3 Correlatiematrix loopt uit beeld

In `analyse.html` staat de matrix in een `table-responsive`, dus je scrollt
horizontaal — maar de tickerkolom scrollt mee, dus halverwege weet je niet meer
welke rij je leest.

**Opdracht:** eerste kolom `position: sticky; left: 0` met een dekkende
achtergrond. En: de cellen coderen nu alleen op kleur (`pos`/`neg`). Voeg een
tweede signaal toe — achtergrondintensiteit naar sterkte, of een `title` met de
waarde in woorden — zodat de matrix ook zonder kleurwaarneming leesbaar is.

### 2.4 Twee kaarten in een grid voor vijf

De sectie *Rendement* op de analysepagina heeft nog maar twee kaarten (TWR,
Sharpe) in een `col-lg`-grid dat voor meer is gebouwd. Ze rekken uit tot halve
schermbreedte en zien er per ongeluk uit.

**Opdracht:** vaste kolombreedte voor deze sectie, of de rendementskaarten
samenvoegen met de risicokaarten eronder tot één rij van vijf.

---

## P3 — Losse eindjes

### 3.1 Lege staten leiden nergens heen

"Nog geen posities. Voeg een broker account toe om te beginnen." — het formulier
daarvoor staat onderaan de pagina, zonder link. Zelfde patroon bij het grootboek.

**Opdracht:** elke lege staat krijgt de bijbehorende actieknop, die naar het juiste
formulier linkt of eropheen focust.

### 3.2 "Broker account toevoegen" staat altijd onderaan

Ook als je er al vijf hebt. Dat is een instelling, geen dagelijkse handeling.

**Opdracht:** inklappen achter een knop, of verplaatsen naar het gebruikersmenu in
de bovenbalk.

### 3.3 Rabo-import geeft geen feedback

In `transacties.html` submit de bestandskiezer direct bij `onchange`. Tussen kiezen
en het voorbeeldscherm gebeurt er zichtbaar niets.

**Opdracht:** spinner en "Bestand inlezen…" op de knop zodra het bestand is
gekozen, in dezelfde stijl als de `data-taak`-knoppen in `base.html`.

### 3.4 Filter op het grootboek bestaat half

`transacties.html` gebruikt `ticker_filter` in de lege staat, maar er is nergens
UI om erop te filteren.

**Opdracht:** eerst uitzoeken of de route dat filter accepteert. Zo ja: een
selectbox met de tickers van dat account boven de tabel. Zo nee: melden en de
verwijzing uit de lege staat halen.

### 3.5 Dark mode

Bootstrap 5.3 heeft `data-bs-theme` ingebouwd; een schakelaar is klein werk.

**Opdracht (alleen als de rest af is):** schakelaar in de bovenbalk, keuze in
`localStorage`, standaard de systeemvoorkeur. Let op de Chart.js-kleuren in
`dashboard.html` — as- en gridkleuren moeten meebewegen, anders krijg je zwarte
tekst op een donkere grafiek.

---

## Volgorde

1.1 → 1.2 → 2.1 → 2.2 → 2.3 → 2.4 → P3 in willekeurige volgorde.

Na elk onderdeel: `pytest` draaien, de pagina in de browser bekijken op 375 px
en op desktop, en dan pas committen.
