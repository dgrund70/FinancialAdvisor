"""Compacte context voor een losse vraag aan het team."""

import advies_vraag


def test_genoemde_tickers_matcht_op_heel_woord():
    kandidaten = ["PLTR", "VWCE.AS", "MP", "NOW"]
    gevonden = advies_vraag.genoemde_tickers("Moet ik PLTR nu verkopen?", kandidaten)
    assert gevonden == ["PLTR"]


def test_genoemde_tickers_negeert_hoofdletters_en_beurssuffix():
    assert advies_vraag.genoemde_tickers("wat vind je van vwce?", ["VWCE.AS"]) == ["VWCE.AS"]


def test_genoemde_tickers_matcht_niet_binnen_een_woord():
    # 'NOW' mag niet matchen in 'nogmaals' of 'nowhere'.
    assert advies_vraag.genoemde_tickers("Is dit nowhere goed?", ["NOW"]) == []


def test_macro_wordt_alleen_bij_een_marktvraag_opgehaald():
    woorden = advies_vraag._MACRO_WOORDEN
    assert any(w in "wat doet de rente met mijn portefeuille".lower() for w in woorden)
    # 'euro' is bewust géén trigger: dat woord staat in vrijwel elke geldvraag.
    assert not any(w in "ik heb 1000 euro extra, wat nu".lower() for w in woorden)
