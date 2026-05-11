"""
Coleta de arremessadores prováveis via MLB Stats API (oficial, gratuita).

Endpoint: statsapi.mlb.com/api/v1/schedule
Sem chave de API necessária.

Usado pelo pipeline para enriquecer alertas MLB com informação do pitcher
e sinalizar baixa confiança quando os arremessadores não estão confirmados.
"""

import logging
from dataclasses import dataclass
from datetime import date, datetime, timezone

import httpx

logger = logging.getLogger(__name__)

MLB_STATS_BASE = "https://statsapi.mlb.com/api/v1"


@dataclass
class PitcherMatchup:
    home_team: str
    away_team: str
    home_pitcher: str | None   # None = não confirmado
    away_pitcher: str | None
    game_time: datetime | None


async def fetch_probable_pitchers(game_date: date | None = None) -> list[PitcherMatchup]:
    """
    Busca arremessadores prováveis para todos os jogos MLB do dia.

    Returns:
        Lista de PitcherMatchup. Em caso de erro retorna lista vazia
        sem quebrar o pipeline.
    """
    if game_date is None:
        game_date = datetime.now(tz=timezone.utc).date()

    params = {
        "sportId": 1,
        "date": game_date.strftime("%Y-%m-%d"),
        "hydrate": "probablePitcher",
    }

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(f"{MLB_STATS_BASE}/schedule", params=params)

        resp.raise_for_status()
        data = resp.json()

        matchups: list[PitcherMatchup] = []
        for date_entry in data.get("dates", []):
            for game in date_entry.get("games", []):
                home = game["teams"]["home"]
                away = game["teams"]["away"]

                game_time: datetime | None = None
                raw_time = game.get("gameDate", "")
                if raw_time:
                    game_time = datetime.fromisoformat(raw_time.replace("Z", "+00:00"))

                home_pitcher = (
                    home["probablePitcher"].get("fullName")
                    if "probablePitcher" in home
                    else None
                )
                away_pitcher = (
                    away["probablePitcher"].get("fullName")
                    if "probablePitcher" in away
                    else None
                )

                matchups.append(PitcherMatchup(
                    home_team=home["team"]["name"],
                    away_team=away["team"]["name"],
                    home_pitcher=home_pitcher,
                    away_pitcher=away_pitcher,
                    game_time=game_time,
                ))

        confirmed = sum(1 for m in matchups if m.home_pitcher and m.away_pitcher)
        logger.info(
            "MLB Stats API: %d jogos hoje, %d com ambos os pitchers confirmados",
            len(matchups), confirmed,
        )
        return matchups

    except httpx.TimeoutException:
        logger.error("MLB Stats API timeout — pitcher info indisponível")
        return []
    except Exception as exc:
        logger.error("MLB Stats API erro: %s", exc)
        return []


def find_pitcher_matchup(
    home_team: str,
    away_team: str,
    matchups: list[PitcherMatchup],
) -> PitcherMatchup | None:
    """
    Encontra o PitcherMatchup correspondente a um evento da Odds API.

    Tenta match exato primeiro, depois match parcial por palavras
    (ex: "Yankees" casa com "New York Yankees").
    """
    home_lower = home_team.lower()
    away_lower = away_team.lower()

    # 1. Match exato (case-insensitive)
    for m in matchups:
        if m.home_team.lower() == home_lower and m.away_team.lower() == away_lower:
            return m

    # 2. Match parcial — palavras em comum
    home_words = set(home_lower.split())
    away_words = set(away_lower.split())
    for m in matchups:
        m_home_words = set(m.home_team.lower().split())
        m_away_words = set(m.away_team.lower().split())
        if home_words & m_home_words and away_words & m_away_words:
            return m

    return None
