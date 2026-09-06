"""Tests voor de Nederlandse getalnotatie in de Jinja-filters."""

import pytest

from app import bedrag_nl, pct_nl


@pytest.mark.parametrize(("waarde", "valuta", "verwacht"), [
    (None, "EUR", "—"),
    (0, "EUR", "€\u00a00,00"),
    (-1234.5, "EUR", "€\u00a0-1.234,50"),
    (1234567.89, "EUR", "€\u00a01.234.567,89"),
    (234567.89, "USD", "234.567,89\u00a0USD"),
])
def test_bedrag_nl(waarde, valuta, verwacht):
    assert bedrag_nl(waarde, valuta) == verwacht


@pytest.mark.parametrize(("waarde", "decimalen", "verwacht"), [
    (None, 1, "—"),
    (0, 1, "0,0%"),
    (-12.34, 1, "-12,3%"),
    (12.34, 2, "+12,34%"),
    (1234567.89, 1, "+1234567,9%"),
])
def test_pct_nl(waarde, decimalen, verwacht):
    assert pct_nl(waarde, decimalen) == verwacht


def test_pp_nl_gebruikt_nederlandse_notatie():
    from app import pp_nl
    assert pp_nl(12.5) == "+12,5 pp"
    assert pp_nl(-2.1) == "-2,1 pp"
    assert pp_nl(0) == "0,0 pp"
    assert pp_nl(None) == "—"


def test_relatieve_tijd_en_marktstatus():
    from datetime import datetime
    from app import koersen_status

    # Zondagavond: beurs dicht, laatste handelsdag is vrijdag.
    st = koersen_status("2026-09-06T20:45:00", datetime(2026, 9, 6, 21, 30))
    assert st["relatief"] == "45 min geleden"
    assert st["markt_open"] is False
    assert st["markt"] == "markt gesloten (slotkoers vrijdag)"

    # Maandagochtend tijdens handelsuren.
    st = koersen_status("2026-09-07T10:15:00", datetime(2026, 9, 7, 11, 0))
    assert st["markt_open"] is True
    assert st["markt"] == "markt open"
    assert st["relatief"] == "45 min geleden"

    # Ouder dan een dag.
    st = koersen_status("2026-09-04T12:00:00", datetime(2026, 9, 7, 12, 0))
    assert st["relatief"] == "3 dagen geleden"

    # Geen tijdstempel: alleen marktstatus.
    st = koersen_status(None, datetime(2026, 9, 7, 11, 0))
    assert st["relatief"] is None
    assert st["markt_open"] is True
