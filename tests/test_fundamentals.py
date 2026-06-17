"""Tests voor de fundamentals-formattering in de adviescontext."""

from advies_generator import _format_fundamental, _kort_bedrag


def test_format_includes_key_metrics():
    f = {
        "sector": "Technology", "markt_kap": 2.5e12, "pe": 28.4, "forward_pe": 24.1,
        "koers_boekwaarde": 12.0, "dividend_rendement": 0.005, "winstmarge": 0.25,
        "omzetgroei": 0.12, "winstgroei": 0.18, "rendement_ev": 0.30,
        "koersdoel": 210.5, "aanbeveling": "buy",
    }
    s = _format_fundamental("AAPL", f)
    assert s.startswith("- **AAPL**:")
    for frag in ["Technology", "mkt cap 2.5T", "K/W 28.4", "fwd K/W 24.1",
                 "K/B 12.0", "marge 25.0%", "ROE 30.0%", "koersdoel 210.50",
                 "analisten: buy"]:
        assert frag in s, frag


def test_empty_or_metadata_only_returns_none():
    assert _format_fundamental("X", {}) is None
    # alleen naam/valuta is geen bruikbare fundamental
    assert _format_fundamental("X", {"naam": "X Corp", "valuta": "USD"}) is None


def test_kort_bedrag():
    assert _kort_bedrag(2.5e12) == "2.5T"
    assert _kort_bedrag(3.4e9) == "3.4mld"
    assert _kort_bedrag(7.8e6) == "7.8mln"
    assert _kort_bedrag(1234) == "1234"
