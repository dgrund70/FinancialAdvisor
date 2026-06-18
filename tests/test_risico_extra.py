"""Tests voor Sharpe-ratio en correlatie."""

from helpers import sharpe, correlatie


def test_correlatie_identiek():
    r = [0.01, -0.02, 0.03, -0.01, 0.02]
    assert abs(correlatie(r, r) - 1.0) < 1e-9


def test_correlatie_gespiegeld():
    r = [0.01, -0.02, 0.03, -0.01, 0.02]
    neg = [-x for x in r]
    assert abs(correlatie(r, neg) - (-1.0)) < 1e-9


def test_correlatie_geen_variantie_of_te_weinig():
    assert correlatie([0.01, 0.01, 0.01], [0.02, 0.03, 0.01]) is None  # a constant
    assert correlatie([0.01], [0.02]) is None


def test_sharpe_positief_bij_winst():
    r = [0.01, -0.005, 0.012, -0.004, 0.008, 0.002]
    s = sharpe(r, rf=0.0)
    assert s is not None and s > 0


def test_sharpe_daalt_met_hogere_rf():
    r = [0.01, -0.005, 0.012, -0.004, 0.008, 0.002]
    assert sharpe(r, rf=0.0) > sharpe(r, rf=0.10)


def test_sharpe_geen_volatiliteit_of_te_weinig():
    assert sharpe([0.01, 0.01, 0.01]) is None   # vol 0
    assert sharpe([0.01]) is None
