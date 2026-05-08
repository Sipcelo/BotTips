"""
Filtros de qualidade do ValueBot.

Uma aposta só gera alerta se PASSAR em todos os filtros.
"""

from dataclasses import dataclass, field

from app.config import get_settings


@dataclass
class FilterResult:
    """Resultado da aplicação dos filtros."""
    passed: bool
    failures: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def apply_filters(
    ev: float,
    odd: float,
    overround: float,
    odd_opening: float | None = None,
    odd_30min_ago: float | None = None,
) -> FilterResult:
    """
    Aplica todos os filtros de qualidade a uma oportunidade.

    Filtros obrigatórios (falha = descarta):
        1. EV >= MIN_EV (default 4%)
        2. odd >= MIN_ODD (default 1.60)
        3. odd <= MAX_ODD (default 4.00)
        4. overround <= MAX_MARGIN (default 10%)
        5. Sem line movement contrário (odd não pode ter subido)
    """
    settings = get_settings()
    failures: list[str] = []
    warnings: list[str] = []

    if ev < settings.min_ev:
        failures.append(
            f"EV insuficiente: {ev:.1%} < mínimo {settings.min_ev:.1%}"
        )

    if odd < settings.min_odd:
        failures.append(
            f"Odd muito baixa: {odd:.2f} < mínimo {settings.min_odd:.2f}"
        )

    if odd > settings.max_odd:
        failures.append(
            f"Odd muito alta (longshot): {odd:.2f} > máximo {settings.max_odd:.2f}"
        )

    if overround > settings.max_margin:
        failures.append(
            f"Margem da casa muito alta: {overround:.1%} > máximo {settings.max_margin:.1%}"
        )

    if odd_30min_ago is not None and odd > odd_30min_ago:
        failures.append(
            f"Line movement contrário: odd subiu de {odd_30min_ago:.2f} → {odd:.2f}"
        )

    if odd_opening is not None:
        movement = odd - odd_opening
        if movement < -0.10:
            warnings.append(
                f"Steam detectado: odd caiu {abs(movement):.2f} desde abertura "
                f"({odd_opening:.2f} → {odd:.2f})"
            )
        elif movement > 0.05:
            warnings.append(
                f"Odd subiu levemente desde abertura ({odd_opening:.2f} → {odd:.2f})"
            )

    if ev > 0.20:
        warnings.append(f"EV muito alto ({ev:.1%}) — verificar dados antes de apostar")

    return FilterResult(
        passed=len(failures) == 0,
        failures=failures,
        warnings=warnings,
    )
