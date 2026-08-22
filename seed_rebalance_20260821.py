"""Eenmalig invoerscript: de rebalance van 21-08-2026 op de Rabobank zakelijke
beleggersrekening (Kineoo B.V.).

Bron: bankmutaties 21-08-2026 (batch BaNCS-EUR-20260820-14033, handelsdatum
20-08, valuta 24-08) gecombineerd met de portefeuille-export van 22-08-2026.
Aantallen zijn afgeleid uit het verschil tussen de juli-posities (zie
seed_zakelijk_kineoo.py) en de stukken in de export van 22-08; de prijs is
opbrengst/kosten gedeeld door dat aantal, zodat de kasstroom exact klopt.

Netto kasstroom: +934,97 verkocht, -914,86 gekocht = +20,11 → cash 692,00 → 712,11.

Idempotent: stopt als er al transacties op 21-08-2026 in dit account staan.

Draaien:
    .venv/bin/python seed_rebalance_20260821.py
"""
from datetime import date

from app import app, db
from models import Gebruiker, BrokerAccount, Transactie
from projectie import na_transactie_wijziging, cash_saldi

NAAM_GEBRUIKER = "Zakelijk (Kineoo)"
NAAM_ACCOUNT   = "Rabobank Zakelijk"
DATUM          = date(2026, 8, 21)
DATUM_JULI     = date(2026, 7, 15)

# Stap 0 — afrondingscorrectie op de juli-aankopen.
# seed_zakelijk_kineoo.py gebruikte de door de bank afgeronde GAK (2 decimalen),
# waardoor aantal * prijs 52 cent afweek van de werkelijke afschrijvingen en het
# cash-saldo op 692,52 uitkwam i.p.v. 692,00. Hier zetten we de prijs op
# bedrag / aantal (volle precisie); de GAK blijft afgerond identiek.
JULI_BEDRAGEN = {          # ticker: exact afgeschreven bedrag volgens de bank
    "0P0001IT6X.F": 34990.00,
    "0P0001JET8.F": 17553.00,
    "0P0001LI9F.F": 17509.00,
    "0P0001KOL0.F": 12904.00,
    "0P0001UMPU.F":  4458.00,
    "0P0001K5V2.F":  6045.00,
    "0P0001L6VX.F":  3770.00,
    "0P0001UR5B.F":  2079.00,
}

# (type, ticker, aantal, prijs, omschrijving uit de eff.nota)
MUTATIES = [
    ("verkoop", "0P0001IT6X.F",   2.4827, 189.0240, "NL0014065450 1895 Aand Ind Wereld 61-111230990"),
    ("verkoop", "0P0001JET8.F",   1.4844, 224.8046, "NL0014270340 1895 Aand Multifact 61-111231021"),
    ("verkoop", "0P0001LI9F.F",   0.8482, 155.6001, "NL00150004M2 1895 Aand Opp Macro 61-111230982"),
    ("koop",    "0P0001KOL0.F",   5.1405,  79.1071, "NL0015436049 1895 Obl Bedrijven 61-111231038"),
    ("koop",    "0P0001UMPU.F",   1.2169, 101.2409, "NL0015002BM8 1895 Obl Short Dur 61-111231001"),
    ("koop",    "0P0001K5V2.F",   2.4305,  82.7155, "NL0014857104 1895 Obl Ind Euro F 61-111231029"),
    ("koop",    "0P0001L6VX.F",   1.4700,  82.3878, "NL0015602376 1895 Obl Inv. Grade 61-111231011"),
    ("koop",    "0P0001UR5B.F",   0.6366,  98.7433, "NL0015002BS5 1895 Obl Spec. Proj 61-111230971"),
]

# Verwachte eindstand uit de portefeuille-export van 22-08-2026 (controle).
VERWACHT_AANTAL = {
    "0P0001IT6X.F": 183.5078,
    "0P0001JET8.F":  77.0608,
    "0P0001LI9F.F": 111.0498,
    "0P0001KOL0.F": 168.2871,
    "0P0001UMPU.F":  45.2928,
    "0P0001K5V2.F":  75.0643,
    "0P0001L6VX.F":  47.2198,
    "0P0001UR5B.F":  21.6211,
}


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

        # Stap 0 — juli-prijzen op volle precisie zetten (idempotent).
        gecorrigeerd = 0
        for tx in Transactie.query.filter_by(
                broker_account_id=account.id, datum=DATUM_JULI, type="koop").all():
            bedrag = JULI_BEDRAGEN.get(tx.ticker)
            if bedrag and tx.aantal:
                exact = bedrag / tx.aantal
                if abs((tx.prijs or 0) - exact) > 1e-9:
                    tx.prijs = exact
                    gecorrigeerd += 1
        if gecorrigeerd:
            print(f"Afrondingscorrectie toegepast op {gecorrigeerd} juli-aankoop(en).")
            db.session.flush()

        al_gedaan = Transactie.query.filter_by(
            broker_account_id=account.id, datum=DATUM).count()
        if al_gedaan:
            if gecorrigeerd:
                for ticker in JULI_BEDRAGEN:
                    na_transactie_wijziging(account.id, ticker)
                db.session.commit()
            print(f"Er staan al {al_gedaan} transactie(s) op {DATUM} in dit "
                  "account. Geen nieuwe mutaties toegevoegd.")
            print("Cash saldo:", cash_saldi(account.id))
            return

        for soort, ticker, aantal, prijs, notitie in MUTATIES:
            db.session.add(Transactie(
                broker_account_id=account.id,
                type=soort,
                ticker=ticker,
                aantal=aantal,
                prijs=prijs,
                kosten=0.0,
                valuta="EUR",
                datum=DATUM,
                notitie=f"Eff.nota {soort} fondsen {notitie} (rebalance 21-08-2026)",
            ))

        db.session.flush()
        for _, ticker, *_ in MUTATIES:
            na_transactie_wijziging(account.id, ticker)
        db.session.commit()

        print("\nRebalance verwerkt. Resultaat:")
        print("Cash saldo:", cash_saldi(account.id), " (verwacht: EUR 712,11)")
        fout = False
        for pos in sorted(account.posities, key=lambda p: p.ticker):
            verwacht = VERWACHT_AANTAL.get(pos.ticker)
            afwijking = "" if verwacht is None else f"  verwacht={verwacht:>10.4f}"
            if verwacht is not None and abs(pos.aantal - verwacht) > 0.0005:
                afwijking += "  <-- AFWIJKING"
                fout = True
            print(f"  {pos.ticker:15s} aantal={pos.aantal:>10.4f}  "
                  f"GAK={pos.aankoopprijs:>8.2f}  "
                  f"gerealiseerd={pos.gerealiseerde_winst:>7.2f}{afwijking}")
        totaal_gerealiseerd = sum(p.gerealiseerde_winst or 0 for p in account.posities)
        print(f"\nTotaal gerealiseerde winst: {totaal_gerealiseerd:.2f} "
              "(verwacht: ca. 3,46)")
        if fout:
            print("\nLet op: aantallen wijken af van de export van 22-08-2026.")


if __name__ == "__main__":
    main()
