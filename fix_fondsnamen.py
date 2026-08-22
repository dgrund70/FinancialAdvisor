"""Zet de leesbare fondsnamen op de posities van de zakelijke rekening.

seed_zakelijk_kineoo.py liet `Positie.naam` op de ticker staan (dat is wat
projectie.herbereken_positie() doet bij het aanmaken van een nieuwe rij), dus
het dashboard toont "0P0001IT6X.F" i.p.v. "1895 Aandelen Index Wereld Fonds".
Namen komen uit de Rabo portefeuille-export van 22-08-2026.

Idempotent: overschrijft alleen als de naam nog gelijk is aan de ticker.
Draaien:
    .venv/bin/python fix_fondsnamen.py
"""
from app import app, db
from models import Positie

NAMEN = {
    "0P0001IT6X.F": "1895 Aandelen Index Wereld Fonds",
    "0P0001JET8.F": "1895 Aandelen Multifactor Fonds",
    "0P0001LI9F.F": "1895 Aandelen Opportunity Macro Fonds",
    "0P0001KOL0.F": "1895 Obligaties Bedrijven Wereld Fonds",
    "0P0001UMPU.F": "1895 Obligaties Eur Short Duration",
    "0P0001K5V2.F": "1895 Obligaties Index Euro Fonds",
    "0P0001L6VX.F": "1895 Obligaties Inv. Grade Wereld Fonds",
    "0P0001UR5B.F": "1895 Obligaties Spec. Projecten Fonds",
}


def main():
    with app.app_context():
        gewijzigd = 0
        for pos in Positie.query.filter(Positie.ticker.in_(NAMEN)).all():
            nieuw = NAMEN[pos.ticker]
            if pos.naam != nieuw:
                print(f"  {pos.ticker:15s} '{pos.naam}' -> '{nieuw}'")
                pos.naam = nieuw
                gewijzigd += 1
        if gewijzigd:
            db.session.commit()
            print(f"\n{gewijzigd} fondsnaam(en) bijgewerkt.")
        else:
            print("Alle fondsnamen stonden al goed. Niets gewijzigd.")


if __name__ == "__main__":
    main()
