"""
Dixon-Coles model para futebol europeu.

Ref: Dixon, Coles (1997) "Modelling Association Football Scores and
     Inefficiencies in the Football Betting Market"

Fluxo:
    1. Coleta resultados históricos (Football-Data.co.uk)
    2. Ajusta parâmetros por MLE com decaimento temporal
    3. Gera matriz de placar → probabilidades 1X2, OU, BTTS

Regra: aplicar APENAS a futebol. Ver anti_patterns.md — ρ não existe
em outros esportes.
"""

import math
import logging
from dataclasses import dataclass, field
from datetime import date

import numpy as np
from scipy.optimize import minimize
from scipy.stats import poisson

logger = logging.getLogger(__name__)


@dataclass
class MatchResult:
    """Resultado de uma partida para ajuste do modelo."""
    home_team: str
    away_team: str
    home_goals: int
    away_goals: int
    match_date: date


@dataclass
class DixonColesModel:
    """Parâmetros ajustados para uma liga."""
    attack: dict[str, float]    # α_team — força ofensiva
    defense: dict[str, float]   # β_team — fragilidade defensiva (> 1 = mais fraco)
    gamma: float                 # vantagem de jogar em casa
    rho: float                   # correção low-score (tipicamente -0.1 a -0.2)
    teams: list[str] = field(default_factory=list)
    fitted_on: date | None = None
    n_matches: int = 0


@dataclass
class FootballProbabilities:
    """Probabilidades de resultado derivadas do score matrix."""
    home_win: float
    draw: float
    away_win: float
    over_25: float
    btts: float


# ---------------------------------------------------------------------------
# Correção Dixon-Coles para baixos placares
# ---------------------------------------------------------------------------

def _tau(x: int, y: int, lam: float, mu: float, rho: float) -> float:
    """
    Fator de correção τ para a célula (x, y) do score matrix.

    Aplicado apenas para (0,0), (1,0), (0,1), (1,1) — os outros = 1.
    τ < 0 invalida o modelo; bounded na otimização via rho ≤ 0.
    """
    if x == 0 and y == 0:
        return 1.0 - lam * mu * rho
    if x == 1 and y == 0:
        return 1.0 + mu * rho
    if x == 0 and y == 1:
        return 1.0 + lam * rho
    if x == 1 and y == 1:
        return 1.0 - rho
    return 1.0


# ---------------------------------------------------------------------------
# Score matrix e derivações
# ---------------------------------------------------------------------------

def score_matrix(
    home_team: str,
    away_team: str,
    model: DixonColesModel,
    max_goals: int = 10,
) -> np.ndarray:
    """
    Calcula a matriz (max_goals+1) × (max_goals+1) de P(home=i, away=j).

    Times desconhecidos recebem rating médio (1.0) — confiança baixa.
    """
    alpha_h = model.attack.get(home_team, 1.0)
    beta_h = model.defense.get(home_team, 1.0)
    alpha_a = model.attack.get(away_team, 1.0)
    beta_a = model.defense.get(away_team, 1.0)

    lam = alpha_h * beta_a * model.gamma   # expected home goals
    mu = alpha_a * beta_h                   # expected away goals

    lam = max(lam, 1e-6)
    mu = max(mu, 1e-6)

    matrix = np.zeros((max_goals + 1, max_goals + 1))
    for i in range(max_goals + 1):
        for j in range(max_goals + 1):
            matrix[i][j] = (
                _tau(i, j, lam, mu, model.rho)
                * poisson.pmf(i, lam)
                * poisson.pmf(j, mu)
            )

    total = matrix.sum()
    if total > 0:
        matrix /= total

    return matrix


