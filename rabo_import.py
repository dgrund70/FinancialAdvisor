"""Import van het Rabobank-mutatieoverzicht (Beheerd Beleggen) naar het grootboek.

Vervangt de reeks handgeschreven `seed_*.py`-scripts. De CSV die Rabo levert
(`Mutatieoverzicht_<portefeuille>_<van>_<tot>.csv`) bevat sinds september 2026
de kolom `Volume`, dus aantallen hoeven niet meer uit een portefeuille-export
te komen.

Wat dit bestand doet is puur parsen en valideren -- geen database. `app.py`
doet de vergelijking met wat er al in het grootboek staat en het wegschrijven.

## Eigenaardigheden van het bestand

* Scheidingsteken `;`, tekstcodering meestal cp1252 (soms UTF-8 met BOM).
* Nederlandse getalnotatie: `19.777,68` = 19777,68 en `50.000` = 50000.
* De kolomkoppen lopen vanaf `Bedrag in valuta` een positie uit de pas met de
  inhoud: op die plek staat de valutacode en pas in `Transactie bedrag` het
  ondertekende cashbedrag. We lezen daarom op positie, en controleren de
  koprij zodat een gewijzigd exportformaat hard stukloopt in plaats van
  stilletjes verkeerde bedragen te boeken.
* `-` en lege strings betekenen "niet van toepassing", niet nul-met-teken.

## Conventies (gelijk aan de eerdere seed-scripts)

* Fondsmutaties worden geboekt op de dag **na** de datum in het overzicht
  (de eff.nota-datum); stortingen en opnames op de dag zelf.
* De prijs per stuk wordt afgeleid uit bedrag / aantal op volle precisie,
  niet uit de afgeronde kolom `Prijs`, zodat de kasstroom exact op de bank
  aansluit.
* Binnen een dag blijft de uitvoeringsvolgorde (kolom `Tijd`) staan. Dat is
  nodig omdat `projectie.herbereken_positie()` op `(datum, id)` sorteert en
  een batch koop-en-verkoop in hetzelfde fonds anders een verkeerde GAK en
  gerealiseerde winst oplevert.
"""
from datetime import date, timedelta
import csv
import io

# Portefeuille waarvoor deze import bedoeld is. Een bestand van een andere
# portefeuille wordt geweigerd -- dat zou stilzwijgend in het verkeerde
# grootboek belanden.
PORTEFEUILLE = "42272386"

# De acht Rabo 1895-fondsen in deze portefeuille. Voegt Rabo een fonds toe,
# dan stopt de import met een nette melding en hoort de regel hier bij.
ISIN_TICKER = {
    "NL0014065450": "0P0001IT6X.F",   # 1895 Aandelen Index Wereld
    "NL0014270340": "0P0001JET8.F",   # 1895 Aandelen Multifactor
    "NL00150004M2": "0P0001LI9F.F",   # 1895 Aandelen Opportunities Macro
    "NL0015436049": "0P0001KOL0.F",   # 1895 Obligaties Bedrijven
    "NL0015002BM8": "0P0001UMPU.F",   # 1895 Obligaties Short Duration
    "NL0014857104": "0P0001K5V2.F",   # 1895 Obligaties Index Euro Fund
    "NL0015602376": "0P0001L6VX.F",   # 1895 Obligaties Investment Grade
    "NL0015002BS5": "0P0001UR5B.F",   # 1895 Obligaties Special Projects
}

# Kolompositie -> verwachte kop. Alleen de kolommen die we echt gebruiken;
# een afwijking hierin betekent dat het exportformaat is veranderd.
VERWACHTE_KOPPEN = {
    0:  "portefeuille",
    1:  "naam",
    2:  "datum",
    3:  "type mutatie",
    5:  "volume",
    7:  "prijs",
    11: "transactie bedrag",
    16: "isin code",
    18: "tijd",
}

