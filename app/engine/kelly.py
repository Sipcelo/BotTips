"""
Kelly Criterion para sizing de apostas no ValueBot.

Usa sempre Kelly ¼ (fracionado) para reduzir variância.
"""

from dataclasses import dataclass

from app.config import get_settings


@dataclass
class KellyResult:
    """Resultado do cálculo de sizing."""
    kelly_full: float
    kelly_fraction: float
    stake_pct: float
    stake_amount: float
    bankroll: float


def calculate_kelly(
    prob_real: float,
    odd: float,
    bankroll: float,
    fraction: float | None = None,
    max_stake_pct: float | None = None,
) -> KellyResult:
    """
    Calcula o stake ótimo usando Kelly Criterion fracionado.

    K = (prob_real × odd - 1) / (odd - 1)
    K_frac = K × fraction

    Stake nunca excede max_stake_pct da banca.
    """
    settings = get_settings()
    fraction = fraction or settings.kelly_fraction
    max_pct = max_stake_pct or settings.max_stake_pct

    if not 0 < prob_real < 1:
        raise ValueError("prob_real deve estar entre 0 e 1")
    if odd <= 1.0:
        raise ValueError("odd deve ser maior que 1.0")
    if bankroll <= 0:
        raise ValueError("bankroll deve ser positivo")

    kelly_full = (prob_real * odd - 1) / (odd - 1)

    if kelly_full <= 0:
        return KellyResult(
            kelly_full=kelly_full,
            kelly_fraction=0.0,
            stake_pct=0.0,
            stake_amount=0.0,
            bankroll=bankroll,
        )

    kelly_frac = kelly_full * fraction
    stake_pct = min(kelly_frac, max_pct)
    stake_raw = bankroll * stake_pct

    # Clama dentro do range configurado (min/max stake em reais)
    min_stake = settings.min_stake_amount
    max_stake = settings.max_stake_amount
    stake_final = round(max(min_stake, min(stake_raw, max_stake)), 2)

    return KellyResult(
        kelly_full=kelly_full,
        kelly_fraction=kelly_frac,
        stake_pct=stake_pct,
        stake_amount=stake_final,
        bankroll=bankroll,
    )
