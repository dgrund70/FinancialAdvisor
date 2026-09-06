"""Ontleedt de markdown van een advies in secties, kernboodschap en acties.

Pure functies zonder Flask- of DB-afhankelijkheid, zodat ze los te testen zijn.
Het advies komt van een taalmodel en heeft dus geen gegarandeerde vorm; alles
hier is defensief: geen match = lege lijst, nooit een exception.
"""
import re
import unicodedata

# Secties zijn ## -koppen. Alles daaronder (###, ####) hoort bij die sectie.
_SECTIE_KOP = re.compile(r"^##[ \t]+(.+?)[ \t]*$", re.M)

# Secties die standaard dicht staan: achtergrond, geen actie.
_DICHT = ("risico", "macro", "geopolit", "disclaimer", "bijlage")

# Een ticker zoals het model ze schrijft: ASML, VWCE.AS, 0P0001UMPU.F
_TICKER = r"[A-Z0-9]{1,12}(?:\.[A-Z]{1,3})?"

# Vorm A — oordeel in de kop:  ### 🔴 QBTS (D-Wave) — **VOLLEDIG VERKOPEN**
# Non-greedy tot het gedachtestreepje: een tickernaam mag zelf koppeltekens
# bevatten ("(D-Wave Quantum)"), anders valt zo'n regel buiten de match.
_KOP_MET_VERDICT = re.compile(
    r"^#{3,4}[ \t]+[^\w\n]*(?P<ticker>" + _TICKER + r")\b[^\n]*?[—–-][ \t]*\*\*(?P<verdict>[^*\n]+)\*\*",
    re.M)

# Vorm B — kop, daaronder een losse regel:  **Advies: Kopen (kleine positie)**
_KOP = re.compile(r"^#{3,4}[ \t]+[^\w\n]*(?P<ticker>" + _TICKER + r")\b(?P<rest>[^\n]*)$", re.M)
_ADVIESREGEL = re.compile(r"^\*\*Advies:[ \t]*(?P<verdict>[^*\n]+)\*\*", re.M)

_KOOPWOORDEN = ("kopen", "bijkopen", "instappen", "opbouwen")
_VERKOOPWOORDEN = ("verkopen", "halveren", "reduceren", "afbouwen", "verkoop")


def _slug(tekst):
    kaal = unicodedata.normalize("NFKD", tekst).encode("ascii", "ignore").decode()
    kaal = re.sub(r"[^a-zA-Z0-9]+", "-", kaal).strip("-").lower()
    return kaal or "sectie"


def _schoon(tekst):
    """Markdown-opmaak weg, voor gebruik in platte tekst."""
    tekst = re.sub(r"\*\*|__|`", "", tekst)
    tekst = re.sub(r"\s+", " ", tekst)
    return tekst.strip()


def secties(tekst):
    """Split het advies in ##-secties.

    Geeft dicts met slug, titel, tekst en `open` (of de sectie standaard
    uitgeklapt hoort te staan). Zonder ##-koppen: één sectie 'Advies'.
    """
    if not tekst:
        return []
    koppen = list(_SECTIE_KOP.finditer(tekst))
    if not koppen:
        return [{"slug": "advies", "titel": "Advies", "tekst": tekst.strip(), "open": True}]

    uit = []
    intro = tekst[: koppen[0].start()].strip()
    if intro:
        uit.append({"slug": "inleiding", "titel": "Inleiding", "tekst": intro, "open": True})

    gebruikt = set()
    for i, kop in enumerate(koppen):
        einde = koppen[i + 1].start() if i + 1 < len(koppen) else len(tekst)
        body = tekst[kop.end():einde].strip()
        body = re.sub(r"\n-{3,}[ \t]*$", "", body).strip()   # scheidingsstreep aan het eind
        titel = kop.group(1).strip()
        slug = _slug(titel)
        teller = 2
        while slug in gebruikt:                              # dubbele koppen: uniek houden
            slug, teller = f"{_slug(titel)}-{teller}", teller + 1
        gebruikt.add(slug)
        uit.append({
            "slug": slug,
            "titel": titel,
            "tekst": body,
            "open": not any(woord in titel.lower() for woord in _DICHT),
        })
    return uit


def kernboodschap(tekst, maxlengte=240):
    """Eén zin uit de samenvatting: bij voorkeur die met de belangrijkste actie."""
    for sectie in secties(tekst):
        if "samenvatting" not in sectie["titel"].lower():
            continue
        plat = _schoon(sectie["tekst"])
        zinnen = [z.strip() for z in re.split(r"(?<=[.!?])\s+", plat) if z.strip()]
        if not zinnen:
            return ""
        for zin in zinnen:
            if "belangrijkste" in zin.lower() or "actie is" in zin.lower():
                return zin[:maxlengte]
        return zinnen[0][:maxlengte]
    return ""


def _kort(verdict):
    """'Kopen (kleine positie, gefaseerd)' -> 'Kopen'."""
    kern = re.split(r"[(,;/—–]", verdict, 1)[0]
    kern = _schoon(kern).rstrip(" .-")
    if not kern:
        return ""
    # Alleen de eerste letter aanpassen; 'VS-blootstelling' moet VS blijven.
    return kern[0].upper() + kern[1:]


def _soort(verdict):
    """Classificeer op het eerste oordeel, niet op de nuance erachter.

    'Houden / Gefaseerd bijkopen' is houden — het woord 'bijkopen' verderop in
    de regel maakt er geen koopadvies van.
    """
    laag = _kort(verdict).lower() or verdict.lower()
    if "houden" in laag:
        return "houden"
    if any(w in laag for w in _VERKOOPWOORDEN):
        return "verkopen"
    if any(w in laag for w in _KOOPWOORDEN):
        return "kopen"
    return "anders"


def acties(tekst):
    """Alle (ticker, verdict)-paren uit het advies, in volgorde van voorkomen."""
    if not tekst:
        return []
    gevonden = {}
    volgorde = []

    def voeg_toe(ticker, verdict):
        if ticker in gevonden or not verdict:
            return
        kort = _kort(verdict)
        if not kort:
            return
        gevonden[ticker] = {"ticker": ticker, "verdict": kort,
                            "volledig": _schoon(verdict), "soort": _soort(verdict)}
        volgorde.append(ticker)

    for m in _KOP_MET_VERDICT.finditer(tekst):
        voeg_toe(m.group("ticker"), m.group("verdict"))

    # Vorm B: kop, en binnen de eerstvolgende alinea's een **Advies: …**-regel.
    koppen = list(_KOP.finditer(tekst))
    for i, kop in enumerate(koppen):
        einde = koppen[i + 1].start() if i + 1 < len(koppen) else len(tekst)
        blok = tekst[kop.end():einde]
        regel = _ADVIESREGEL.search(blok)
        if regel:
            voeg_toe(kop.group("ticker"), regel.group("verdict"))

    return [gevonden[t] for t in volgorde]


def hoofdacties(tekst, maxaantal=3):
    """De acties die om een beslissing vragen: kopen en verkopen eerst."""
    alles = acties(tekst)
    prioriteit = {"kopen": 0, "verkopen": 1, "anders": 2, "houden": 3}
    gesorteerd = sorted(alles, key=lambda a: prioriteit.get(a["soort"], 4))
    return gesorteerd[:maxaantal]
