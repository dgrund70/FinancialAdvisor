"""Eenmalig herstelscript: de ontbrekende stortingen op de twee DEGIRO-accounts.

Probleem
--------
In de accounts 'Mijn account / DEGIRO' en 'Olivier / DEGIRO' staan wel kopen,
maar geen enkele storting. Cash wordt in projectie.cash_saldi() uit het
grootboek afgeleid, dus elke koop trekt het saldo negatief:

    Mijn account   EUR -989,66
    Olivier        EUR -5.208,96 en USD -2.255,05

Gevolgen: het dashboard verrekent dat negatieve saldo in 'Totale waarde'
(1.174,91 belegd - 989,66 cash = 185,25), en _externe_flows() vindt geen
cashflows, waardoor die accounts geen XIRR kunnen berekenen.

Aanpak
------
Per aankoopdatum en per valuta een storting boeken ter grootte van precies het
bedrag dat die dag aan aankopen wegging. Daarmee wordt het cashsaldo per valuta
exact nul, blijft de timing van de inleg kloppen voor XIRR, en verandert er
niets aan de posities.

Olivier's rekening staat in EUR; de USD-aankopen zijn betaald met euro's die
DEGIRO heeft omgewisseld (AutoFX). Het model kent geen wisseltype, dus die
wissel wordt geboekt als een storting in USD ter grootte van het bedrag dat de
aankoop kostte. Effect: het USD-saldo komt op nul, het EUR-saldo blijft
ongemoeid, en _externe_flows() rekent de USD-storting via de dagkoers om naar
EUR — de inleg telt dus met het juiste eurobedrag mee in de XIRR.

Het alternatief (storting EUR + opname EUR + storting USD per wissel) geeft
exact dezelfde saldi en dezelfde XIRR, maar zet vijf 'opname'-regels in het
grootboek die eruitzien als opnames die nooit hebben plaatsgevonden. Daarom
niet gekozen.

Draaien
-------
    .venv/bin/python seed_ontbrekende_stortingen.py           # toont alleen wat het zou doen
    .venv/bin/python seed_ontbrekende_stortingen.py --boek    # boekt en commit

Zonder --boek wordt er niets weggeschreven. Met --boek maakt het script eerst
een backup van data/app.db (data/app.db.bak_<datum>_pre_stortingen).

Idempotent: een account met een cashsaldo van nul of hoger wordt overgeslagen,
dus een tweede run doet niets. Werkt op elk account, ongeacht hoe gebruiker of
account heet.
"""
import shutil
import sys
from collections import defaultdict
from datetime import date, datetime

from app import app, db, DB_PATH
from models import Gebruiker, BrokerAccount, Transactie
from projectie import cash_saldi

# Geen vaste namen: gebruikers worden hernoemd en accounts komen erbij. Het
# script pakt elk account met een negatief cashsaldo, in welke valuta dan ook.

NOTITIE_EUR = ("Herstelboeking: storting die hoort bij de aankopen van deze dag "
               "(ontbrak in het grootboek)")
NOTITIE_VREEMD = ("Herstelboeking: euro's omgewisseld naar {valuta} voor de aankopen "
                  "van deze dag (rekening staat in EUR; AutoFX)")


def benodigde_stortingen(account_id):
    """{(datum, valuta): bedrag} — per dag en valuta het netto cashtekort.

    Alleen dagen met een negatief saldo-effect leveren een storting op;
    verkopen en dividend op dezelfde dag worden ermee verrekend.
    """
    per = defaultdict(float)
    for tx in Transactie.query.filter_by(broker_account_id=account_id).all():
        valuta = tx.valuta or "EUR"
        aantal = tx.aantal or 0.0
        prijs  = tx.prijs or 0.0
        bedrag = tx.bedrag or 0.0
        kosten = tx.kosten or 0.0
        if tx.type == "koop":
            delta = -(aantal * prijs + kosten)
        elif tx.type == "verkoop":
            delta = aantal * prijs - kosten
        elif tx.type == "dividend":
            delta = bedrag - kosten
        elif tx.type == "kosten":
            delta = -bedrag
        elif tx.type == "storting":
            delta = bedrag
        elif tx.type == "opname":
            delta = -bedrag
        else:                       # correctie: geen cash-effect
            delta = 0.0
        per[(tx.datum, valuta)] += delta
    return {sleutel: round(-saldo, 2)
            for sleutel, saldo in sorted(per.items()) if saldo < -0.005}


def main(boeken):
    with app.app_context():
        te_boeken = []          # (gebruiker, account, datum, valuta, bedrag)
        for account in BrokerAccount.query.order_by(BrokerAccount.id).all():
            gebruiker = db.session.get(Gebruiker, account.gebruiker_id)
            naam = f"{gebruiker.naam if gebruiker else '?'} / {account.naam}"
            saldi = cash_saldi(account.id)
            tekort = {v: s for v, s in saldi.items() if s < -0.005}
            if not tekort:
                print(f"{naam}: cash {saldi} — niets te doen.")
                continue

            print(f"\n{naam}")
            print(f"  cash nu:      {saldi}")
            nodig = benodigde_stortingen(account.id)
            if not nodig:
                print("  Negatief saldo, maar geen dag met een tekort gevonden — "
                      "handmatig nakijken.")
                continue
            for (datum, valuta), bedrag in nodig.items():
                print(f"    storting {datum}  {valuta} {bedrag:>10,.2f}")
                te_boeken.append((naam, account, datum, valuta, bedrag))

        if not te_boeken:
            print("\nNiets te doen.")
            return

        if not boeken:
            print(f"\n{len(te_boeken)} storting(en) klaar om te boeken. "
                  "Draai opnieuw met --boek om ze weg te schrijven.")
            return

        stempel = datetime.now().strftime("%Y%m%d")
        backup = DB_PATH.with_name(f"{DB_PATH.name}.bak_{stempel}_pre_stortingen")
        shutil.copy2(DB_PATH, backup)
        print(f"\nBackup geschreven: {backup.name}")

        geraakt = {}
        for naam, account, datum, valuta, bedrag in te_boeken:
            db.session.add(Transactie(
                broker_account_id=account.id,
                type="storting",
                bedrag=bedrag,
                kosten=0.0,
                valuta=valuta,
                datum=datum,
                notitie=(NOTITIE_EUR if valuta == "EUR"
                         else NOTITIE_VREEMD.format(valuta=valuta)),
            ))
            geraakt[naam] = account.id
        db.session.commit()

        print("\nGeboekt. Nieuwe cashsaldi:")
        for naam, account_id in geraakt.items():
            print(f"  {naam}: {cash_saldi(account_id)}")


if __name__ == "__main__":
    main(boeken="--boek" in sys.argv)