I_PORTEFEUILLE, I_NAAM, I_DATUM, I_TYPE = 0, 1, 2, 3
I_VOLUME, I_PRIJS = 5, 7
I_VALUTA, I_BEDRAG = 10, 11
I_KOSTEN, I_DIVBELASTING, I_TRANSBELASTING = 12, 14, 15
I_ISIN, I_TIJD = 16, 18

MINIMUM_KOLOMMEN = 19

# "Type mutatie" uit het overzicht -> transactietype in het grootboek.
# Storting/opname krijgt zijn richting uit het teken van het bedrag.
TYPE_MAP = {
    "koop fondsen":      "koop",
    "verkoop fondsen":   "verkoop",
    "dividend":          "dividend",
    "storting / opname": "_cash",
}
# Alles waar dit in voorkomt boeken we als kosten (Rabo varieert de naamgeving
# per soort: beheerkosten, servicekosten, transactiekosten).
KOSTEN_HINTS = ("kosten", "fee")


class ImportFout(Exception):
    """Het bestand is niet bruikbaar; er wordt niets geboekt."""


def _getal(ruw):
    """Nederlands getal naar float. Lege waarde en '-' geven None terug.

    '19.777,68' -> 19777.68 en '50.000' -> 50000.0: een punt is altijd een
    duizendtalscheiding, Rabo schrijft decimalen met een komma.
    """
    if ruw is None:
        return None
    tekst = ruw.strip().replace("\xa0", "").replace(" ", "")
    if tekst in ("", "-", "--"):
        return None
    negatief = tekst.startswith("-")
    tekst = tekst.lstrip("+-").replace(".", "").replace(",", ".")
    if not tekst:
        return None
    try:
        waarde = float(tekst)
    except ValueError:
        raise ImportFout(f"Onleesbaar getal: {ruw!r}")
    return -waarde if negatief else waarde


def _datum(ruw):
    tekst = (ruw or "").strip()
    for scheiding in ("-", "/"):
        deel = tekst.split(scheiding)
        if len(deel) == 3:
            try:
                dag, maand, jaar = (int(d) for d in deel)
                return date(jaar, maand, dag)
            except ValueError:
                break
    raise ImportFout(f"Onleesbare datum: {ruw!r}")


def _decodeer(rauw_bytes):
    for codering in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            return rauw_bytes.decode(codering)
        except UnicodeDecodeError:
            continue
    raise ImportFout("Kan de tekstcodering van het bestand niet bepalen.")


def _controleer_koprij(rij):
    if len(rij) < MINIMUM_KOLOMMEN:
        raise ImportFout(
            f"Dit bestand heeft {len(rij)} kolommen, verwacht minimaal "
            f"{MINIMUM_KOLOMMEN}. Is dit een mutatieoverzicht en geen "
            "portefeuille-export?")
    for index, verwacht in VERWACHTE_KOPPEN.items():
        gevonden = rij[index].strip().lower().lstrip("﻿")
        if gevonden != verwacht:
            raise ImportFout(
                f"Kolom {index + 1} heet '{rij[index].strip()}' maar zou "
                f"'{verwacht}' moeten zijn. Het exportformaat van Rabo is "
                "gewijzigd — de import is gestopt om te voorkomen dat er "
                "verkeerde bedragen worden geboekt.")


def _kosten(rij):
    """Kosten en belastingen bij elkaar; die drukken alle drie de opbrengst."""
    totaal = 0.0
    for index in (I_KOSTEN, I_DIVBELASTING, I_TRANSBELASTING):
        waarde = _getal(rij[index]) if index < len(rij) else None
        if waarde:
            totaal += abs(waarde)
    return round(totaal, 2)


