"""Tests voor advies_team.py — pure promptopbouw/formattering en de
kwantitatief-risico-berekening op een geïsoleerde tijdelijke SQLite-DB.
Geen echte Claude-API-calls (die kosten geld en horen hier niet thuis)."""

import json
import sqlite3

import pytest

import advies_team
from advies_generator import parse_kop


# ── format_risico_cijfers (pure) ────────────────────────────────────

def test_format_risico_cijfers_none():
    assert advies_team.format_risico_cijfers(None) is None


def test_format_risico_cijfers_bevat_alle_cijfers_ook_bij_ontbrekende_sharpe():
    """Regressietest: een eerdere versie verloor volatiliteit/max-drawdown uit
    de output zodra sharpe None was, door adjacent-string-literal-concatenatie
    die vóór de if/else-ternary bindt (f"a" f"b" if cond else "c" == (f"a"+f"b")
    if cond else "c", niet f"a" + (f"b" if cond else "c"))."""
    risico = {
        "volatiliteit": 0.167, "max_drawdown": -0.147, "sharpe": None,
        "beta": 1.07, "correlatie_wereldindex": 0.78,
        "dekking": 8, "totaal_posities": 8, "dagen": 307,
        "per_positie": [
            {"ticker": "QBTS", "volatiliteit": 1.206, "correlatie_portefeuille": 0.54},
            {"ticker": "VWCE.AS", "volatiliteit": 0.123, "correlatie_portefeuille": None},
        ],
    }
    tekst = advies_team.format_risico_cijfers(risico)
    assert "16.7%" in tekst          # volatiliteit — moet aanwezig blijven
    assert "-14.7%" in tekst         # max drawdown — moet aanwezig blijven
    assert "Sharpe-ratio (rf=2,5%): onbekend" in tekst
    assert "1.07" in tekst           # bèta
    assert "QBTS" in tekst and "120.6%" in tekst
    assert "VWCE.AS" in tekst and "12.3%" in tekst


def test_format_risico_cijfers_alles_onbekend():
    risico = {
        "volatiliteit": None, "max_drawdown": None, "sharpe": None,
        "beta": None, "correlatie_wereldindex": None,
        "dekking": 1, "totaal_posities": 2, "dagen": 25,
        "per_positie": [{"ticker": "AAA", "volatiliteit": None, "correlatie_portefeuille": None}],
    }
    tekst = advies_team.format_risico_cijfers(risico)
    assert tekst.count("onbekend") >= 3


# ── bouw_synthese_prompt + FORMAAT_INSTRUCTIE (formaatcontract) ────

def test_bouw_synthese_prompt_bevat_alle_onderdelen():
    prompt = advies_team.bouw_synthese_prompt(
        context="## Portefeuille\n...", gewaagd="concept gewaagd",
        gemiddeld="concept gemiddeld", briefing="concept briefing",
        perspectief="concept perspectief", tag_naam=None)
    assert "concept gewaagd" in prompt
    assert "concept gemiddeld" in prompt
    assert "concept briefing" in prompt
    assert "concept perspectief" in prompt
    assert "RISICO:" in prompt and "TIPS:" in prompt   # FORMAAT_INSTRUCTIE aanwezig


def test_bouw_synthese_prompt_met_tag():
    prompt = advies_team.bouw_synthese_prompt("context", "g", "m", "b", "p", tag_naam="Pensioen")
    assert "Pensioen" in prompt


def test_eindsynthese_output_blijft_parseerbaar_met_parse_kop():
    """Simuleert een (fictieve) eindsynthese-respons in het afgesproken
    formaat en controleert dat de bestaande parse_kop() 'm correct verwerkt,
    zodat tips-validatie en opslag in advies_team.py ongewijzigd werken."""
    fake_response = (
        "RISICO: 3 — matig-offensief door concentratie\n"
        "TIPS: QBTS, VWCE.AS\n"
        "\n"
        "## Samenvatting\nTest.\n"
    )
    risico_score, risico_reden, tickers, rest = parse_kop(fake_response)
    assert risico_score == 3
    assert tickers == ["QBTS", "VWCE.AS"]
    assert rest.startswith("## Samenvatting")


