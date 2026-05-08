"""
Testes do motor de cálculo.

Casos reais usados como referência:
- Dellien (1.88) vs Hardt (1.90) — Santos Challenger
- Justo (1.52) vs Rodriguez (2.50)
- Pucinelli (1.50) vs Aboian (2.52)
"""

import pytest
from app.engine.calculator import (
    calculate_overround,
    calculate_fair_probs,
    calculate_fair_odds,
    calculate_ev,
    analyze_market,
)


class TestOverround:
    def test_dellien_hardt(self):
        """Betano: Dellien 1.88 / Hardt 1.90 → ~5.8%"""
        result = calculate_overround([1.88, 1.90])
        assert abs(result - 0.0580) < 0.001

    def test_justo_rodriguez(self):
        """Betano: Justo 1.52 / Rodriguez 2.50 → ~5.8%"""
        result = calculate_overround([1.52, 2.50])
        assert abs(result - 0.0580) < 0.001

    def test_pucinelli_aboian(self):
        """Betano: Pucinelli 1.50 / Aboian 2.52 → ~6.4%"""
        result = calculate_overround([1.50, 2.52])
        assert abs(result - 0.0640) < 0.001

    def test_perfect_market(self):
        """Mercado sem margem: odds 2.0 e 2.0 → 0%"""
        result = calculate_overround([2.0, 2.0])
        assert abs(result - 0.0) < 0.001

    def test_three_way_market(self):
        """Mercado 1X2 com três resultados"""
        result = calculate_overround([2.10, 3.20, 3.50])
        assert result > 0.0

    def test_invalid_odd_raises(self):
        with pytest.raises(ValueError):
            calculate_overround([0.5, 2.0])

    def test_empty_odds_raises(self):
        with pytest.raises((ValueError, ZeroDivisionError)):
            calculate_overround([])


class TestFairProbs:
    def test_dellien_prob_real(self):
        """Betano Dellien 1.88 → prob implícita ~50.3% após normalização"""
        probs = calculate_fair_probs([1.88, 1.90])
        assert abs(probs[0] - 0.503) < 0.002
        assert abs(probs[1] - 0.497) < 0.002

    def test_probs_sum_to_one(self):
        """Fair probs devem sempre somar 1.0"""
        probs = calculate_fair_probs([1.52, 2.50])
        assert abs(sum(probs) - 1.0) < 0.0001

    def test_favorite_has_higher_prob(self):
        """Favorito (odd menor) deve ter prob maior"""
        probs = calculate_fair_probs([1.52, 2.50])
        assert probs[0] > probs[1]


class TestFairOdds:
    def test_fair_odds_always_better(self):
        """Fair odds devem ser maiores que odds com margem"""
        odds = [1.88, 1.90]
        fair = calculate_fair_odds(odds)
        assert all(f > o for f, o in zip(fair, odds))

    def test_fair_odds_reciprocal(self):
        """Fair odds devem refletir as fair probs"""
        odds = [1.52, 2.50]
        probs = calculate_fair_probs(odds)
        fair = calculate_fair_odds(odds)
        for prob, fair_odd in zip(probs, fair):
            assert abs(1 / fair_odd - prob) < 0.0001


class TestEV:
    def test_dellien_ev_positive(self):
        """
        Dellien: prob_real=0.78, odd=1.88
        EV = (0.78 × 0.88) - (0.22 × 1.0) = 0.686 - 0.22 = 0.466
        """
        ev = calculate_ev(0.78, 1.88)
        assert abs(ev - 0.466) < 0.001

    def test_justo_ev_positive(self):
        """
        Justo: prob_real=0.80, odd=1.52
        EV = (0.80 × 0.52) - (0.20 × 1.0) = 0.416 - 0.20 = 0.216
        """
        ev = calculate_ev(0.80, 1.52)
        assert abs(ev - 0.216) < 0.001

    def test_pucinelli_ev_positive(self):
        """
        Pucinelli: prob_real=0.72, odd=1.50
        EV = (0.72 × 0.50) - (0.28 × 1.0) = 0.36 - 0.28 = 0.08
        """
        ev = calculate_ev(0.72, 1.50)
        assert abs(ev - 0.08) < 0.001

    def test_negative_ev(self):
        """Apostador perdedor: prob_real=0.40, odd=1.88 → EV negativo"""
        ev = calculate_ev(0.40, 1.88)
        assert ev < 0

    def test_invalid_prob_raises(self):
        with pytest.raises(ValueError):
            calculate_ev(1.5, 2.0)

    def test_invalid_odd_raises(self):
        with pytest.raises(ValueError):
            calculate_ev(0.5, 0.9)


class TestAnalyzeMarket:
    def test_dellien_full_analysis(self):
        """Análise completa do mercado Dellien vs Hardt"""
        result = analyze_market(
            odds=[1.88, 1.90],
            probs_reais=[0.78, 0.22]
        )
        assert result.best_side_index == 0          # Dellien
        assert abs(result.best_ev - 0.466) < 0.001  # EV +46.6%
        assert result.best_odd == 1.88
        assert abs(result.overround - 0.058) < 0.001

    def test_without_model_probs(self):
        """Sem probs do modelo, usa fair probs (EV ~0)"""
        result = analyze_market(odds=[1.88, 1.90])
        # Com fair probs, EV deve ser levemente negativo (margem da casa)
        assert result.best_ev < 0.01