def predict(
    home_team: str,
    away_team: str,
    model: DixonColesModel,
) -> FootballProbabilities:
    """
    Probabilidades de resultado 1X2 + Over 2.5 + BTTS.

    Returns:
        FootballProbabilities com todos os mercados derivados.
    """
    m = score_matrix(home_team, away_team, model)

    # 1X2
    p_home = float(np.sum(np.tril(m, -1)))   # home_goals > away_goals
    p_away = float(np.sum(np.triu(m, 1)))    # away_goals > home_goals
    p_draw = float(np.trace(m))

    # Over 2.5
    p_over25 = 0.0
    n = m.shape[0]
    for i in range(n):
        for j in range(n):
            if i + j > 2:
                p_over25 += m[i][j]

    # BTTS
    p_btts = 0.0
    for i in range(1, n):
        for j in range(1, n):
            p_btts += m[i][j]

    return FootballProbabilities(
        home_win=p_home,
        draw=p_draw,
        away_win=p_away,
        over_25=p_over25,
        btts=p_btts,
    )


# ---------------------------------------------------------------------------
# Fitting via MLE
# ---------------------------------------------------------------------------

def fit(
    matches: list[MatchResult],
    time_decay_xi: float = 0.0018,
    gamma_prior: float = 1.30,
    rho_prior: float = -0.13,
) -> DixonColesModel:
    """
    Ajusta o modelo Dixon-Coles por máxima verossimilhança.

    Args:
        matches: histórico de partidas ordenado por data
        time_decay_xi: taxa de decaimento exponencial por dia
                       (0.0018 → match de 1 ano atrás = 50% do peso)
        gamma_prior: ponto inicial para home advantage
        rho_prior: ponto inicial para ρ (deve ser negativo)

    Returns:
        DixonColesModel ajustado

    Raises:
        ValueError: se matches estiver vazio
    """
    if not matches:
        raise ValueError("Sem partidas para ajustar o modelo")

    teams = sorted({m.home_team for m in matches} | {m.away_team for m in matches})
    n = len(teams)
    idx = {t: i for i, t in enumerate(teams)}
    today = date.today()

    def weight(m: MatchResult) -> float:
        days = (today - m.match_date).days
        return math.exp(-time_decay_xi * days)

    def neg_log_lik(params: np.ndarray) -> float:
        attack = params[:n]
        defense = params[n: 2 * n]
        gamma = params[2 * n]
        rho = params[2 * n + 1]

        log_l = 0.0
        for m in matches:
            w = weight(m)
            i, j = idx[m.home_team], idx[m.away_team]

            lam = attack[i] * defense[j] * gamma
            mu = attack[j] * defense[i]

            if lam <= 0 or mu <= 0:
                return 1e10

            tau = _tau(m.home_goals, m.away_goals, lam, mu, rho)
            if tau <= 0:
                return 1e10

            log_l += w * (
                math.log(tau)
                + m.home_goals * math.log(lam) - lam - math.lgamma(m.home_goals + 1)
                + m.away_goals * math.log(mu) - mu - math.lgamma(m.away_goals + 1)
            )

        return -log_l

    x0 = np.ones(2 * n + 2)
    x0[2 * n] = gamma_prior
    x0[2 * n + 1] = rho_prior

    # Identifiabilidade: média dos ataques = 1
    constraints = [{"type": "eq", "fun": lambda p: p[:n].mean() - 1.0}]

    bounds = (
        [(0.05, 5.0)] * n       # attack
        + [(0.05, 5.0)] * n     # defense
        + [(1.0, 2.0)]          # gamma
        + [(-0.5, -0.001)]      # rho — sempre negativo
    )

    result = minimize(
        neg_log_lik,
        x0,
        method="L-BFGS-B",
        bounds=bounds,
        constraints=constraints,
        options={"maxiter": 2000, "ftol": 1e-9},
    )

    params = result.x

    model = DixonColesModel(
        attack={t: float(params[idx[t]]) for t in teams},
        defense={t: float(params[n + idx[t]]) for t in teams},
        gamma=float(params[2 * n]),
        rho=float(params[2 * n + 1]),
        teams=teams,
        fitted_on=today,
        n_matches=len(matches),
    )

    logger.info(
        "DC ajustado: %d times, %d partidas, γ=%.3f, ρ=%.3f, convergiu=%s",
        len(teams), len(matches), model.gamma, model.rho, result.success,
    )
    return model
