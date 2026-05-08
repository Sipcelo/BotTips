"""
Coleta de dados de tênis via API-Tennis.

Busca rankings ATP/WTA e H2H entre dois jogadores para enriquecer
a análise do modelo Elo com dados externos.
"""

import logging
from dataclasses import dataclass

import httpx

from app.config import get_settings

logger = logging.getLogger(__name__)

BASE_URL = "https://v1.tennis.api-sports.io"


@dataclass
class PlayerInfo:
    name: str
    ranking: int | None
    points: int | None
    country: str | None


@dataclass
class H2HRecord:
    player_a: str
    player_b: str
    wins_a: int
    wins_b: int
    total: int


def _headers() -> dict[str, str]:
    return {"x-apisports-key": get_settings().tennis_api_key}


async def fetch_ranking(tour: str = "ATP", limit: int = 100) -> list[PlayerInfo]:
    """
    Busca top N do ranking ATP ou WTA.

    Args:
        tour: "ATP" ou "WTA"
        limit: número de jogadores a retornar

    Returns:
        Lista de PlayerInfo ordenada por ranking.
    """
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(
                f"{BASE_URL}/rankings",
                headers=_headers(),
                params={"type": tour},
            )
        resp.raise_for_status()
        data = resp.json()

        players = []
        for item in data.get("response", [])[:limit]:
            players.append(PlayerInfo(
                name=item.get("player", {}).get("name", ""),
                ranking=item.get("position"),
                points=item.get("points"),
                country=item.get("player", {}).get("country", {}).get("name"),
            ))

        logger.info("Ranking %s: %d jogadores carregados", tour, len(players))
        return players

    except Exception as exc:
        logger.error("API-Tennis ranking erro: %s", exc)
        return []


async def fetch_h2h(player_a: str, player_b: str) -> H2HRecord | None:
    """
    Busca histórico de confrontos diretos entre dois jogadores.

    Returns:
        H2HRecord ou None em caso de erro.
    """
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(
                f"{BASE_URL}/players/h2h",
                headers=_headers(),
                params={"player1": player_a, "player2": player_b},
            )
        resp.raise_for_status()
        data = resp.json()

        results = data.get("response", [])
        wins_a = sum(1 for r in results if r.get("winner", {}).get("name") == player_a)
        wins_b = sum(1 for r in results if r.get("winner", {}).get("name") == player_b)

        record = H2HRecord(
            player_a=player_a,
            player_b=player_b,
            wins_a=wins_a,
            wins_b=wins_b,
            total=len(results),
        )
        logger.debug("H2H %s vs %s: %d-%d", player_a, player_b, wins_a, wins_b)
        return record

    except Exception as exc:
        logger.error("API-Tennis H2H erro: %s", exc)
        return None
