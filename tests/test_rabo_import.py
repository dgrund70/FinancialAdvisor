"""Tests voor de Rabo Zakelijk Import (parser + signaturen).

Het echte bewijs zat in de vergelijking met de handgeschreven seed-scripts:
dezelfde CSV levert dezelfde aantallen, GAK's en cashstand op. Deze tests
houden de onderdelen vast die makkelijk stilletjes kapotgaan — de
Nederlandse getalnotatie, de datumconventie, de uitvoeringsvolgorde en de
weigeringen.
"""
from datetime import date

import pytest

import rabo_import


KOP = ("Portefeuille;Naam;Datum;Type mutatie;Transactie Eenheid/Valuta;Volume;"
       "Koers valuta;Prijs;Transactie valuta;Waarde in valuta;Bedrag in valuta;"
       "Transactie bedrag;Valuta kosten €;Opgelopen rente;Dividendbelasting;"
       "Transactiebelasting;Isin code;Beurs;Tijd")

KOOP = ("42272386;1895 Obl Bedrijven;02-09-2026;Koop fondsen;st;251,7445;EUR;"
        "78,5625;EUR;19.777,68;EUR;-19.777,68;-;;-;-;NL0015436049;"
        "Clearstream - Vestima;11:37:22")
VERKOOP = ("42272386;1895 Obl Bedrijven;02-09-2026;Verkoop fondsen;st;3,9383;EUR;"
           "78,5625;EUR;309,4;EUR;309,4;-;;-;-;NL0015436049;"
           "Clearstream - Vestima;11:48:24")
STORTING = ("42272386;-;28-08-2026;Storting / opname;;0;;0;;0;EUR;50.000;"
            "-;-;-;-;;;06:26:53")


def lees(*rijen):
    return rabo_import.parse("\n".join((KOP,) + rijen).encode("utf-8"))


def test_nederlandse_getalnotatie():
    # punt is duizendtal, komma is decimaal -- '50.000' is vijftigduizend
    assert rabo_import._getal("19.777,68") == pytest.approx(19777.68)
    assert rabo_import._getal("50.000") == pytest.approx(50000.0)
    assert rabo_import._getal("-3.175,6") == pytest.approx(-3175.6)
    assert rabo_import._getal("-") is None
    assert rabo_import._getal("") is None


def test_koop_prijs_uit_bedrag_gedeeld_door_aantal():
    """Niet de afgeronde kolom Prijs, anders sluit de kasstroom niet aan."""
    (regel,), _ = lees(KOOP)
    assert regel["type"] == "koop"
    assert regel["ticker"] == "0P0001KOL0.F"
    assert regel["aantal"] == pytest.approx(251.7445)
    assert regel["aantal"] * regel["prijs"] == pytest.approx(19777.68, abs=0.005)


def test_fondsmutatie_krijgt_effnota_datum_storting_niet():
    regels, _ = lees(KOOP, STORTING)
    per_type = {r["type"]: r for r in regels}
    assert per_type["koop"]["boek_datum"] == date(2026, 9, 3)     # dag ná 02-09
    assert per_type["storting"]["boek_datum"] == date(2026, 8, 28)  # dag zelf


def test_uitvoeringsvolgorde_blijft_staan():
    """Koop vóór verkoop binnen dezelfde dag: de GAK-berekening hangt ervan af."""
    regels, _ = lees(VERKOOP, KOOP)   # bewust omgekeerd aangeboden
    assert [r["type"] for r in regels] == ["koop", "verkoop"]


def test_kosten_worden_uit_de_prijs_gehaald():
    rij = ("42272386;1895 Obl Bedrijven;02-10-2026;Koop fondsen;st;100;EUR;80;"
           "EUR;8.010;EUR;-8.010;10,00;;-;-;NL0015436049;X;10:00:00")
    (regel,), _ = lees(rij)
    assert regel["kosten"] == pytest.approx(10.0)
    assert regel["prijs"] == pytest.approx(80.0)


def test_identieke_stortingen_blijven_apart():
    """De inleg van 28-08 stond als drie losse boekingen van 50.000."""
    regels, _ = lees(STORTING, STORTING, STORTING)
    assert len(regels) == 3
    assert len({rabo_import.regel_signatuur(r) for r in regels}) == 1


def test_weigert_andere_portefeuille():
    with pytest.raises(rabo_import.ImportFout, match="99999999"):
        lees(KOOP.replace("42272386", "99999999", 1))


def test_weigert_onbekend_fonds():
    with pytest.raises(rabo_import.ImportFout, match="NL0099999999"):
        lees(KOOP.replace("NL0015436049", "NL0099999999"))


def test_weigert_verkeerd_bestandsformaat():
    with pytest.raises(rabo_import.ImportFout, match="kolommen"):
        rabo_import.parse(b"Naam;Aantal;Waarde\n1895 Obl Bedrijven;416,0933;32689,33")


def test_weigert_leeg_bestand():
    with pytest.raises(rabo_import.ImportFout, match="leeg"):
        rabo_import.parse(b"")


def test_onbekende_mutatiesoort_wordt_gemeld_niet_genegeerd():
    with pytest.raises(rabo_import.ImportFout):
        lees(KOOP.replace("Koop fondsen", "Splitsing"))


def test_signatuur_van_regel_en_transactie_komen_overeen():
    """Anders herkent de import zijn eigen eerdere boeking niet als dubbel."""
    class NepTransactie:
        type, ticker = "koop", "0P0001KOL0.F"
        aantal, prijs, kosten, bedrag = 251.7445, 19777.68 / 251.7445, 0.0, None
        datum = date(2026, 9, 3)

    (regel,), _ = lees(KOOP)
    assert (rabo_import.regel_signatuur(regel)
            == rabo_import.transactie_signatuur(NepTransactie()))
