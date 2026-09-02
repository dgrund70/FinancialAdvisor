"""Eenmalig invoerscript: de tweede inleg op de Rabobank zakelijke
beleggersrekening (Kineoo B.V.), augustus/september 2026.

Bron: Mutatieoverzicht_42272386_02072026_01092026.csv.

  28-08-2026  3 x EUR 50.000 gestort  = EUR 150.000
  01-09-2026  3 aankopen (alleen de aandelenfondsen) = EUR 104.143,33

Anders dan bij de eerdere batches staan de aantallen (kolom Volume) dit keer
wel in de bankmutaties, dus een portefeuille-export is niet nodig.

De obligatiefondsen zijn op de peildatum van dit overzicht nog niet bijgekocht;
er blijft EUR 46.568,78 cash staan. Dat komt vrijwel exact overeen met wat er
nodig is om terug te komen op de doelverhouding 69,7/30,3 -- die aankopen
worden apart geboekt zodra ze op het afschrift staan.

Datumconventie volgt de bestaande boekingen: fondsmutaties op de dag na de
datum in het mutatieoverzicht (eff.nota-datum), stortingen op de dag zelf.

Idempotent: stopt als er al transacties op 02-09-2026 in dit account staan.

Draaien:
    .venv/bin/python seed_inleg_20260901.py
"""
from datetime import date

from app import app, db
from models import Gebruiker, BrokerAccount, Transactie
from projectie import na_transactie_wijziging, cash_saldi

NAAM_GEBRUIKER = "Zakelijk (Kineoo)"
NAAM_ACCOUNT   = "Rabobank Zakelijk"
DATUM_STORTING = date(2026, 8, 28)
DATUM_KOOP     = date(2026, 9, 2)

STORTINGEN = [50000.0, 50000.0, 50000.0]   # 3 losse boekingen, zo staan ze op het afschrift

# (ticker, aantal, prijs, bedrag, omschrijving uit de mutatieregel)
AANKOPEN = [
    ("0P0001IT6X.F", 273.4431, 190.4641, 52081.09, "NL0014065450 1895 Aand Ind Wereld"),
    ("0P0001LI9F.F", 165.4580, 157.0226, 25980.65, "NL00150004M2 1895 Aand Opp Macro"),
    ("0P0001JET8.F", 114.9095, 226.9750, 26081.59, "NL0014270340 1895 Aand Multifact"),
]

VERWACHT_AANTAL = {
    "0P0001IT6X.F": 456.9509,
    "0P0001JET8.F": 191.9703,
    "0P0001LI9F.F": 276.5078,
    "0P0001KOL0.F": 168.2871,
    "0P0001UMPU.F":  45.2928,
    "0P0001K5V2.F":  75.0643,
    "0P0001L6VX.F":  47.2198,
    "0P0001UR5B.F":  21.6211,
}
VERWACHTE_CASH = 46568.78


def main():
    with app.app_context():
        gebruiker = Gebruiker.query.filter_by(naam=NAAM_GEBRUIKER).first()
        if gebruiker is None:
            print(f"Gebruiker '{NAAM_GEBRUIKER}' bestaat niet. "
                  "Draai eerst seed_zakelijk_kineoo.py.")
            return

        account = BrokerAccount.query.filter_by(
            naam=NAAM_ACCOUNT, gebruiker_id=gebruiker.id).first()
        if account is None:
            print(f"Account '{NAAM_ACCOUNT}' bestaat niet. "
                  "Draai eerst seed_zakelijk_kineoo.py.")
            return

        al_gedaan = Transactie.query.filter_by(
            broker_account_id=account.id, datum=DATUM_KOOP).count()
        if al_gedaan:
            print(f"Er staan al {al_gedaan} transactie(s) op {DATUM_KOOP} in dit "
                  "account. Niets toegevoegd.")
            print("Cash saldo:", cash_saldi(account.id))
            return

        for bedrag in STORTINGEN:
            db.session.add(Transactie(
                broker_account_id=account.id,
                type="storting",
                bedrag=bedrag,
                kosten=0.0,
                valuta="EUR",
                datum=DATUM_STORTING,
                notitie="Storting Kineoo B.V. naar beleggersrekening (2e inleg, 28-08-2026)",
            ))

        for ticker, aantal, prijs, bedrag, notitie in AANKOPEN:
            # prijs op volle precisie, zodat de kasstroom exact op de bank aansluit
            db.session.add(Transactie(
                broker_account_id=account.id,
                type="koop",
                ticker=ticker,
                aantal=aantal,
                prijs=bedrag / aantal,
                kosten=0.0,
                valuta="EUR",
                datum=DATUM_KOOP,
                notitie=f"Eff.nota koop fondsen {notitie} (inleg 01-09-2026)",
            ))

        db.session.flush()
        for ticker, *_ in AANKOPEN:
            na_transactie_wijziging(account.id, ticker)
        db.session.commit()

        print("\nInleg verwerkt. Resultaat:")
        print("Cash saldo:", cash_saldi(account.id),
              f" (verwacht: EUR {VERWACHTE_CASH:,.2f})")
        fout = False
        for pos in sorted(account.posities, key=lambda p: p.ticker):
            verwacht = VERWACHT_AANTAL.get(pos.ticker)
            regel = "" if verwacht is None else f"  verwacht={verwacht:>10.4f}"
            if verwacht is not None and abs(pos.aantal - verwacht) > 0.0005:
                regel += "  <-- AFWIJKING"
                fout = True
            print(f"  {pos.ticker:15s} aantal={pos.aantal:>10.4f}  "
                  f"GAK={pos.aankoopprijs:>9.4f}{regel}")
        if fout:
            print("\nLet op: aantallen wijken af van het mutatieoverzicht.")


if __name__ == "__main__":
    main()
