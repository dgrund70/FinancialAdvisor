"""Tests voor de pure XIRR-helper."""

from datetime import date

from helpers import xirr


def test_simple_one_year_return():
    # 1000 erin, 1100 eruit na ~1 jaar → ~10% geld-gewogen rendement
    r = xirr([(date(2024, 1, 1), -1000), (date(2025, 1, 1), 1100)])
    assert r is not None
    assert abs(r - 0.10) < 0.01


def test_multiple_deposits():
    # twee stortingen, eindwaarde hoger dan totale inleg → positief rendement
    r = xirr([
        (date(2023, 1, 1), -1000),
        (date(2023, 7, 1), -1000),
        (date(2024, 1, 1), 2200),
    ])
    assert r is not None
    assert r > 0


def test_no_sign_change_returns_none():
    assert xirr([(date(2024, 1, 1), -1000), (date(2025, 1, 1), -100)]) is None


def test_too_few_flows_returns_none():
    assert xirr([(date(2024, 1, 1), -1000)]) is None


def test_all_same_day_returns_none():
    # zonder tijdsverloop is het rendement ongedefinieerd
    assert xirr([(date(2024, 1, 1), -1000), (date(2024, 1, 1), 1100)]) is None
