"""Ticker-validatie bij het toevoegen aan de volglijst."""

import app as webapp
from models import Gebruiker, Volglijst, db


def _gebruiker():
    g = Gebruiker(naam="tester")
    db.session.add(g)
    db.session.commit()
    return g


def test_exacte_ticker_wordt_toegevoegd(webclient, monkeypatch):
    g = _gebruiker()
    monkeypatch.setattr(webapp, "zoek_tickers",
                        lambda q, aantal=8: ([{"symbol": "ADBE", "naam": "Adobe Inc.",
                                               "beurs": "NASDAQ", "type": "EQUITY"}], True))
    webclient.post(f"/gebruiker/{g.id}/volglijst/toevoegen", data={"ticker": "adbe"})
    assert Volglijst.query.filter_by(ticker="ADBE").first() is not None


def test_onbekende_ticker_met_treffers_geeft_suggesties(webclient, monkeypatch):
    g = _gebruiker()
    treffers = [{"symbol": "ADBE", "naam": "Adobe Inc.", "beurs": "NASDAQ", "type": "EQUITY"},
                {"symbol": "ADB.DE", "naam": "Adobe Inc. R", "beurs": "XETRA", "type": "EQUITY"}]
    monkeypatch.setattr(webapp, "zoek_tickers", lambda q, aantal=8: (treffers, True))
    client = webclient
    with client.session_transaction() as sess:
        sess.clear()
    client.post(f"/gebruiker/{g.id}/volglijst/toevoegen", data={"ticker": "ADOBE"})
    assert Volglijst.query.filter_by(ticker="ADOBE").first() is None
    with client.session_transaction() as sess:
        suggesties = sess.get("ticker_suggesties")
    assert suggesties["invoer"] == "ADOBE"
    assert [o["symbol"] for o in suggesties["opties"]] == ["ADBE", "ADB.DE"]


def test_ticker_zonder_treffers_is_onbekend(webclient, monkeypatch):
    g = _gebruiker()
    monkeypatch.setattr(webapp, "zoek_tickers", lambda q, aantal=8: ([], True))
    client = webclient
    client.post(f"/gebruiker/{g.id}/volglijst/toevoegen", data={"ticker": "ZZZQQQ"})
    assert Volglijst.query.filter_by(ticker="ZZZQQQ").first() is None
    with client.session_transaction() as sess:
        assert sess["ticker_suggesties"]["opties"] == []


def test_netwerkfout_blokkeert_het_toevoegen_niet(webclient, monkeypatch):
    """Geen verbinding is geen bewijs dat de ticker fout is."""
    g = _gebruiker()
    monkeypatch.setattr(webapp, "zoek_tickers", lambda q, aantal=8: ([], False))
    webclient.post(f"/gebruiker/{g.id}/volglijst/toevoegen", data={"ticker": "IEMA.AS"})
    assert Volglijst.query.filter_by(ticker="IEMA.AS").first() is not None


def test_bevestigde_suggestie_slaat_de_controle_over(webclient, monkeypatch):
    g = _gebruiker()

    def niet_aanroepen(*a, **kw):
        raise AssertionError("zoek_tickers hoort niet aangeroepen te worden")

    monkeypatch.setattr(webapp, "zoek_tickers", niet_aanroepen)
    webclient.post(f"/gebruiker/{g.id}/volglijst/toevoegen",
                   data={"ticker": "ADBE", "bevestigd": "1"})
    assert Volglijst.query.filter_by(ticker="ADBE").first() is not None
