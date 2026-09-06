"""Eenmalig invoerscript: de obligatie-aankoop + rebalance op de Rabobank
zakelijke beleggersrekening (Kineoo B.V.), handelsdatum 02-09-2026.

Bron: Mutatieoverzicht_42272386_06072026_05092026.csv.

Dit is de boeking waar het vorige script (seed_inleg_20260901.py) op wachtte:
Rabo belegt nu de cash die na de tweede inleg bleef staan. In dezelfde batch
zit een kleine rebalance -- daarom staan er bij vier fondsen zowel een koop
als een verkoop op dezelfde dag, tegen dezelfde koers.

  8 aankopen   EUR 55.462,41
  4 verkopen   EUR 10.673,09
  netto        EUR 44.789,32  -> cash 46.568,78 - 44.789,32 = EUR 1.779,46

Opvallend: 1895 Obl Ind Euro F wordt per saldo juist afgebouwd
(+112,1740 / -122,7721 = -10,5981 st).

Datumconventie volgt de bestaande boekingen: fondsmutaties op de dag na de
datum in het mutatieoverzicht (eff.nota-datum), dus 03-09-2026.
Volgorde van invoer = volgorde van uitvoering (kolom Tijd), zodat de
average-cost-berekening de werkelijke koop-voor-verkoop-volgorde volgt.

Idempotent: stopt als er al transacties op 03-09-2026 in dit account staan.

Draaien:
    .venv/bin/python seed_rebalance_20260903.py
"""
from datetime import date

from app import app, db
from models import Gebruiker, BrokerAccount, Transactie
from projectie import na_transactie_wijziging, cash_saldi

NAAM_GEBRUIKER = "Zakelijk (Kineoo)"
NAAM_ACCOUNT   = "Rabobank Zakelijk"
DATUM          = date(2026, 9, 3)

# (type, ticker, aantal, bedrag, omschrijving) -- op volgorde van uitvoeringstijd
MUTATIES = [
    ("koop",    "0P0001KOL0.F", 251.7445, 19777.68, "NL0015436049 1895 Obl Bedrijven"),
    ("koop",    "0P0001UR5B.F",  32.3165,  3175.60, "NL0015002BS5 1895 Obl Spec. Proj"),
    ("koop",    "0P0001L6VX.F",  70.6376,  5783.57, "NL0015602376 1895 Obl Inv. Grade"),
    ("koop",    "0P0001UMPU.F",  67.4382,  6821.45, "NL0015002BM8 1895 Obl Short Dur"),
    ("koop",    "0P0001K5V2.F", 112.1740,  9236.87, "NL0014857104 1895 Obl Ind Euro F"),
    ("verkoop", "0P0001KOL0.F",   3.9383,   309.40, "NL0015436049 1895 Obl Bedrijven"),
    ("verkoop", "0P0001UMPU.F",   1.5158,   153.32, "NL0015002BM8 1895 Obl Short Dur"),
    ("verkoop", "0P0001L6VX.F",   1.2312,   100.81, "NL0015602376 1895 Obl Inv. Grade"),
    ("koop",    "0P0001IT6X.F",   3.7367,   708.71, "NL0014065450 1895 Aand Ind Wereld"),
    ("koop",    "0P0001LI9F.F",  47.1259,  7376.07, "NL00150004M2 1895 Aand Opp Macro"),
    ("koop",    "0P0001JET8.F",  11.3994,  2582.46, "NL0014270340 1895 Aand Multifact"),
    ("verkoop", "0P0001K5V2.F", 122.7721, 10109.56, "NL0014857104 1895 Obl Ind Euro F"),
]

VERWACHT_AANTAL = {
    "0P0001IT6X.F": 460.6876,   # Aand Ind Wereld
    "0P0001JET8.F": 203.3697,   # Aand Multifact
    "0P0001LI9F.F": 323.6337,   # Aand Opp Macro
    "0P0001KOL0.F": 416.0933,   # Obl Bedrijven
    "0P0001UMPU.F": 111.2152,   # Obl Short Dur
    "0P0001K5V2.F":  64.4662,   # Obl Ind Euro F
    "0P0001L6VX.F": 116.6262,   # Obl Inv. Grade
    "0P0001UR5B.F":  53.9376,   # Obl Spec. Proj
}
VERWACHTE_CASH = 1779.46


def main():
    with app.app_context():
        gebruiker = Gebruiker.query.filter_by(naam=NAAM_GEBRUIKER).first()
        if gebruiker is None:
            print(f"Gebruiker '{NAAM_GEBRUIKER}' bestaat niet.")
            return

        account = BrokerAccount.query.filter_by(
            naam=NAAM_ACCOUNT, gebruiker_id=gebruiker.id).first()
        if account is None:
            print(f"Account '{NAAM_ACCOUNT}' bestaat niet.")
            return

        al_gedaan = Transactie.query.filter_by(
            broker_account_id=account.id, datum=DATUM).count()
        if al_gedaan:
            print(f"Er staan al {al_gedaan} transactie(s) op {DATUM} in dit "
                  "account. Niets toegevoegd.")
            print("Cash saldo:", cash_saldi(account.id))
            return

        for soort, ticker, aantal, bedrag, notitie in MUTATIES:
            db.session.add(Transactie(
                broker_account_id=account.id,
                type=soort,
                ticker=ticker,
                aantal=aantal,
                prijs=bedrag / aantal,   # volle precisie: kasstroom sluit exact aan
                kosten=0.0,
                valuta="EUR",
                datum=DATUM,
                notitie=f"Eff.nota {soort} fondsen {notitie} (batch 02-09-2026)",
            ))

        db.session.flush()
        na_transactie_wijziging(account.id, *{m[1] for m in MUTATIES})
        db.session.commit()

        print("\nBatch verwerkt. Resultaat:")
        saldo = cash_saldi(account.id)
        print("Cash saldo:", saldo, f" (verwacht: EUR {VERWACHTE_CASH:,.2f})")
        fout = False
        for pos in sorted(account.posities, key=lambda p: p.ticker):
            verwacht = VERWACHT_AANTAL.get(pos.ticker)
            regel = "" if verwacht is None else f"  verwacht={verwacht:>10.4f}"
            if verwacht is not None and abs(pos.aantal - verwacht) > 0.0005:
                regel += "  <-- AFWIJKING"
                fout = True
            print(f"  {pos.ticker:15s} aantal={pos.aantal:>10.4f}  "
                  f"GAK={pos.aankoopprijs:>9.4f}  "
                  f"gerealiseerd={pos.gerealiseerde_winst:>8.2f}{regel}")
        if fout:
            print("\nLet op: aantallen wijken af van het mutatieoverzicht.")


if __name__ == "__main__":
    main()
