# UX-opdrachten — kant-en-klare prompts voor Codex

Elke prompt is zelfstandig: kopieer één blok, plak het in een verse Codex-sessie,
klaar. De randvoorwaarden staan bewust in elke prompt herhaald — een nieuwe sessie
kent de vorige niet.

Volgorde: 1.1 → 1.2 → 2.1 → 2.2 → 2.3 → 2.4 → P3 naar smaak.
Na elke opdracht: `pytest`, bekijken op 375 px én desktop, dan pas committen.

Basis: commit `15db0ea`, repo `dgrund70/FinancialAdvisor`.

---

## 1.1 — Nederlandse getalnotatie

```
Dit is een Flask-app (server-rendered Jinja, Bootstrap 5.3.8 via CDN, geen
buildstap, geen npm). Nederlandse UI.

Probleem: alle bedragen renderen met Engelse notatie. In dashboard.html,
analyse.html en transacties.html staat overal `"%.2f"|format(v)`, wat
€ 234567.89 oplevert in plaats van € 234.567,89.

Opdracht:
1. Voeg in app.py twee Jinja-filters toe:
   - `bedrag_nl(v, valuta='EUR')`: duizendtalpunt, decimale komma, twee
     decimalen, '—' bij None. Bij EUR '€ ' ervoor (met non-breaking space),
     bij een andere valuta de code erachter.
   - `pct_nl(v, decimalen=1)`: decimale komma, expliciet '+' bij positief,
     '—' bij None.
2. Herschrijf de macro's `eur`, `geld`, `pct` en `xirr_fmt` in dashboard.html,
   `eur`/`pct1` in analyse.html en `bedrag` in transacties.html zodat ze deze
   filters gebruiken. De macro-aanroepen in de templates blijven ongewijzigd —
   alleen de implementatie verandert.
3. Aantallen die via `compact_float` lopen (app.py regel ~149) krijgen ook
   komma-notatie. De bestaande afrondingslogica voor fractionele fondsstukken
   blijft exact zoals hij is — alleen de weergave verandert.
4. Voeg tests toe in tests/ voor beide filters: None, 0, negatief, een bedrag
   boven een miljoen, een niet-EUR valuta.

Randvoorwaarden:
- Geen Tailwind, geen npm, geen extra CDN-afhankelijkheden.
- Routes, endpointnamen en templatenamen blijven ongewijzigd.
- Wijzig in app.py alleen wat hierboven staat; meld elke andere aanpassing.
- `pytest` moet groen blijven (nu 64 tests).
```

---

## 1.2 — Tabellen bruikbaar op mobiel

```
Flask-app, server-rendered Jinja, Bootstrap 5.3.8 via CDN, geen buildstap.

Probleem: dashboard.html heeft een positietabel met 11 kolommen, transacties.html
een grootboektabel met 9. Op mobiel blijven er via d-none-klassen zes over — nog
steeds te veel voor 375 px.

Opdracht:
1. Maak in macros.html een macro voor een mobiele "regelkaart": ticker (of datum
   + type bij transacties) en het belangrijkste bedrag prominent, daaronder de
   overige velden als labelparen, en de bestaande actieknoppen (bewerken,
   verwijderen) met hun aria-labels intact.
2. Toon onder de md-breakpoint die kaartenlijst (`d-md-none`) en vanaf md de
   bestaande tabel (`d-none d-md-block`). De tabel zelf blijft inhoudelijk
   ongewijzigd.
3. Gebruik dezelfde macro in dashboard.html en transacties.html — geen
   gekopieerde opmaak tussen de twee templates.
4. De totaalregel: dashboard.html heeft nu een tfoot met drie verschillende
   colspan-varianten achter d-none-klassen. Laat die tfoot staan voor de
   tabelweergave, en maak voor de kaartweergave een aparte totaalkaart.

Randvoorwaarden:
- Alleen templates en static/style.css. app.py niet aanpassen.
- Bestaande CSRF-tokens, confirm-dialogen en aria-labels blijven behouden.
- Geen JavaScript nodig; los het op met Bootstrap-klassen.
- `pytest` groen houden.
```

---

## 2.1 — Hiërarchie in de dashboard-kopregel

