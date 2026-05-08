"""Testes do Kelly Criterion."""

import pytest
from app.engine.kelly import calculate_kelly


class TestKelly:
    def test_dellien_sizing(self):
        """
        Dellien: prob=0.78, odd=1.88, banca=50
        Kelly completo = (0.78*1.88 - 1) / (1.88 - 1) = 0.4664/0.88 ≈ 0.530
        Kelly ¼ = 0.530 * 0.25 = 0.1325 → teto 3% → stake = R$1.50
        """
        result = calculate_kelly(prob_real=0.78, odd=1.88, bankroll=50.0)
        assert abs(result.kelly_full - 0.530) < 0.01
        assert result.stake_pct == pytest.approx(0.03, abs=0.001)  # teto de 3%
        assert result.stake_amount == pytest.approx(1.50, abs=0.01)

    def test_negative_ev_returns_zero_stake(self):
        """Kelly negativo (EV negativo) deve retornar stake=0."""
        result = calculate_kelly(prob_real=0.30, odd=1.50, bankroll=100.0)
        assert result.kelly_full < 0
        assert result.stake_pct == 0.0
        assert result.stake_amount == 0.0

    def test_max_stake_cap(self):
        """Stake não deve ultrapassar 3% da banca."""
        result = calculate_kelly(prob_real=0.90, odd=3.00, bankroll=1000.0)
        assert result.stake_pct <= 0.03
        assert result.stake_amount <= 30.0

    def test_custom_fraction(self):
        """Kelly ½ deve resultar em stake maior que Kelly ¼."""
        r_quarter = calculate_kelly(0.60, 2.00, 100.0, fraction=0.25)
        r_half = calculate_kelly(0.60, 2.00, 100.0, fraction=0.50)
        assert r_half.kelly_fraction >= r_quarter.kelly_fraction

    def test_stake_amount_rounds_to_cents(self):
        result = calculate_kelly(0.65, 2.10, 73.33)
        # stake_amount deve ter no máximo 2 casas decimais
        assert result.stake_amount == round(result.stake_amount, 2)

    def test_invalid_prob_below_zero(self):
        with pytest.raises(ValueError):
            calculate_kelly(prob_real=-0.1, odd=2.0, bankroll=100.0)

    def test_invalid_prob_above_one(self):
        with pytest.raises(ValueError):
            calculate_kelly(prob_real=1.1, odd=2.0, bankroll=100.0)

    def test_invalid_odd(self):
        with pytest.raises(ValueError):
            calculate_kelly(prob_real=0.6, odd=0.9, bankroll=100.0)

    def test_invalid_bankroll(self):
        with pytest.raises(ValueError):
            calculate_kelly(prob_real=0.6, odd=2.0, bankroll=0.0)

    def test_bankroll_reflected_in_result(self):
        result = calculate_kelly(0.65, 2.0, bankroll=200.0)
        assert result.bankroll == 200.0


class TestKellyEdgeCases:
    def test_prob_exactly_break_even(self):
        """prob = 1/odd → Kelly = 0 (break-even)."""
        odd = 2.0
        prob = 1 / odd  # exatamente break-even
        result = calculate_kelly(prob_real=prob, odd=odd, bankroll=100.0)
        assert result.kelly_full == pytest.approx(0.0, abs=0.001)

    def test_very_low_bankroll(self):
        result = calculate_kelly(0.60, 2.0, bankroll=1.0)
        assert result.stake_amount >= 0
        assert result.stake_amount <= 1.0
