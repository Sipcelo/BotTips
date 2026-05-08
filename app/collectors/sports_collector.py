"""
Coleta de stats para tênis de mesa e handebol via API-Sports.

Usado para enriquecer o contexto dos alertas com forma recente
e histórico de confrontos.
"""

import logging
from dataclasses import dataclass

import httpx

from app.config import get_settings

logger = logging.getLogger(__name__)

BASE_URL = "https://v1.handball.api-sports.io"
TABLE_TENNIS_URL = "https://v1.table-tennis.api-sports.io"


def _headers() -> dict[str, str]:
    return {"x-apisports-key": get_settings().sports_api_key}


@dataclass
class RecentForm:
    team: str
    wins: int
    losses: int
    last_5: list[str]  # ex: ["W", "W", "L", "W", "L"]


async def fetch_handball_form(team_id: int, season: int = 2025) -> RecentForm | None:
    """
    Busca forma recente de um time de handebol.

    Returns:
        RecentForm ou None em caso de erro.
    """
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(
                f"{BASE_URL}/teams/statistics",
                headers=_headers(),
                params={"team": team_id, "season": season},
            )
        resp.raise_for_status()
        data = resp.json().get("response", {})

        fixtures = data.get("fixtures", {})
        wins = fixtures.get("wins", {}).get("total", 0)
        losses = fixtures.get("loses", {}).get("total", 0)

        logger.debug("Handebol form team_id=%d: %dW/%dL", team_id, wins, losses)
        return RecentForm(
            team=str(team_id),
            wins=wins,
            losses=losses,
            last_5=[],
        )

    except Exception as exc:
        logger.error("API-Sports handebol erro: %s", exc)
        return None


async def fetch_table_tennis_h2h(player_a_id: int, player_b_id: int) -> dict | None:
    """
    Busca H2H entre dois jogadores de tênis de mesa.

    Returns:
        Dict com resultados ou None em caso de erro.
    """
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(
                f"{TABLE_TENNIS_URL}/players/h2h",
                headers=_headers(),
                params={"h2h": f"{player_a_id}-{player_b_id}"},
            )
        resp.raise_for_status()
        data = resp.json()

        results = data.get("response", [])
        logger.debug("Table tennis H2H: %d partidas encontradas", len(results))
        return {"matches": results, "total": len(results)}

    except Exception as exc:
        logger.error("API-Sports tênis de mesa erro: %s", exc)
        return None
