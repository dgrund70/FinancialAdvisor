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


def xirr(cashflows, guess=0.1):
    """Geld-gewogen rendement (geannualiseerd) uit gedateerde cashflows.

    `cashflows`: lijst van (date, bedrag). Externe inleg is negatief (geld dat
    de portefeuille in gaat), opnames en de slot-waarde positief. Pure Python:
    Newton-Raphson met bisectie-fallback. Geeft None terug bij < 2 flows, geen
    tekenwissel (XIRR ongedefinieerd) of geen convergentie — nooit een crash.
    """
    flows = [(d, float(b)) for d, b in cashflows if b is not None]
    if len(flows) < 2:
        return None
    # Zonder tekenwissel bestaat er geen rendement-oplossing.
    tekens = {b > 0 for _, b in flows if abs(b) > 1e-12}
    if len(tekens) < 2:
        return None

    flows.sort(key=lambda f: f[0])
    t0       = flows[0][0]
    jaren    = [(d - t0).days / 365.0 for d, _ in flows]
    bedragen = [b for _, b in flows]

    def npv(rate):
        return sum(b / (1.0 + rate) ** t for b, t in zip(bedragen, jaren))

    def dnpv(rate):
        return sum(-t * b / (1.0 + rate) ** (t + 1.0) for b, t in zip(bedragen, jaren))

    # Newton-Raphson
    rate = guess
    for _ in range(50):
        if rate <= -1.0:
            break
        noemer = dnpv(rate)
        if abs(noemer) < 1e-12:
            break
        nieuw = rate - npv(rate) / noemer
        if nieuw != nieuw or nieuw <= -1.0 or abs(nieuw) > 1e6:   # NaN/divergentie
            break
        if abs(nieuw - rate) < 1e-8:
            return round(nieuw, 6)
        rate = nieuw

    # Bisectie-fallback op een ruim, geldig interval
    lo, hi   = -0.9999, 10.0
    flo, fhi = npv(lo), npv(hi)
    if flo * fhi > 0:
        return None
    for _ in range(200):
        mid  = (lo + hi) / 2.0
        fmid = npv(mid)
        if abs(fmid) < 1e-9:
            return round(mid, 6)
        if flo * fmid < 0:
            hi, fhi = mid, fmid
        else:
            lo, flo = mid, fmid
    return round((lo + hi) / 2.0, 6)