def parse(rauw_bytes):
    """Lees het mutatieoverzicht.

    Geeft `(regels, overgeslagen)` terug. Elke regel is een dict die klaar is
    om een `Transactie` van te maken; `overgeslagen` bevat leesbare uitleg per
    regel die we niet konden of hoefden te boeken.

    Gooit `ImportFout` als het hele bestand niet deugt (verkeerd formaat,
    andere portefeuille, onbekend fonds) — dan wordt er niets geboekt.
    """
    tekst = _decodeer(rauw_bytes)
    lezer = csv.reader(io.StringIO(tekst), delimiter=";")

    try:
        koprij = next(lezer)
    except StopIteration:
        raise ImportFout("Het bestand is leeg.")
    _controleer_koprij(koprij)

    regels, overgeslagen, onbekende_isins, vreemde_portefeuilles = [], [], {}, set()

    for regelnr, rij in enumerate(lezer, start=2):
        if not any(veld.strip() for veld in rij):
            continue
        if len(rij) < MINIMUM_KOLOMMEN:
            overgeslagen.append(f"Regel {regelnr}: te weinig kolommen, overgeslagen.")
            continue

        portefeuille = rij[I_PORTEFEUILLE].strip()
        if portefeuille and portefeuille != PORTEFEUILLE:
            vreemde_portefeuilles.add(portefeuille)
            continue

        soort_ruw = rij[I_TYPE].strip().lower()
        soort = TYPE_MAP.get(soort_ruw)
        if soort is None and any(h in soort_ruw for h in KOSTEN_HINTS):
            soort = "kosten"
        if soort is None:
            overgeslagen.append(
                f"Regel {regelnr}: mutatiesoort '{rij[I_TYPE].strip()}' wordt "
                "(nog) niet ondersteund.")
            continue

        bron_datum = _datum(rij[I_DATUM])
        bedrag = _getal(rij[I_BEDRAG])
        tijd = rij[I_TIJD].strip() or "00:00:00"
        fonds = rij[I_NAAM].strip()
        valuta = (rij[I_VALUTA].strip() or "EUR").upper()
        kosten = _kosten(rij)

        if bedrag is None or bedrag == 0:
            overgeslagen.append(f"Regel {regelnr}: geen bedrag, overgeslagen.")
            continue

        if soort == "_cash":
            regels.append({
                "boek_datum": bron_datum,          # stortingen op de dag zelf
                "bron_datum": bron_datum,
                "tijd": tijd,
                "type": "storting" if bedrag > 0 else "opname",
                "ticker": None,
                "fonds": "",
                "aantal": None,
                "prijs": None,
                "bedrag": round(abs(bedrag), 2),
                "kosten": kosten,
                "valuta": valuta,
                "notitie": f"Rabo {'storting' if bedrag > 0 else 'opname'} "
                           f"{bron_datum.strftime('%d-%m-%Y')}",
            })
            continue

        if soort == "kosten":
            regels.append({
                "boek_datum": bron_datum,
                "bron_datum": bron_datum,
                "tijd": tijd,
                "type": "kosten",
                "ticker": None,
                "fonds": fonds,
                "aantal": None,
                "prijs": None,
                "bedrag": round(abs(bedrag), 2),
                "kosten": 0.0,
                "valuta": valuta,
                "notitie": f"Rabo {rij[I_TYPE].strip()} "
                           f"{bron_datum.strftime('%d-%m-%Y')}",
            })
            continue

        # Vanaf hier: koop, verkoop of dividend — die hebben een fonds nodig.
        isin = rij[I_ISIN].strip().upper()
        ticker = ISIN_TICKER.get(isin)
        if ticker is None:
            onbekende_isins[isin or "(leeg)"] = fonds
            continue

        if soort == "dividend":
            regels.append({
                "boek_datum": bron_datum + timedelta(days=1),
                "bron_datum": bron_datum,
                "tijd": tijd,
                "type": "dividend",
                "ticker": ticker,
                "fonds": fonds,
                "aantal": None,
                "prijs": None,
                "bedrag": round(abs(bedrag), 2),
                "kosten": kosten,
                "valuta": valuta,
                "notitie": f"Rabo dividend {isin} {fonds} "
                           f"({bron_datum.strftime('%d-%m-%Y')})",
            })
            continue

        aantal = _getal(rij[I_VOLUME])
        if not aantal:
            overgeslagen.append(
                f"Regel {regelnr}: {rij[I_TYPE].strip()} zonder aantal "
                "(kolom Volume) — overgeslagen.")
            continue
        aantal = abs(aantal)

        # Het transactiebedrag van Rabo is inclusief kosten. Het grootboek
        # rekent kosten apart, dus die halen we eruit voordat we delen.
        netto = abs(bedrag) - kosten if soort == "koop" else abs(bedrag) + kosten
        regels.append({
            "boek_datum": bron_datum + timedelta(days=1),   # eff.nota-datum
            "bron_datum": bron_datum,
            "tijd": tijd,
            "type": soort,
            "ticker": ticker,
            "fonds": fonds,
            "aantal": aantal,
            "prijs": netto / aantal,        # volle precisie: sluit op de bank aan
            "bedrag": round(abs(bedrag), 2),
            "kosten": kosten,
            "valuta": valuta,
            "notitie": f"Rabo eff.nota {soort} {isin} {fonds} "
                       f"({bron_datum.strftime('%d-%m-%Y')})",
        })

    if vreemde_portefeuilles:
        raise ImportFout(
            "Dit bestand hoort bij portefeuille "
            + ", ".join(sorted(vreemde_portefeuilles))
            + f" en niet bij {PORTEFEUILLE}.")

    if onbekende_isins:
        lijst = ", ".join(f"{i} ({n or 'naam onbekend'})"
                          for i, n in sorted(onbekende_isins.items()))
        raise ImportFout(
            f"Onbekend fonds in het overzicht: {lijst}. Voeg de ISIN toe aan "
            "ISIN_TICKER in rabo_import.py voordat je importeert — anders zou "
            "die mutatie stilzwijgend wegvallen.")

    if not regels:
        raise ImportFout("Geen bruikbare mutaties in dit bestand gevonden.")

    # Uitvoeringsvolgorde binnen de dag vasthouden: de average-cost-berekening
    # is er gevoelig voor.
    regels.sort(key=lambda r: (r["boek_datum"], r["tijd"]))
    return regels, overgeslagen


