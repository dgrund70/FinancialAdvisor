"""Projectie — leidt de Positie-cache af uit het Transactie-grootboek.

Holdings (`Positie`) zijn niet langer rechtstreeks ingevoerd maar afgeleid van
een stroom transacties, met **average-cost** boekhouding:
  - koop      : kapitaliseert kosten in de kostprijs, herberekent gewogen GAK
  - verkoop   : realiseert winst (opbrengst - GAK*aantal - kosten), GAK ongewijzigd
  - correctie : zet aantal/GAK absoluut (drager van de handmatige direct-edit)

Geen van deze functies commit zelf — de aanroeper bepaalt de transactiegrens.
"""

from models import db, Positie, Transactie

# Aandelen-bewegende transactietypes (de rest raakt alleen cash).
_AANDEEL_TYPES = ("koop", "verkoop", "correctie")
_EPS = 1e-9


def herbereken_positie(account_id, ticker):
    """(Her)bereken de Positie-cache voor (account_id, ticker) uit het grootboek.

    Maakt de rij aan, werkt 'm bij, of verwijdert 'm als er geen aandelen-
    transacties (meer) zijn. Metadata (naam/koers_type/handmatige_koers/valuta/
    tags) van een bestaande rij blijft ongemoeid; alleen aantal, aankoopprijs en
    gerealiseerde_winst worden geprojecteerd.
    """
    txs = (Transactie.query
           .filter(Transactie.broker_account_id == account_id,
                   Transactie.ticker == ticker,
                   Transactie.type.in_(_AANDEEL_TYPES))
           .order_by(Transactie.datum, Transactie.id)
           .all())

    pos = (Positie.query
           .filter_by(broker_account_id=account_id, ticker=ticker)
           .order_by(Positie.id)
           .first())

    if not txs:
        # Geen aandelen-transacties meer → verweesde cache opruimen.
        if pos is not None:
            db.session.delete(pos)
        return None

    qty = avg = realized = 0.0
    eerste_koop_datum = None
    valuta = "EUR"

    for tx in txs:
        valuta = tx.valuta or valuta
        aantal = tx.aantal or 0.0
        prijs  = tx.prijs or 0.0
        kosten = tx.kosten or 0.0

        if tx.type == "koop":
            nieuwe_qty = qty + aantal
            if nieuwe_qty > _EPS:
                avg = (qty * avg + aantal * prijs + kosten) / nieuwe_qty
            qty = nieuwe_qty
            if eerste_koop_datum is None:
                eerste_koop_datum = tx.datum
        elif tx.type == "verkoop":
            realized += aantal * prijs - aantal * avg - kosten
            qty -= aantal
            if qty <= _EPS:        # volledig verkocht → reset GAK voor her-aankoop
                qty = avg = 0.0
        elif tx.type == "correctie":
            qty = aantal           # her-baseline; realized blijft staan
            avg = prijs
            if eerste_koop_datum is None:
                eerste_koop_datum = tx.datum

    if qty < 0:                    # short niet ondersteund — defensief clampen
        qty = 0.0

    if pos is None:
        pos = Positie(broker_account_id=account_id, ticker=ticker,
                      naam=ticker, koers_type="live", valuta=valuta,
                      aankoopdatum=eerste_koop_datum)
        db.session.add(pos)

    pos.aantal              = round(qty, 8)
    pos.aankoopprijs        = round(avg, 8)
    pos.gerealiseerde_winst = round(realized, 8)
    return pos


def na_transactie_wijziging(account_id, *tickers):
    """Herbereken de geraakte ticker(s) na een transactie-mutatie.

    Bij een edit die de ticker wijzigt: geef zowel de oude als de nieuwe ticker
    mee. Cash-only types (zonder ticker) vereisen geen projectie.
    """
    for ticker in tickers:
        if ticker:
            herbereken_positie(account_id, ticker)


def cash_saldi(account_id):
    """Geef {valuta: saldo} voor een account, gesommeerd over alle transacties
    volgens de tekenconventie in models.Transactie."""
    saldi = {}
    for tx in Transactie.query.filter_by(broker_account_id=account_id).all():
        v      = tx.valuta or "EUR"
        aantal = tx.aantal or 0.0
        prijs  = tx.prijs or 0.0
        bedrag = tx.bedrag or 0.0
        kosten = tx.kosten or 0.0
        if tx.type == "storting":
            delta = bedrag
        elif tx.type == "opname":
            delta = -bedrag
        elif tx.type == "koop":
            delta = -(aantal * prijs + kosten)
        elif tx.type == "verkoop":
            delta = aantal * prijs - kosten
        elif tx.type == "dividend":
            delta = bedrag - kosten
        elif tx.type == "kosten":
            delta = -bedrag
        else:                      # correctie → geen cash-effect
            delta = 0.0
        saldi[v] = saldi.get(v, 0.0) + delta
    return {v: round(s, 2) for v, s in saldi.items()}


def herbereken_alles():
    """Self-heal: herbereken elke (account, ticker) opnieuw uit het grootboek.
    Commit zelf. Te gebruiken als onderhouds-/herstelactie."""
    paren = (db.session.query(Transactie.broker_account_id, Transactie.ticker)
             .filter(Transactie.ticker.isnot(None),
                     Transactie.type.in_(_AANDEEL_TYPES))
             .distinct().all())
    for account_id, ticker in paren:
        herbereken_positie(account_id, ticker)
    db.session.commit()
