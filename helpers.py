"""Gedeelde hulpfuncties voor app, generator en prijzen-service."""


def naar_eur(koers, valuta, wisselkoersen):
    """Reken een koers/bedrag om naar EUR.

    Geeft None terug als de wisselkoers voor `valuta` ontbreekt (dan kan niet
    betrouwbaar worden omgerekend). EUR (en lege valuta) blijft ongewijzigd.
    """
    if koers is None:
        return None
    if not valuta or valuta == "EUR":
        return koers
    fx = (wisselkoersen or {}).get(valuta)
    return koers * fx if fx is not None else None