```
Flask-app, server-rendered Jinja, Bootstrap 5.3.8 via CDN.

Probleem: dashboard.html opent met zeven gelijkwaardige cijfers (totale waarde,
dag-P&L, ongerealiseerd, gerealiseerd, cash, XIRR, TWR), allemaal h4 fw-bold in
twee rijen. Er is geen antwoord op "waar kijk ik eerst". Op mobiel is de bovenste
rij col-12, dus je scrollt langs drie schermvullende kaarten.

Opdracht:
1. Eén hero-kaart bovenaan: totale waarde groot, met de dagmutatie ernaast en de
   bestaande belegd/cash-uitsplitsing eronder.
2. De overige vijf cijfers daaronder als compacte strip, gegroepeerd onder twee
   sectiekopjes in de stijl die analyse.html al gebruikt
   (`text-uppercase text-muted small fw-semibold`, letter-spacing .05em):
   "Vermogen" (gerealiseerd, cash) en "Rendement" (ongerealiseerd, XIRR, TWR).
3. Op mobiel col-6 in plaats van col-12.
4. De uitleg-popovers via `uitleg(...)` uit macros.html blijven op élk cijfer
   staan — die zijn juist nodig nu er vier rendementsmaten naast elkaar staan.

Randvoorwaarden:
- Alleen dashboard.html en static/style.css. app.py niet aanpassen.
- Alle bestaande waarden blijven zichtbaar; er mag niets sneuvelen.
- De macro's kleur(), teken(), eur() en pct() blijven in gebruik.
- `pytest` groen houden.
```

---

## 2.2 — Grafiek: tekstalternatief en knoptoestand

```
Flask-app, server-rendered Jinja, Bootstrap 5.3.8, Chart.js in dashboard.html.

Probleem: de waardegrafiek is een canvas met alleen een aria-label. De cijfers
zijn nergens leesbaar zonder muis. De periodeknoppen (1W/1M/3M/Alles) zijn een
knoppengroep zonder toestandsattribuut.

Opdracht:
1. Onder de grafiek een ingeklapte <details> met een compacte tabel van
   maandeindwaarden uit `waarde_serie` (datum + waarde). Dient als
   tekstalternatief en als manier om een waarde af te lezen zonder hoveren.
2. Zet aria-pressed op de periodeknoppen en werk die bij in de bestaande
   klik-handler, naast de active-klasse die er al is.

Randvoorwaarden:
- Alleen dashboard.html. app.py niet aanpassen; `waarde_serie` bevat de data al.
- De bestaande Chart.js-configuratie en de mutatie-markers ongemoeid laten.
- `pytest` groen houden.
```

---

## 2.3 — Correlatiematrix leesbaar maken

```
Flask-app, server-rendered Jinja, Bootstrap 5.3.8.

Probleem: in analyse.html staat de correlatiematrix in een table-responsive. Bij
veel holdings scroll je horizontaal, maar de tickerkolom scrollt mee — halverwege
weet je niet meer welke rij je leest. Daarnaast coderen de cellen alleen op kleur
(klassen `pos` en `neg`), dus zonder kleurwaarneming valt er niets af te lezen.

Opdracht:
1. Maak de eerste kolom sticky (position: sticky; left: 0) met een dekkende
   achtergrond, zodat de tickernaam in beeld blijft bij horizontaal scrollen.
   Regel dat in static/style.css met een eigen klasse, niet met inline stijlen.
2. Voeg een tweede signaal toe naast kleur: achtergrondintensiteit naar sterkte
   van de correlatie, of een title-attribuut dat de waarde in woorden geeft
   ("beweegt vrijwel identiek" / "onafhankelijk" / "beweegt tegengesteld").

Randvoorwaarden:
- Alleen analyse.html en static/style.css. app.py niet aanpassen.
- De bestaande drempels blijven: > 0,6 positief, < -0,2 negatief.
- `pytest` groen houden.
```

---

## 2.4 — Rendementsblok op de analysepagina

```
Flask-app, server-rendered Jinja, Bootstrap 5.3.8.

Probleem: in analyse.html staat de sectie "Rendement" met twee kaarten (TWR,
Sharpe) in een col-lg-grid dat voor meer kaarten is gebouwd. Ze rekken uit tot
halve schermbreedte en zien er per ongeluk uit.

Opdracht: geef deze twee kaarten een vaste kolombreedte, óf voeg de sectie
"Rendement" samen met de sectie "Risico" eronder tot één rij van vijf kaarten met
één sectiekopje. Kies de tweede optie als dat de pagina rustiger maakt; licht je
keuze toe in het commitbericht.

Randvoorwaarden:
- Alleen analyse.html. app.py niet aanpassen.
- Alle bestaande waarden en uitleg-popovers blijven staan.
- `pytest` groen houden.
```

---

## 3.1 — Lege staten met een actieknop

