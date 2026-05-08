"""Testes dos filtros de qualidade."""

import pytest
from app.engine.filter import apply_filters


class TestApplyFilters:
    def test_all_filters_pass(self):
        result = apply_filters(ev=0.10, odd=2.00, overround=0.05)
        assert result.passed is True
        assert result.failures == []

    def test_ev_below_minimum_fails(self):
        result = apply_filters(ev=0.02, odd=2.00, overround=0.05)
        assert result.passed is False
        assert any("EV" in f for f in result.failures)

    def test_odd_below_minimum_fails(self):
        result = apply_filters(ev=0.10, odd=1.40, overround=0.05)
        assert result.passed is False
        assert any("baixa" in f for f in result.failures)

    def test_odd_above_maximum_fails(self):
        result = apply_filters(ev=0.10, odd=5.00, overround=0.05)
        assert result.passed is False
        assert any("alta" in f for f in result.failures)

    def test_overround_above_maximum_fails(self):
        result = apply_filters(ev=0.10, odd=2.00, overround=0.15)
        assert result.passed is False
        assert any("Margem" in f for f in result.failures)

    def test_line_movement_contrario_fails(self):
        result = apply_filters(
            ev=0.10, odd=2.10, overround=0.05,
            odd_30min_ago=2.00  # odd subiu: movimento contrário
        )
        assert result.passed is False
        assert any("movement" in f.lower() for f in result.failures)

    def test_line_movement_favoravel_passes(self):
        result = apply_filters(
            ev=0.10, odd=1.90, overround=0.05,
            odd_30min_ago=2.00  # odd caiu: favorável
        )
        assert result.passed is True

    def test_multiple_failures_collected(self):
        result = apply_filters(ev=0.01, odd=1.20, overround=0.05)
        assert result.passed is False
        assert len(result.failures) >= 2  # EV + odd baixa

    def test_steam_warning_generated(self):
        """Queda de odd > 0.10 desde abertura gera aviso de steam."""
        result = apply_filters(
            ev=0.08, odd=1.75, overround=0.05,
            odd_opening=1.90  # caiu 0.15
        )
        assert result.passed is True
        assert any("steam" in w.lower() for w in result.warnings)

    def test_high_ev_warning_generated(self):
        """EV > 20% gera aviso para verificar dados."""
        result = apply_filters(ev=0.25, odd=2.00, overround=0.05)
        assert result.passed is True
        assert any("EV" in w for w in result.warnings)

    def test_exact_boundary_ev(self):
        """EV exatamente no limite mínimo deve passar."""
        result = apply_filters(ev=0.04, odd=2.00, overround=0.05)
        assert result.passed is True

    def test_exact_boundary_odd_min(self):
        result = apply_filters(ev=0.05, odd=1.60, overround=0.05)
        assert result.passed is True

    def test_exact_boundary_odd_max(self):
        result = apply_filters(ev=0.05, odd=4.00, overround=0.05)
        assert result.passed is True

    def test_no_line_movement_data_passes(self):
        """Sem dados de line movement, não deve falhar."""
        result = apply_filters(ev=0.10, odd=2.00, overround=0.05)
        assert result.passed is True


class TestFilterWarnings:
    def test_no_warnings_on_normal_bet(self):
        result = apply_filters(ev=0.08, odd=2.00, overround=0.05)
        assert result.warnings == []

    def test_odd_rising_since_opening_warning(self):
        result = apply_filters(
            ev=0.08, odd=2.10, overround=0.05,
            odd_opening=2.00  # subiu levemente
        )
        assert any("subiu" in w for w in result.warnings)