def signatuur(datum, soort, ticker, aantal, bedrag):
    """Vingerafdruk om een regel te herkennen die al in het grootboek staat.

    Bewust op afgeronde waarden: de bank levert 4 decimalen bij aantallen en
    centen bij bedragen. Identieke regels mogen meerdere keren voorkomen (de
    inleg van 28-08 stond als drie losse boekingen van 50.000 op het afschrift),
    dus vergelijken we aantallen per signatuur en niet alleen het bestaan ervan.
    """
    return (
        datum,
        soort,
        (ticker or "").upper(),
        None if aantal is None else round(float(aantal), 4),
        None if bedrag is None else round(float(bedrag), 2),
    )


def regel_signatuur(regel):
    if regel["type"] in ("koop", "verkoop"):
        bedrag = regel["aantal"] * regel["prijs"] + (
            regel["kosten"] if regel["type"] == "koop" else -regel["kosten"])
    else:
        bedrag = regel["bedrag"]
    return signatuur(regel["boek_datum"], regel["type"], regel["ticker"],
                     regel["aantal"], bedrag)


def transactie_signatuur(tx):
    """Zelfde vingerafdruk, maar dan voor een rij uit het grootboek."""
    if tx.type in ("koop", "verkoop"):
        aantal = tx.aantal
        bedrag = (tx.aantal or 0) * (tx.prijs or 0) + (
            (tx.kosten or 0) if tx.type == "koop" else -(tx.kosten or 0))
    else:
        aantal = None
        bedrag = tx.bedrag
    return signatuur(tx.datum, tx.type, tx.ticker, aantal, bedrag)
