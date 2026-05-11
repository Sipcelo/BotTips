"""
Cache em memória dos modelos Dixon-Coles por liga.

Modelos são ajustados no startup e re-ajustados semanalmente.
Se o ajuste falha, o bot cai de volta para Pinnacle devig puro —
nunca bloqueia alertas por falta de modelo.
"""

import logging
from datetime import date

from app.engine.dixon_coles import DixonColesModel

logger = logging.getLogger(__name__)

# Esportes com liga de futebol monitorada
FOOTBALL_LEAGUES = [
    "soccer_epl",
    "soccer_spain_la_liga",
    "soccer_germany_bundesliga",
    "soccer_italy_serie_a",
    "soccer_france_ligue_1",
    "soccer_brazil_campeonato",
]

# Liga → modelo ajustado
_models: dict[str, DixonColesModel] = {}

# Home advantage por liga (prior — usado se modelo não disponível)
LEAGUE_GAMMA_PRIOR: dict[str, float] = {
    "soccer_epl": 1.30,
    "soccer_spain_la_liga": 1.35,
    "soccer_germany_bundesliga": 1.25,
    "soccer_italy_serie_a": 1.30,
    "soccer_france_ligue_1": 1.30,
    "soccer_brazil_campeonato": 1.45,
}


def get_model(league_key: str) -> DixonColesModel | None:
    """Retorna o modelo ajustado, ou None se não disponível."""
    return _models.get(league_key)


def store_model(league_key: str, model: DixonColesModel) -> None:
    """Armazena modelo ajustado em memória."""
    _models[league_key] = model
    logger.info(
        "Modelo DC armazenado [%s]: %d times, γ=%.3f, ρ=%.3f, %d partidas",
        league_key, len(model.teams), model.gamma, model.rho, model.n_matches,
    )


def needs_refit(league_key: str, max_age_days: int = 7) -> bool:
    """Retorna True se modelo não existe ou tem mais de max_age_days."""
    model = _models.get(league_key)
    if model is None or model.fitted_on is None:
        return True
    return (date.today() - model.fitted_on).days >= max_age_days


def is_football(sport_key: str) -> bool:
    """True se o esporte é futebol (qualquer liga)."""
    return sport_key.startswith("soccer_")
