"""Tests voor de pure risico-statistiek."""

from helpers import dagrendementen, volatiliteit, max_drawdown, beta


def test_dagrendementen():
    r = dagrendementen([100, 110, 99])
    assert abs(r[0] - 0.10) < 1e-9
    assert abs(r[1] - (-0.10)) < 1e-9


def test_max_drawdown():
    # 100 → 120 → 60 → 90 : grootste daling = 60/120 - 1 = -0.5
    assert abs(max_drawdown([100, 120, 60, 90]) - (-0.5)) < 1e-9
    assert max_drawdown([100]) is None


def test_volatiliteit_zero_for_constant_returns():
    v = volatiliteit([0.01, 0.01, 0.01, 0.01])
    assert v is not None and abs(v) < 1e-9


def test_volatiliteit_known():
    r = [0.01, -0.01, 0.01, -0.01]          # mean 0, var = 0.0004/3
    v = volatiliteit(r, periodes=1)          # zonder annualisatie
    assert abs(v - (0.0004 / 3) ** 0.5) < 1e-9


def test_beta_identical_is_one():
    r = [0.01, -0.02, 0.03, -0.01, 0.02]
    assert abs(beta(r, r) - 1.0) < 1e-9


def test_beta_double_is_two():
    b = [0.01, -0.02, 0.03, -0.01, 0.02]
    p = [2 * x for x in b]
    assert abs(beta(p, b) - 2.0) < 1e-9


def test_too_few_returns_none():
    assert volatiliteit([0.01]) is None
    assert beta([0.01], [0.01]) is None
