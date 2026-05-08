"""Testes do modelo Elo por superfície."""

import pytest
from app.engine.elo import EloModel, DEFAULT_RATING


class TestEloPredict:
    def test_new_players_equal_probability(self):
        """Dois jogadores sem histórico devem ter 50/50."""
        model = EloModel()
        prob_a, prob_b = model.predict("clay", "Player A", "Player B")
        assert abs(prob_a - 0.5) < 0.001
        assert abs(prob_b - 0.5) < 0.001

    def test_probs_sum_to_one(self):
        model = EloModel()
        prob_a, prob_b = model.predict("hard", "Djokovic", "Federer")
        assert abs(prob_a + prob_b - 1.0) < 0.0001

    def test_winner_rating_increases(self):
        model = EloModel()
        before = model.get_rating("clay", "Nadal")
        model.update("clay", winner="Nadal", loser="Djokovic")
        after = model.get_rating("clay", "Nadal")
        assert after > before

    def test_loser_rating_decreases(self):
        model = EloModel()
        before = model.get_rating("clay", "Djokovic")
        model.update("clay", winner="Nadal", loser="Djokovic")
        after = model.get_rating("clay", "Djokovic")
        assert after < before

    def test_rating_sum_conserved(self):
        """A soma dos ratings deve ser conservada após um confronto."""
        model = EloModel()
        sum_before = (
            model.get_rating("hard", "A") + model.get_rating("hard", "B")
        )
        model.update("hard", winner="A", loser="B")
        sum_after = (
            model.get_rating("hard", "A") + model.get_rating("hard", "B")
        )
        assert abs(sum_before - sum_after) < 0.001

    def test_surface_isolation(self):
        """Ratings de superfícies diferentes não devem interferir."""
        model = EloModel()
        model.update("clay", winner="Nadal", loser="Murray")
        clay_rating = model.get_rating("clay", "Nadal")
        hard_rating = model.get_rating("hard", "Nadal")
        assert clay_rating != hard_rating  # clay foi atualizado, hard permanece default

    def test_higher_rated_has_more_than_50pct(self):
        """Após vencer 5 partidas, o favorito deve ter >50% de chance."""
        model = EloModel()
        for _ in range(5):
            model.update("grass", winner="Federer", loser="Opponent")
        prob_federer, prob_opponent = model.predict("grass", "Federer", "Opponent")
        assert prob_federer > 0.5

    def test_default_rating(self):
        model = EloModel()
        assert model.get_rating("clay", "Unknown Player") == DEFAULT_RATING

    def test_matches_counter_increments(self):
        model = EloModel()
        model.update("hard", winner="A", loser="B")
        model.update("hard", winner="A", loser="C")
        assert model.ratings["hard"]["A"].matches == 2

    def test_tournament_level_k_factor(self):
        """Grand Slam deve alterar o rating mais do que Challenger."""
        model_gs = EloModel()
        model_ch = EloModel()
        model_gs.update("hard", winner="A", loser="B", tournament_level="grand_slam")
        model_ch.update("hard", winner="A", loser="B", tournament_level="challenger")
        delta_gs = model_gs.get_rating("hard", "A") - DEFAULT_RATING
        delta_ch = model_ch.get_rating("hard", "A") - DEFAULT_RATING
        assert delta_gs > delta_ch
