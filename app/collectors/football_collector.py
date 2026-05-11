"""
Coleta de dados históricos via Football-Data.co.uk (gratuito, sem chave).

URL: https://www.football-data.co.uk/mmz4281/{season}/{code}.csv

Usada para ajustar o modelo Dixon-Coles. Baixa temporada atual + anterior
para ter volume suficiente de partidas (mínimo ~300 para convergência).

Normalização de nomes: Football-Data usa abreviações diferentes da Odds API.
O mapeamento está em TEAM_NAME_MAP.
"""

import csv
import logging
from datetime import date
from io import StringIO

import httpx

from app.engine.dixon_coles import MatchResult

logger = logging.getLogger(__name__)

BASE_URL = "https://www.football-data.co.uk/mmz4281"

# Odds API key → código Football-Data.co.uk
LEAGUE_CODES: dict[str, str] = {
    "soccer_epl": "E0",
    "soccer_england_championship": "E1",
    "soccer_spain_la_liga": "SP1",
    "soccer_germany_bundesliga": "D1",
    "soccer_italy_serie_a": "I1",
    "soccer_france_ligue_1": "F1",
    "soccer_brazil_campeonato": None,   # Football-Data não tem BR — tratado separadamente
}

# Normalização de nomes: Football-Data → The Odds API
# Expandir conforme novos times aparecerem nos logs ("time desconhecido")
TEAM_NAME_MAP: dict[str, str] = {
    # Premier League
    "Man City": "Manchester City",
    "Man United": "Manchester United",
    "Spurs": "Tottenham Hotspur",
    "Tottenham": "Tottenham Hotspur",
    "Newcastle": "Newcastle United",
    "West Ham": "West Ham United",
    "Wolves": "Wolverhampton Wanderers",
    "Brighton": "Brighton and Hove Albion",
    "Nott'm Forest": "Nottingham Forest",
    "Sheffield United": "Sheffield United",
    "Luton": "Luton Town",
    "Burnley": "Burnley",
    # La Liga
    "Ath Bilbao": "Athletic Club",
    "Ath Madrid": "Atletico Madrid",
    "Betis": "Real Betis",
    "Celta": "Celta Vigo",
    "Espanol": "Espanyol",
    "Sociedad": "Real Sociedad",
    "Vallecano": "Rayo Vallecano",
    # Bundesliga
    "Ein Frankfurt": "Eintracht Frankfurt",
    "Leverkusen": "Bayer Leverkusen",
    "M'gladbach": "Borussia Monchengladbach",
    "Dortmund": "Borussia Dortmund",
    "Schalke 04": "FC Schalke 04",
    # Serie A
    "Inter": "Inter Milan",
    "Spal": "SPAL",
    "Verona": "Hellas Verona",
    # Ligue 1
    "Paris SG": "Paris Saint-Germain",
    "St Etienne": "Saint-Etienne",
    "Nimes": "Nîmes Olympique",
}


def _normalize_team(name: str) -> str:
    """Normaliza nome do time do formato Football-Data para The Odds API."""
    return TEAM_NAME_MAP.get(name.strip(), name.strip())


def _seasons_to_fetch(n: int = 5) -> list[str]:
    """
    Retorna os códigos das últimas N temporadas europeias.
    Ex: n=5 em 2024/25 → ['2021', '2122', '2223', '2324', '2425']

    5 temporadas (~1.900 partidas EPL) é o range recomendado para
    Dixon-Coles convergir com time-decay: jogos de 5 anos atrás têm
    peso ~exp(-0.0018 × 1825) ≈ 4% — presentes mas não dominantes.
    """
    today = date.today()
    year = today.year
    cur_start = year if today.month >= 8 else year - 1

    def code(start: int) -> str:
        return f"{str(start)[2:]}{str(start + 1)[2:]}"

    return [code(cur_start - n + 1 + i) for i in range(n)]


async def fetch_historical_results(league_key: str) -> list[MatchResult]:
    """
    Baixa resultados históricos para uma liga da temporada atual + anterior.

    Returns:
        Lista de MatchResult para fitting do Dixon-Coles.
        Retorna [] em caso de erro — pipeline não quebra.
    """
    code = LEAGUE_CODES.get(league_key)
    if not code:
        logger.debug("Liga sem fonte Football-Data: %s", league_key)
        return []

    results: list[MatchResult] = []

    for season in _seasons_to_fetch(n=5):
        url = f"{BASE_URL}/{season}/{code}.csv"
        try:
            async with httpx.AsyncClient(timeout=20.0) as client:
                resp = await client.get(url)

            if resp.status_code == 404:
                logger.debug("Football-Data: %s temporada %s não encontrada", code, season)
                continue

            resp.raise_for_status()
            parsed = _parse_csv(resp.text)
            results.extend(parsed)
            logger.info(
                "Football-Data [%s %s]: %d partidas carregadas",
                code, season, len(parsed),
            )

        except httpx.TimeoutException:
            logger.error("Football-Data timeout: %s %s", code, season)
        except Exception as exc:
            logger.error("Football-Data erro [%s %s]: %s", code, season, exc)

    return results


def _parse_csv(text: str) -> list[MatchResult]:
    """Parseia CSV do Football-Data.co.uk em MatchResult."""
    results: list[MatchResult] = []
    reader = csv.DictReader(StringIO(text))

    for row in reader:
        try:
            date_str = row.get("Date", "").strip()
            if not date_str:
                continue

            parts = date_str.split("/")
            if len(parts) != 3:
                continue

            day, month, year_str = parts
            year = int(year_str)
            if year < 100:
                year += 2000

            home_goals = int(row.get("FTHG") or row.get("HG") or 0)
            away_goals = int(row.get("FTAG") or row.get("AG") or 0)

            home = _normalize_team(row.get("HomeTeam", ""))
            away = _normalize_team(row.get("AwayTeam", ""))

            if not home or not away:
                continue

            results.append(MatchResult(
                home_team=home,
                away_team=away,
                home_goals=home_goals,
                away_goals=away_goals,
                match_date=date(year, int(month), int(day)),
            ))

        except (ValueError, KeyError) as exc:
            logger.debug("Row ignorada: %s", exc)

    return results
