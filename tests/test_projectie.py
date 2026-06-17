"""Tests voor de average-cost projectie en cash-saldi."""

from datetime import date

from models import db, BrokerAccount, Gebruiker, Positie, Transactie
import projectie


def _account():
    g = Gebruiker(naam="t")
    db.session.add(g)
    db.session.flush()
    a = BrokerAccount(naam="acc", gebruiker_id=g.id)
    db.session.add(a)
    db.session.flush()
    return a


def _tx(account, **kw):
    kw.setdefault("kosten", 0.0)
    kw.setdefault("valuta", "EUR")
    db.session.add(Transactie(broker_account_id=account.id, **kw))


def _pos(account, ticker="X"):
    return Positie.query.filter_by(broker_account_id=account.id, ticker=ticker).one()


def test_average_cost_and_realized(app_ctx):
    a = _account()
    _tx(a, type="koop", ticker="X", aantal=10, prijs=100, kosten=5, datum=date(2024, 1, 1))
    _tx(a, type="koop", ticker="X", aantal=10, prijs=120, kosten=5, datum=date(2024, 1, 2))
    db.session.flush()
    projectie.herbereken_positie(a.id, "X")
    pos = _pos(a)
    assert abs(pos.aantal - 20) < 1e-6
    assert abs(pos.aankoopprijs - 110.5) < 1e-6        # (1000+5+1200+5)/20
    assert abs(pos.gerealiseerde_winst - 0) < 1e-6

    _tx(a, type="verkoop", ticker="X", aantal=5, prijs=130, kosten=2, datum=date(2024, 1, 3))
    db.session.flush()
    projectie.herbereken_positie(a.id, "X")
    pos = _pos(a)
    assert abs(pos.aantal - 15) < 1e-6
    assert abs(pos.aankoopprijs - 110.5) < 1e-6        # GAK ongewijzigd na verkoop
    assert abs(pos.gerealiseerde_winst - 95.5) < 1e-6  # 5*130 - 5*110.5 - 2


def test_full_sell_then_rebuy_resets_gak(app_ctx):
    a = _account()
    _tx(a, type="koop", ticker="X", aantal=10, prijs=100, datum=date(2024, 1, 1))
    _tx(a, type="verkoop", ticker="X", aantal=10, prijs=110, datum=date(2024, 1, 2))
    _tx(a, type="koop", ticker="X", aantal=5, prijs=50, datum=date(2024, 1, 3))
    db.session.flush()
    projectie.herbereken_positie(a.id, "X")
    pos = _pos(a)
    assert abs(pos.aantal - 5) < 1e-6
    assert abs(pos.aankoopprijs - 50) < 1e-6           # verse GAK na volledige verkoop
    assert abs(pos.gerealiseerde_winst - 100) < 1e-6   # 10*110 - 10*100


def test_correctie_rebaselines(app_ctx):
    a = _account()
    _tx(a, type="koop", ticker="X", aantal=10, prijs=100, datum=date(2024, 1, 1))
    _tx(a, type="correctie", ticker="X", aantal=8, prijs=90, datum=date(2024, 1, 2))
    db.session.flush()
    projectie.herbereken_positie(a.id, "X")
    pos = _pos(a)
    assert abs(pos.aantal - 8) < 1e-6
    assert abs(pos.aankoopprijs - 90) < 1e-6


def test_cash_saldi(app_ctx):
    a = _account()
    _tx(a, type="storting", bedrag=5000, datum=date(2024, 1, 1))
    _tx(a, type="koop", ticker="X", aantal=10, prijs=100, kosten=5, datum=date(2024, 1, 2))
    _tx(a, type="verkoop", ticker="X", aantal=5, prijs=130, kosten=2, datum=date(2024, 1, 3))
    _tx(a, type="dividend", ticker="X", bedrag=20, datum=date(2024, 1, 4))
    _tx(a, type="opname", bedrag=100, datum=date(2024, 1, 5))
    db.session.flush()
    saldi = projectie.cash_saldi(a.id)
    # 5000 - 1005 + 648 + 20 - 100 = 4563
    assert abs(saldi["EUR"] - 4563) < 1e-6


def test_delete_all_share_tx_removes_position(app_ctx):
    a = _account()
    _tx(a, type="koop", ticker="X", aantal=10, prijs=100, datum=date(2024, 1, 1))
    db.session.flush()
    projectie.herbereken_positie(a.id, "X")
    assert Positie.query.filter_by(broker_account_id=a.id, ticker="X").count() == 1
    Transactie.query.filter_by(broker_account_id=a.id, ticker="X").delete()
    db.session.flush()
    projectie.herbereken_positie(a.id, "X")
    assert Positie.query.filter_by(broker_account_id=a.id, ticker="X").count() == 0
