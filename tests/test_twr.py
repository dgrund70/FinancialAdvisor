"""Tests voor tijd-gewogen rendement (TWR) en annualisatie."""

from helpers import twr, annualiseer


def test_twr_zonder_flows():
    # 100 → 110 → 99 : (1.10)(0.90) - 1 = -0.01
    r = twr([100, 110, 99], [0, 0, 0])
    assert abs(r - (-0.01)) < 1e-9


def test_twr_negeert_storting():
    # dag1 +10%; dag2 storting 500 (einde dag), basis 1100 groeit 10% → 1210, +500 = 1710
    # ketenrendement = 1.10 × 1.10 - 1 = 0.21, ongeacht de storting
    r = twr([1000, 1100, 1710], [0, 0, 500])
    assert abs(r - 0.21) < 1e-9


def test_twr_te_weinig():
    assert twr([1000], [0]) is None
    assert twr([], []) is None


def test_annualiseer():
    assert abs(annualiseer(0.21, 365) - 0.21) < 1e-9          # precies 1 jaar
    assert abs(annualiseer(0.10, 730) - ((1.1) ** 0.5 - 1)) < 1e-9  # 2 jaar
    assert annualiseer(0.1, 0) is None
    assert annualiseer(-1.5, 365) is None                     # totaal verlies > 100%
