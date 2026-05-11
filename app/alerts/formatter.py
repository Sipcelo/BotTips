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
    "mma_mixed_martial_arts": "🥊",
    "soccer_epl": "⚽",
    "soccer_spain_la_liga": "⚽",
    "soccer_germany_bundesliga": "⚽",
    "soccer_italy_serie_a": "⚽",
    "soccer_france_ligue_1": "⚽",
    "soccer_brazil_campeonato": "⚽",
}

SPORT_LABEL = {
    "tennis_atp": "Tênis ATP",
    "tennis_wta": "Tênis WTA",
    "table_tennis": "Tênis de Mesa",
    "handball": "Handebol EHF",
    "baseball_mlb": "MLB",
    "mma_mixed_martial_arts": "MMA",
    "soccer_epl": "Premier League",
    "soccer_spain_la_liga": "La Liga",
    "soccer_germany_bundesliga": "Bundesliga",
    "soccer_italy_serie_a": "Serie A",
    "soccer_france_ligue_1": "Ligue 1",
    "soccer_brazil_campeonato": "Brasileirão",
}

# Label amigável para o lado da aposta
SIDE_LABEL = {
    "home": lambda home, away: home,
    "away": lambda home, away: away,
    "draw": lambda home, away: "Empate",
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

    # MLB — arremessadores prováveis
    home_pitcher: str | None = None
    away_pitcher: str | None = None
    pitcher_confirmed: bool = True   # False = nenhum dos dois confirmado

    # Nível de confiança calculado pelo filter
    confidence: str = "ALTA"   # "ALTA" | "MÉDIA" | "BAIXA"

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
    side_fn = SIDE_LABEL.get(data.side, lambda h, a: h)
    team = side_fn(data.home_team, data.away_team)

    confidence_emoji = {"ALTA": "🟢", "MÉDIA": "🟡", "BAIXA": "🔴"}.get(data.confidence, "⚪")
    flag_count = len(data.warnings) if data.warnings else 0
    flag_label = f"{flag_count} flag" + ("s" if flag_count != 1 else "")

    lines = [
        f"{confidence_emoji} *[{data.confidence}] ALERTA VALUE BET*",
        f"_{emoji} {sport_label} — {team} ML @ {data.odd:.2f} | EV +{data.ev:.1%} | {flag_label}_\n",
        f"⚔️  {data.home_team} vs {data.away_team}",
    ]

    if data.surface:
        lines.append(f"🏠 Superfície: {data.surface.capitalize()}")

    # MLB: arremessadores
    if data.sport == "baseball_mlb":
        if data.home_pitcher and data.away_pitcher:
            lines.append(f"⚾ Pitchers: {data.away_pitcher} vs {data.home_pitcher}")
        else:
            lines.append("⚾ Pitchers: ⚠️ não confirmados")

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