# ── team_details JSON-vorm (wat de Jinja-`fromjson`-filter verwacht) ─

def test_team_details_json_vorm():
    details = {
        "gewaagd":        {"model": "haiku", "tekst": "..."},
        "gemiddeld":       {"model": "haiku", "tekst": "..."},
        "macro_briefing":  {"model": "haiku", "tekst": "..."},
        "perspectief":     {"model": "sonnet", "tekst": "..."},
    }
    tekst = json.dumps(details, ensure_ascii=False)
    terug = json.loads(tekst)
    assert set(terug) == {"gewaagd", "gemiddeld", "macro_briefing", "perspectief"}
    for rol in terug.values():
        assert set(rol) == {"model", "tekst"}


# ── laad_risico_cijfers / laad_risicoprofiel (geïsoleerde tijdelijke DB) ─

@pytest.fixture
def temp_db(monkeypatch, tmp_path):
    """Tijdelijke sqlite-DB met alleen de tabellen die advies_team.py nodig
    heeft — losstaand van de Flask/SQLAlchemy-app en van data/app.db."""
    pad = tmp_path / "test.db"
    conn = sqlite3.connect(pad)
    conn.execute("CREATE TABLE koers_historie (ticker TEXT, datum TEXT, koers REAL)")
    conn.execute("CREATE TABLE benchmark_punten (ticker TEXT, datum TEXT, koers REAL)")
    conn.execute("CREATE TABLE gebruikers (id INTEGER PRIMARY KEY, risicoprofiel TEXT)")
    conn.commit()
    conn.close()
    monkeypatch.setattr(advies_team, "DB_PATH", pad)
    return pad


def _vul_historie(pad, ticker, prijzen, tabel="koers_historie"):
    conn = sqlite3.connect(pad)
    for i, prijs in enumerate(prijzen):
        conn.execute(f"INSERT INTO {tabel} (ticker, datum, koers) VALUES (?, ?, ?)",
                     (ticker, f"2026-01-{i + 1:02d}", prijs))
    conn.commit()
    conn.close()


def test_laad_risico_cijfers_te_weinig_historie(temp_db):
    posities = [("AAA", "A", 1.0, 10.0, None, "acc", "live", None, "EUR")]
    _vul_historie(temp_db, "AAA", [10, 10.5, 11])   # < 20 dagen
    assert advies_team.laad_risico_cijfers(posities) is None


def test_laad_risico_cijfers_geen_dekking(temp_db):
    posities = [("ZZZ", "Z", 1.0, 10.0, None, "acc", "live", None, "EUR")]
    assert advies_team.laad_risico_cijfers(posities) is None   # geen historie voor ZZZ


def test_laad_risico_cijfers_berekent_met_voldoende_historie(temp_db):
    prijzen = [100 + i for i in range(30)]   # 30 dagen, gestaag stijgend
    _vul_historie(temp_db, "AAA", prijzen)
    posities = [("AAA", "A", 2.0, 90.0, None, "acc", "live", None, "EUR")]
    risico = advies_team.laad_risico_cijfers(posities)
    assert risico is not None
    assert risico["dekking"] == 1
    assert risico["dagen"] == 30
    assert risico["volatiliteit"] is not None
    assert risico["beta"] is None          # geen benchmark-data ingevuld
    assert risico["per_positie"][0]["ticker"] == "AAA"


def test_laad_risicoprofiel_valt_terug_op_standaard(temp_db):
    conn = sqlite3.connect(temp_db)
    conn.execute("INSERT INTO gebruikers (id, risicoprofiel) VALUES (1, NULL)")
    conn.commit()
    conn.close()
    profiel = advies_team.laad_risicoprofiel(1)
    assert "voorzichtige particuliere belegger" in profiel


def test_laad_risicoprofiel_gebruikt_ingevulde_tekst(temp_db):
    conn = sqlite3.connect(temp_db)
    conn.execute(
        "INSERT INTO gebruikers (id, risicoprofiel) VALUES (1, 'Zakelijk vermogen Kineoo B.V.')")
    conn.commit()
    conn.close()
    assert advies_team.laad_risicoprofiel(1) == "Zakelijk vermogen Kineoo B.V."
