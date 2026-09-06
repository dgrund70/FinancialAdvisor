"""Regressietests voor validatie op de Flask-routes en importbevestiging."""

from datetime import date

import pytest
from itsdangerous import BadData
from werkzeug.datastructures import MultiDict

import app as webapp
from models import BrokerAccount, Gebruiker, Transactie, db


def _account():
    gebruiker = Gebruiker(naam="test")
    db.session.add(gebruiker)
    db.session.flush()
    account = BrokerAccount(naam="account", gebruiker_id=gebruiker.id)
    db.session.add(account)
    db.session.flush()
    return account


def _waarden(soort, aantal, datum):
    return {
        "type": soort, "ticker": "X", "aantal": aantal, "prijs": 100.0,
        "bedrag": None, "kosten": 0.0, "valuta": "EUR", "datum": datum,
        "notitie": None,
    }


def test_teruggedateerde_verkoop_wordt_geweigerd(app_ctx):
    account = _account()
    db.session.add(Transactie(
        broker_account_id=account.id, type="koop", ticker="X", aantal=10,
        prijs=100, kosten=0, valuta="EUR", datum=date(2024, 2, 1)))
    db.session.flush()

    fout = webapp._valideer_aandelenverloop(
        account.id, _waarden("verkoop", 5, date(2024, 1, 1)))
    assert "Niet genoeg X" in fout


def test_bewerking_kan_latere_verkoop_niet_ongedekt_maken(app_ctx):
    account = _account()
    koop = Transactie(
        broker_account_id=account.id, type="koop", ticker="X", aantal=10,
        prijs=100, kosten=0, valuta="EUR", datum=date(2024, 1, 1))
    verkoop = Transactie(
        broker_account_id=account.id, type="verkoop", ticker="X", aantal=8,
        prijs=110, kosten=0, valuta="EUR", datum=date(2024, 2, 1))
    db.session.add_all([koop, verkoop])
    db.session.flush()

    fout = webapp._valideer_aandelenverloop(
        account.id, _waarden("koop", 5, date(2024, 1, 1)), koop.id)
    assert "max 5" in fout


@pytest.mark.parametrize("veld,waarde", [
    ("aantal", "nan"), ("prijs", "inf"), ("kosten", "-1"),
])
def test_transactie_weigert_onveilige_getallen(app_ctx, veld, waarde):
    account = _account()
    form = MultiDict({
        "type": "koop", "ticker": "X", "aantal": "1", "prijs": "10",
        "kosten": "0", "valuta": "EUR", "datum": "2024-01-01",
    })
    form[veld] = waarde
    _, fouten = webapp._parse_transactie_form(form, account)
    assert fouten


def test_rabo_payload_is_signed_and_tampering_fails(app_ctx):
    webapp.app.config["SECRET_KEY"] = "test-secret"
    regels = [{
        "boek_datum": date(2024, 1, 2), "bron_datum": date(2024, 1, 1),
        "type": "storting", "ticker": None, "fonds": "", "aantal": None,
        "prijs": None, "bedrag": 100.0, "kosten": 0.0, "valuta": "EUR",
        "tijd": "10:00:00", "notitie": "test",
    }]
    token = webapp._rabo_serialiseer(regels)
    assert webapp._rabo_deserialiseer(token)[0]["bedrag"] == 100.0
    ander = token[:-1] + ("a" if token[-1] != "a" else "b")
    with pytest.raises(BadData):
        webapp._rabo_deserialiseer(ander)
