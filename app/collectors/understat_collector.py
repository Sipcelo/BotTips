"""
Coleta dados de xG (Expected Goals) via Understat.com (gratuito).

Usa understatapi — wrapper assíncrono oficial para understat.com.
Roda a cada 3 dias (cadência de rodada europeia) para manter os
dados frescos antes de cada rodada.

Ligas suportadas pelo Understat:
    EPL, La liga, Bundesliga, Serie A, Ligue 1, RFPL (Rússia)

Brasileirão NÃO está disponível no Understat — sem xG para essa liga.

Normalização de nomes: Understat usa nomes próprios que podem diferir
da Odds API. TEAM_NAME_MAP resolve as diferenças conhecidas.
"""

import logging
from dataclasses import dataclass
from datetime import date, datetime, timezone

from understatapi import UnderstatClient

logger = logging.getLogger(__name__)

# Odds API key → nome da liga no Understat
UNDERSTAT_LEAGUE_MAP: dict[str, str] = {
    "soccer_epl": "EPL",
    "soccer_spain_la_liga": "La liga",
    "soccer_germany_bundesliga": "Bundesliga",
    "soccer_italy_serie_a": "Serie A",
    "soccer_france_ligue_1": "Ligue 1",
}

# Understat → The Odds API (nome canônico)
TEAM_NAME_MAP: dict[str, str] = {
    # Premier League
    "Manchester United": "Manchester United",
    "Manchester City": "Manchester City",
    "Tottenham": "Tottenham Hotspur",
    "Newcastle United": "Newcastle United",
    "West Ham": "West Ham United",
    "Wolverhampton Wanderers": "Wolverhampton Wanderers",
    "Brighton": "Brighton and Hove Albion",
    "Nottingham Forest": "Nottingham Forest",
    # La Liga
    "Athletic Club": "Athletic Club",
    "Atletico Madrid": "Atletico Madrid",
    "Real Betis": "Real Betis",
    "Celta Vigo": "Celta Vigo",
    "Rayo Vallecano": "Rayo Vallecano",
    # Bundesliga
    "Eintracht Frankfurt": "Eintracht Frankfurt",
    "Bayer Leverkusen": "Bayer Leverkusen",
    "Borussia Mönchengladbach": "Borussia Monchengladbach",
    # Serie A
    "Internazionale": "Inter Milan",
    "Hellas Verona": "Hellas Verona",
    # Ligue 1
    "Paris Saint-Germain": "Paris Saint-Germain",
}


def _normalize(name: str) -> str:
    return TEAM_NAME_MAP.get(name.strip(), name.strip())


def _current_season() -> str:
    """Retorna o ano de início da temporada atual. Ex: '2024' para 2024/25."""
    today = date.today()
    return str(today.year if today.month >= 8 else today.year - 1)


@dataclass
class TeamXGData:
    """Dados xG de um time para a temporada."""
    team_name: str          # nome normalizado para The Odds API
    league_key: str
    season: str
    matches_played: int
    goals_for: int
    goals_against: int
    xg_for: float
    xg_against: float
    xg_divergence: float    # (goals_for - xg_for) / xg_for
    fetched_at: datetime


async def fetch_xg_for_league(league_key: str) -> list[TeamXGData]:
    """
    Coleta xG acumulado de todos os times de uma liga na temporada atual.

    Returns:
        Lista de TeamXGData. Retorna [] em caso de erro — não quebra pipeline.
    """
    understat_league = UNDERSTAT_LEAGUE_MAP.get(league_key)
    if not understat_league:
        logger.debug("Understat: liga sem mapeamento: %s", league_key)
        return []

    season = _current_season()
    fetched_at = datetime.now(tz=timezone.utc)
    results: list[TeamXGData] = []

    try:
        async with UnderstatClient() as client:
            teams_raw = await client.league.get_team_data(
                understat_league, season=season
            )

        # A API retorna dict {team_id: {title, history: [match_stats...]}}
        for team_id, team_data in teams_raw.items():
            try:
                title = _normalize(team_data.get("title", ""))
                history = team_data.get("history", [])

                if not history:
                    continue

                goals_for = sum(int(m.get("scored", 0)) for m in history)
                goals_against = sum(int(m.get("missed", 0)) for m in history)
                xg_for = sum(float(m.get("xG", 0)) for m in history)
                xg_against = sum(float(m.get("xGA", 0)) for m in history)
                matches = len(history)

                # Divergência: quão longe os gols reais estão do xG
                # Positivo = overperforming, Negativo = underperforming
                divergence = (
                    (goals_for - xg_for) / xg_for
                    if xg_for > 0
                    else 0.0
                )

                results.append(TeamXGData(
                    team_name=title,
                    league_key=league_key,
                    season=season,
                    matches_played=matches,
                    goals_for=goals_for,
                    goals_against=goals_against,
                    xg_for=round(xg_for, 2),
                    xg_against=round(xg_against, 2),
                    xg_divergence=round(divergence, 4),
                    fetched_at=fetched_at,
                ))

            except (KeyError, ValueError, ZeroDivisionError) as exc:
                logger.debug("Understat: erro ao parsear time %s: %s", team_id, exc)

        logger.info(
            "Understat [%s %s]: %d times com xG coletados",
            understat_league, season, len(results),
        )

    except Exception as exc:
        logger.error(
            "Understat erro [%s %s]: %s — xG indisponível para esta rodada",
            understat_league, season, exc,
        )

    return results


async def fetch_all_leagues_xg(league_keys: list[str]) -> list[TeamXGData]:
    """Coleta xG para todas as ligas monitoradas de uma vez."""
    all_data: list[TeamXGData] = []
    for league_key in league_keys:
        data = await fetch_xg_for_league(league_key)
        all_data.extend(data)
    return all_data
