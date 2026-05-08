"""
Motor de cálculo do ValueBot.

Todas as fórmulas matemáticas centrais:
- Overround (margem da casa)
- Fair odds (odds sem margem)
- Expected Value (EV)
"""

from dataclasses import dataclass


@dataclass
class MarketAnalysis:
    """Resultado completo da análise de um mercado."""
    overround: float
    fair_probs: list[float]
    fair_odds: list[float]
    ev_per_side: list[float]
    best_side_index: int
    best_ev: float
    best_odd: float
    best_fair_odd: float
    best_prob_real: float


def calculate_overround(odds: list[float]) -> float:
    """
    Calcula o overround (margem) de um mercado.

    Overround = soma das probabilidades implícitas - 1
    Ex: odds [1.88, 1.90] → 1/1.88 + 1/1.90 = 1.0583 → overround = 5.83%
    """
    if not odds or any(o <= 1.0 for o in odds):
        raise ValueError("Odds devem ser maiores que 1.0")

    return sum(1 / o for o in odds) - 1.0


def calculate_fair_probs(odds: list[float]) -> list[float]:
    """Remove a margem da casa e retorna probabilidades justas (somam 1.0)."""
    raw_probs = [1 / o for o in odds]
    total = sum(raw_probs)
    return [p / total for p in raw_probs]


def calculate_fair_odds(odds: list[float]) -> list[float]:
    """Converte probabilidades justas de volta para odds."""
    fair_probs = calculate_fair_probs(odds)
    return [1 / p for p in fair_probs]


def calculate_ev(prob_real: float, odd: float) -> float:
    """
    Calcula o Expected Value de uma aposta.

    EV = (prob_real × lucro) - ((1 - prob_real) × stake)
    Para stake = 1: EV = (prob_real × (odd - 1)) - (1 - prob_real)
    """
    if not 0 < prob_real < 1:
        raise ValueError("prob_real deve estar entre 0 e 1")
    if odd <= 1.0:
        raise ValueError("odd deve ser maior que 1.0")

    lucro = odd - 1
    return (prob_real * lucro) - ((1 - prob_real) * 1.0)


def analyze_market(
    odds: list[float],
    probs_reais: list[float] | None = None,
) -> MarketAnalysis:
    """
    Análise completa de um mercado.

    Se probs_reais não for fornecido, usa as fair odds como proxy
    (assume que o modelo concorda com o mercado).
    """
    overround = calculate_overround(odds)
    fair_probs = calculate_fair_probs(odds)
    fair_odds = calculate_fair_odds(odds)

    probs = probs_reais if probs_reais else fair_probs
    ev_list = [calculate_ev(p, o) for p, o in zip(probs, odds)]
    best_idx = max(range(len(ev_list)), key=lambda i: ev_list[i])

    return MarketAnalysis(
        overround=overround,
        fair_probs=fair_probs,
        fair_odds=fair_odds,
        ev_per_side=ev_list,
        best_side_index=best_idx,
        best_ev=ev_list[best_idx],
        best_odd=odds[best_idx],
        best_fair_odd=fair_odds[best_idx],
        best_prob_real=probs[best_idx],
    )
