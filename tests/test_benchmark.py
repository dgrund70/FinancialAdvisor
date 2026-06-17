"""Tests voor de deposit-matched benchmark-simulatie."""

from datetime import date

from helpers import benchmark_eindwaarde, xirr


def test_single_deposit_units_and_value():
    flows = [(date(2024, 1, 1), -1000)]            # storting 1000 @ koers 100 → 10 eenheden
    prijs_op = lambda d: 100.0 if d == date(2024, 1, 1) else 200.0
    eind, eenheden = benchmark_eindwaarde(flows, prijs_op, date(2025, 1, 1))
    assert abs(eenheden - 10) < 1e-9
    assert abs(eind - 2000) < 1e-9                 # 10 eenheden × 200


def test_deposit_then_withdrawal():
    # storting 1000 @100 → 10 eenheden; opname 300 @150 → 2 eenheden verkocht → 8
    flows = [(date(2024, 1, 1), -1000), (date(2024, 6, 1), 300)]
    prijzen = {date(2024, 1, 1): 100.0, date(2024, 6, 1): 150.0}
    prijs_op = lambda d: prijzen.get(d, 200.0)     # eindkoers 200
    eind, eenheden = benchmark_eindwaarde(flows, prijs_op, date(2025, 1, 1))
    assert abs(eenheden - 8) < 1e-9
    assert abs(eind - 1600) < 1e-9                 # 8 × 200


def test_missing_price_returns_none():
    eind, eenheden = benchmark_eindwaarde([(date(2024, 1, 1), -1000)],
                                          lambda d: None, date(2025, 1, 1))
    assert eind is None and eenheden is None


def test_benchmark_xirr_consistent():
    # 1000 erin @100, eindkoers 110 → eindwaarde 1100 → ~10% rendement
    flows = [(date(2024, 1, 1), -1000)]
    prijs_op = lambda d: 100.0 if d == date(2024, 1, 1) else 110.0
    eind, _ = benchmark_eindwaarde(flows, prijs_op, date(2025, 1, 1))
    r = xirr(flows + [(date(2025, 1, 1), eind)])
    assert r is not None and abs(r - 0.10) < 0.01
