"""
Formatação de alertas para Telegram.

Monta a mensagem completa com emojis, EV, sizing e line movement
conforme o formato definido no CLAUDE.md.
"""

from dataclasses import dataclass
from datetime import datetime, timezone, timedelta

BRT = timezone(timedelta(hours=-3))


SPORT_EMOJI = {
    "tennis_atp": "🎾",
    "tennis_wta": "🎾",
    "table_tennis": "🏓",
    "handball": "🤾",
    "baseball_mlb": "⚾",
}

SPORT_LABEL = {
    "tennis_atp": "Tênis ATP",
    "tennis_wta": "Tênis WTA",
    "table_tennis": "Tênis de Mesa",
    "handball": "Handebol EHF",
    "baseball_mlb": "MLB",
}


@dataclass
class AlertData:
    # Evento
    sport: str
    tournament: str
    home_team: str
    away_team: str
    surface: str | None
    commence_time: datetime

    # Análise
    bookmaker: str
    side: str          # "home" | "away"
    odd: float
    fair_odd: float
    ev: float
    prob_real: float
    overround: float

    # Sizing
    bankroll: float
    kelly_fraction: float
    stake_pct: float
    stake_amount: float

    # Line movement
    odd_opening: float | None = None
    odd_30min_ago: float | None = None

    # Contexto qualitativo (gerado via OpenRouter)
    ai_context: str | None = None

    # Avisos extras
    warnings: list[str] | None = None


def format_alert(data: AlertData) -> str:
    """
    Formata um alerta completo para envio no Telegram.

    Returns:
        String formatada com Markdown.
    """
    emoji = SPORT_EMOJI.get(data.sport, "🏅")
    sport_label = SPORT_LABEL.get(data.sport, data.sport)
    team = data.home_team if data.side == "home" else data.away_team

    lines = [
        "🔴 *ALERTA VALUE BET*\n",
        f"{emoji} {sport_label} — {data.tournament}",
        f"⚔️  {data.home_team} vs {data.away_team}",
    ]

    if data.surface:
        lines.append(f"🏠 Superfície: {data.surface.capitalize()}")

    lines.append("")
    lines.append("📊 *ANÁLISE*")
    lines.append(f"• Apostar em: *{team}*")
    lines.append(f"• Odd {data.bookmaker.capitalize()}: {data.odd:.2f}")
    lines.append(f"• Fair odd: {data.fair_odd:.2f}")
    lines.append(f"• EV: *+{data.ev:.1%}*")
    lines.append(f"• Prob. real: {data.prob_real:.0%}")
    lines.append(f"• Overround: {data.overround:.1%}")

    lines.append("")
    lines.append("💰 *SIZING*")
    lines.append(f"• Banca: R${data.bankroll:.2f}")
    lines.append(f"• Kelly ¼: {data.kelly_fraction:.1%}")
    lines.append(f"• Stake sugerido: *R${data.stake_amount:.2f}*")

    # Line movement
    if data.odd_opening is not None:
        lines.append("")
        lines.append("📈 *LINE MOVEMENT*")
        movement = data.odd - data.odd_opening
        direction = "caindo ✅" if movement < 0 else "subindo ⚠️"
        lines.append(f"• Abertura: {data.odd_opening:.2f}")
        lines.append(f"• Atual: {data.odd:.2f} ({direction})")

    # Contexto qualitativo via OpenRouter
    if data.ai_context:
        lines.append("")
        lines.append(f"🧠 _{data.ai_context}_")

    # Horário
    lines.append("")
    commence_brt = data.commence_time.astimezone(BRT)
    lines.append(f"🕐 Início: {commence_brt.strftime('%H:%M BRT')} ({commence_brt.strftime('%d/%m')})")
    lines.append(f"🏦 Casa: {data.bookmaker.capitalize()}")

    # Avisos
    if data.warnings:
        lines.append("")
        for warn in data.warnings:
            lines.append(f"⚠️  {warn}")

    return "\n".join(lines)
