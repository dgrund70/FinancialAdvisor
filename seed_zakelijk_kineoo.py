"""Eenmalig invoerscript: nieuw profiel + account + grootboek voor de
Rabobank zakelijke beleggersrekening (Kineoo B.V.), o.b.v. het CSV-export
(Portefeuille_42272386_18-07-2026.csv) en de bankmutaties van 10 en 15 juli 2026.

Idempotent: op naam zoeken vóór aanmaken, dus veilig meerdere keren te draaien.
Gebruikt dezelfde functies (na_transactie_wijziging/herbereken_positie) als de
app zelf — het grootboek (Transactie) is de bron van waarheid, Positie wordt
er automatisch uit afgeleid.

Draaien:
    .venv/bin/python seed_zakelijk_kineoo.py
"""
from datetime import date

from app import app, db
from models import Gebruiker, BrokerAccount, Transactie
from projectie import na_transactie_wijziging, cash_saldi

NAAM_GEBRUIKER = "Zakelijk (Kineoo)"
NAAM_ACCOUNT   = "Rabobank Zakelijk"

# (yfinance-ticker, aantal, GAK, ISIN + omschrijving uit de eff.nota)
AANKOPEN = [
    ("0P0001IT6X.F", 185.9905, 188.13, "NL0014065450 1895 Aand Ind Wereld 61-109836769"),
    ("0P0001JET8.F",  78.5452, 223.48, "NL0014270340 1895 Aand Multifact 61-109836788"),
    ("0P0001LI9F.F", 111.898,  156.47, "NL00150004M2 1895 Aand Opp Macro 61-109836765"),
    ("0P0001KOL0.F", 163.1466,  79.09, "NL0015436049 1895 Obl Bedrijven 61-109836801"),
    ("0P0001UMPU.F",  44.0759, 101.14, "NL0015002BM8 1895 Obl Short Dur 61-109836775"),
    ("0P0001K5V2.F",  72.6338,  83.23, "NL0014857104 1895 Obl Ind Euro F 61-109836794"),
    ("0P0001L6VX.F",  45.7498,  82.40, "NL0015602376 1895 Obl Inv. Grade 61-109836782"),
    ("0P0001UR5B.F",  20.9845,  99.07, "NL0015002BS5 1895 Obl Spec. Proj 61-109836760"),
]


def main():
    with app.app_context():
        gebruiker = Gebruiker.query.filter_by(naam=NAAM_GEBRUIKER).first()
        if gebruiker is None:
            gebruiker = Gebruiker(naam=NAAM_GEBRUIKER)
            db.session.add(gebruiker)
            db.session.flush()
            print(f"Gebruiker aangemaakt: '{NAAM_GEBRUIKER}' (id={gebruiker.id})")
        else:
            print(f"Gebruiker bestaat al: '{NAAM_GEBRUIKER}' (id={gebruiker.id})")

        account = BrokerAccount.query.filter_by(
            naam=NAAM_ACCOUNT, gebruiker_id=gebruiker.id).first()
        if account is None:
            account = BrokerAccount(naam=NAAM_ACCOUNT, gebruiker_id=gebruiker.id)
            db.session.add(account)
            db.session.flush()
            print(f"Account aangemaakt: '{NAAM_ACCOUNT}' (id={account.id})")
        else:
            print(f"Account bestaat al: '{NAAM_ACCOUNT}' (id={account.id})")

        bestaande = Transactie.query.filter_by(broker_account_id=account.id).count()
        if bestaande:
            print(f"Let op: dit account heeft al {bestaande} transactie(s). "
                  "Script stopt om dubbele invoer te voorkomen.")
            return

        # 1) Storting — interne overboeking Kineoo B.V. eigen rekening naar de
        #    beleggersrekening, 10-07-2026.
        db.session.add(Transactie(
            broker_account_id=account.id,
            type="storting",
            bedrag=100000.00,
            valuta="EUR",
            datum=date(2026, 7, 10),
            notitie="Interne overboeking Kineoo B.V. (NL97RABO0129338788) "
                     "naar beleggersrekening",
        ))

        # 2) Aankopen fondsen — 15-07-2026, kosten €0 (bank bevestigt geen
        #    aparte transactiekosten).
        for ticker, aantal, prijs, notitie in AANKOPEN:
            db.session.add(Transactie(
                broker_account_id=account.id,
                type="koop",
                ticker=ticker,
                aantal=aantal,
                prijs=prijs,
                kosten=0.0,
                valuta="EUR",
                datum=date(2026, 7, 15),
                notitie=f"Eff.nota koop fondsen {notitie}",
            ))

        db.session.flush()
        for ticker, *_ in AANKOPEN:
            na_transactie_wijziging(account.id, ticker)
        db.session.commit()

        print("\nGrootboek verwerkt. Resultaat:")
        print("Cash saldo:", cash_saldi(account.id))
        for pos in sorted(account.posities, key=lambda p: p.ticker):
            print(f"  {pos.ticker:15s} aantal={pos.aantal:>10.4f}  "
                  f"GAK={pos.aankoopprijs:>8.2f}  gerealiseerd={pos.gerealiseerde_winst:.2f}")


if __name__ == "__main__":
    main()
