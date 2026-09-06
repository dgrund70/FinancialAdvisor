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
