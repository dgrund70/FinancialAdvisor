"""Tests voor de ontleding van adviesteksten (advies_parser)."""
import advies_parser as ap

DAAM = """## Samenvatting

Je portefeuille bestaat volledig uit VWCE. De belangrijkste actie is nu
**zinvolle spreiding toevoegen** richting opkomende markten.

---

## Huidige posities

### VWCE.AS — **Houden / Gefaseerd bijkopen**

Motivatie hier.

## Nieuwe kansen

#### 🔵 IEMA.AS — iShares Core MSCI Emerging Markets IMI ETF
**Advies: Kopen (kleine positie, gefaseerd)**

- Belegt in opkomende markten

## Risico's

Iets over risico.

## Macro & Geopolitiek

Iets over macro.
"""

OLIVIER = """## Samenvatting

Eerste zin hier. Tweede zin.

## Huidige posities

### 🔴 QBTS (D-Wave Quantum) — **VOLLEDIG VERKOPEN**

Toelichting.

### 🟢 NOW (ServiceNow) — **HOUDEN**, eventueel licht bijkopen

Toelichting.
"""


def test_secties_splitst_op_dubbele_hekjes():
    s = ap.secties(DAAM)
    assert [x["titel"] for x in s] == [
        "Samenvatting", "Huidige posities", "Nieuwe kansen", "Risico's", "Macro & Geopolitiek"]
    assert [x["slug"] for x in s][:3] == ["samenvatting", "huidige-posities", "nieuwe-kansen"]


def test_risico_en_macro_staan_standaard_dicht():
    open_per_titel = {x["titel"]: x["open"] for x in ap.secties(DAAM)}
    assert open_per_titel["Samenvatting"] is True
    assert open_per_titel["Huidige posities"] is True
    assert open_per_titel["Nieuwe kansen"] is True
    assert open_per_titel["Risico's"] is False
    assert open_per_titel["Macro & Geopolitiek"] is False


def test_sectie_zonder_koppen_valt_terug_op_een_geheel():
    s = ap.secties("Gewoon wat tekst zonder koppen.")
    assert len(s) == 1 and s[0]["open"] is True


def test_lege_tekst_geeft_lege_lijst():
    assert ap.secties("") == []
    assert ap.acties("") == []
    assert ap.kernboodschap("") == ""


def test_kernboodschap_pakt_de_zin_met_de_belangrijkste_actie():
    assert ap.kernboodschap(DAAM).startswith("De belangrijkste actie is nu")
    assert "**" not in ap.kernboodschap(DAAM)


def test_kernboodschap_valt_terug_op_de_eerste_zin():
    assert ap.kernboodschap(OLIVIER) == "Eerste zin hier."


def test_acties_leest_beide_schrijfwijzen():
    per_ticker = {a["ticker"]: a for a in ap.acties(DAAM)}
    assert per_ticker["IEMA.AS"]["verdict"] == "Kopen"
    assert per_ticker["IEMA.AS"]["soort"] == "kopen"
    # Oordeel in de kop, met een koppelteken in de naam ertussen.
    per_ticker = {a["ticker"]: a for a in ap.acties(OLIVIER)}
    assert per_ticker["QBTS"]["soort"] == "verkopen"
    assert per_ticker["NOW"]["soort"] == "houden"


def test_houden_wint_van_bijkopen_in_dezelfde_regel():
    a = {x["ticker"]: x for x in ap.acties(DAAM)}["VWCE.AS"]
    assert a["verdict"] == "Houden"
    assert a["soort"] == "houden"


def test_hoofdacties_zet_beslissingen_vooraan():
    soorten = [a["soort"] for a in ap.hoofdacties(OLIVIER, maxaantal=2)]
    assert soorten[0] == "verkopen"
