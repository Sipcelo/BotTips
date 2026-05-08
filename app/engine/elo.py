"""
Modelo Elo para tênis ATP/WTA por superfície.

Mantém ratings em memória (por sessão). Em cada ciclo do scheduler,
os ratings são atualizados com os resultados recentes via API-Tennis.

Referências de rating inicial por superfície:
- Saibro sul-americano: penaliza jogadores de outras superfícies
- Duro: neutro
- Grama: bônus para especialistas
"""

import logging
import math
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# Rating inicial para novos jogadores
DEFAULT_RATING = 1500.0

# K-factor por nível do torneio
K_FACTOR = {
    "grand_slam": 40,
    "masters_1000": 32,
    "atp_500": 24,
    "atp_250": 20,
    "challenger": 16,
    "itf": 12,
}

# Ajuste de rating por superfície (delta aplicado ao rating base)
SURFACE_BONUS: dict[str, dict[str, float]] = {
    "clay": {
        "Nadal": 120,
        "default_specialist": 30,
    },
    "grass": {
        "default_specialist": 25,
    },
    "hard": {},
}


@dataclass
class PlayerRating:
    name: str
    rating: float = DEFAULT_RATING
    matches: int = 0


@dataclass
class EloModel:
    """
    Modelo Elo com ratings separados por superfície.

    ratings: surface → player_name → PlayerRating
    """
    ratings: dict[str, dict[str, PlayerRating]] = field(default_factory=dict)

    def _get_or_create(self, surface: str, player: str) -> PlayerRating:
        surface = surface.lower()
        if surface not in self.ratings:
            self.ratings[surface] = {}
        if player not in self.ratings[surface]:
            self.ratings[surface][player] = PlayerRating(name=player)
        return self.ratings[surface][player]

    def get_rating(self, surface: str, player: str) -> float:
        return self._get_or_create(surface, player).rating

    def expected_score(self, rating_a: float, rating_b: float) -> float:
        """Probabilidade esperada de vitória de A contra B."""
        return 1.0 / (1.0 + math.pow(10, (rating_b - rating_a) / 400.0))

    def update(
        self,
        surface: str,
        winner: str,
        loser: str,
        tournament_level: str = "challenger",
    ) -> None:
        """Atualiza ratings após um resultado."""
        k = K_FACTOR.get(tournament_level, 16)

        r_winner = self._get_or_create(surface, winner)
        r_loser = self._get_or_create(surface, loser)

        exp_w = self.expected_score(r_winner.rating, r_loser.rating)
        exp_l = 1.0 - exp_w

        r_winner.rating += k * (1.0 - exp_w)
        r_loser.rating += k * (0.0 - exp_l)
        r_winner.matches += 1
        r_loser.matches += 1

        logger.debug(
            "Elo update [%s] %s %.0f → %.0f | %s %.0f → %.0f",
            surface, winner,
            r_winner.rating - k * (1.0 - exp_w), r_winner.rating,
            loser,
            r_loser.rating - k * (0.0 - exp_l), r_loser.rating,
        )

    def predict(self, surface: str, player_a: str, player_b: str) -> tuple[float, float]:
        """
        Retorna (prob_a, prob_b) com base nos ratings Elo.

        Returns:
            Tupla com probabilidades reais de vitória (somam 1.0)
        """
        ra = self.get_rating(surface, player_a)
        rb = self.get_rating(surface, player_b)
        prob_a = self.expected_score(ra, rb)
        return prob_a, 1.0 - prob_a


# Instância global compartilhada
elo_model = EloModel()