```
Flask-app, server-rendered Jinja, Bootstrap 5.3.8.

Probleem: de lege staten verwijzen naar een handeling maar bieden geen knop.
dashboard.html zegt "Voeg een broker account toe om te beginnen" terwijl het
formulier onderaan de pagina staat, zonder link. transacties.html en
analyse.html hebben hetzelfde patroon.

Opdracht: geef elke lege staat de bijbehorende actieknop, die naar het juiste
formulier linkt of eropheen focust (anchor + autofocus is voldoende, geen JS-
scrollanimatie).

Randvoorwaarden:
- Alleen templates. app.py niet aanpassen.
- Bestaande teksten mogen korter, maar blijven Nederlands en concreet.
- `pytest` groen houden.
```

---

## 3.2 — "Broker account toevoegen" verplaatsen

```
Flask-app, server-rendered Jinja, Bootstrap 5.3.8.

Probleem: onderaan dashboard.html staat permanent een kaart "Broker account
toevoegen", ook als de gebruiker er al vijf heeft. Dat is een instelling, geen
dagelijkse handeling.

Opdracht: klap het formulier in achter een knop ("Broker account toevoegen…",
Bootstrap collapse), óf verplaats het naar het gebruikersmenu in de bovenbalk van
base.html, naast "Gebruiker toevoegen…". Kies één van beide en licht je keuze toe
in het commitbericht.

Randvoorwaarden:
- Het POST-formulier naar `account_toevoegen` blijft functioneel identiek,
  inclusief CSRF-token.
- app.py niet aanpassen.
- `pytest` groen houden.
```

---

## 3.3 — Feedback bij de Rabo-import

```
Flask-app, server-rendered Jinja, Bootstrap 5.3.8.

Probleem: in transacties.html opent de knop "Rabo Zakelijk Import" een verborgen
file-input die bij onchange direct submit. Tussen bestandskeuze en het
voorbeeldscherm gebeurt er zichtbaar niets, dus bij een groot bestand lijkt de
app te hangen.

Opdracht: zodra een bestand is gekozen, wordt de knop uitgeschakeld en toont hij
een spinner met "Bestand inlezen…", in dezelfde stijl als de data-taak-knoppen in
base.html (spinner-border spinner-border-sm, aria-busy).

Randvoorwaarden:
- Alleen transacties.html (en base.html als je de bestaande helper hergebruikt).
- app.py en de importlogica niet aanpassen.
- De upload blijft een gewone form-submit; geen fetch/XHR.
- `pytest` groen houden.
```

---

## 3.4 — Filter op het grootboek

```
Flask-app, server-rendered Jinja, Bootstrap 5.3.8.

Probleem: transacties.html gebruikt de variabele `ticker_filter` in zijn lege
staat ("Nog geen transacties voor X"), maar er is nergens UI om op een ticker te
filteren.

Opdracht:
1. Zoek eerst uit of de route `transacties_overzicht` in app.py een
   ticker-parameter accepteert.
2. Zo ja: voeg boven de tabel een selectbox toe met de tickers die in dat account
   voorkomen, plus "Alle transacties". Submit via GET, zodat de gefilterde weergave
   een deelbare URL heeft.
3. Zo nee: meld dat, implementeer de parameter in de route (querystring, met
   validatie dat de ticker bij dat account hoort), voeg een test toe, en bouw dan
   de selectbox.

Randvoorwaarden:
- Als je app.py aanpast: alleen deze route, en met een test in tests/.
- Endpointnaam en bestaande URL blijven werken zonder parameter.
- `pytest` groen houden.
```

---

## 3.5 — Dark mode (pas als de rest af is)

```
Flask-app, server-rendered Jinja, Bootstrap 5.3.8 via CDN, Chart.js in
dashboard.html.

Opdracht: voeg een licht/donker-schakelaar toe met het ingebouwde
data-bs-theme-mechanisme van Bootstrap 5.3.

1. Schakelaar in de bovenbalk van base.html, naast het gebruikersmenu.
2. Keuze opslaan in localStorage; standaard de systeemvoorkeur
   (prefers-color-scheme). Zet het thema vóór het renderen van de body om
   flitsen te voorkomen.
3. Belangrijk: de Chart.js-configuratie in dashboard.html gebruikt vaste
   kleuren voor assen, grid en tooltips. Laat die meebewegen met het thema,
   anders krijg je zwarte tekst op een donkere grafiek.
4. Controleer de eigen klassen in static/style.css (.pos, .neg, .icon-action,
   .mutatie-legenda) op leesbaarheid in beide thema's.

Randvoorwaarden:
- Geen extra CDN-afhankelijkheden.
- app.py niet aanpassen.
- `pytest` groen houden.
```
