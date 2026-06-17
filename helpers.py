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


def benchmark_eindwaarde(flows, prijs_op, vandaag):
    """Simuleer de externe cashflows in een benchmark (deposit-matched).

    `flows`: lijst van (date, bedrag_eur) zoals voor xirr() — storting negatief
    (geld de portefeuille in), opname positief. `prijs_op(date)` geeft de
    benchmark-koers (EUR) op of vóór die datum, of None.

    Bij elke storting koop je `bedrag / koers` eenheden van de benchmark; bij
    een opname verkoop je er evenveel als het opgenomen bedrag waard is. Geeft
    (eindwaarde_eur, eenheden) terug, of (None, None) als een koers ontbreekt.
    """
    eenheden = 0.0
    for d, bedrag in flows:
        koers = prijs_op(d)
        if not koers or koers <= 0:
            return None, None
        if bedrag < 0:        # storting → eenheden kopen
            eenheden += (-bedrag) / koers
        elif bedrag > 0:      # opname → eenheden verkopen
            eenheden -= bedrag / koers
    koers_nu = prijs_op(vandaag)
    if not koers_nu or koers_nu <= 0:
        return None, None
    return eenheden * koers_nu, eenheden


# ── Risico-statistiek (pure Python, geen numpy) ───────────────────

def dagrendementen(koersen):
    """Chronologische koersreeks → lijst van dag-op-dag rendementen."""
    r = []
    for vorige, huidig in zip(koersen, koersen[1:]):
        if vorige:
            r.append(huidig / vorige - 1.0)
    return r


def volatiliteit(rendementen, periodes=252):
    """Geannualiseerde volatiliteit = std(dagrendementen) × √periodes. None bij <2."""
    n = len(rendementen)
    if n < 2:
        return None
    gem = sum(rendementen) / n
    var = sum((x - gem) ** 2 for x in rendementen) / (n - 1)
    return (var ** 0.5) * (periodes ** 0.5)


def max_drawdown(waarden):
    """Grootste piek-tot-dal daling als negatieve fractie (bijv. -0.23). None bij <2."""
    if len(waarden) < 2:
        return None
    piek = waarden[0]
    mdd = 0.0
    for v in waarden:
        if v > piek:
            piek = v
        if piek > 0:
            dd = v / piek - 1.0
            if dd < mdd:
                mdd = dd
    return mdd


def beta(port_rendementen, bench_rendementen):
    """Beta van de portefeuille t.o.v. de benchmark uit gepaarde dagrendementen."""
    n = min(len(port_rendementen), len(bench_rendementen))
    if n < 2:
        return None
    p = port_rendementen[-n:]
    b = bench_rendementen[-n:]
    gp = sum(p) / n
    gb = sum(b) / n
    cov = sum((pi - gp) * (bi - gb) for pi, bi in zip(p, b)) / (n - 1)
    var = sum((bi - gb) ** 2 for bi in b) / (n - 1)
    return cov / var if var else None
