"""
Coleta de odds via The Odds API v4.

Busca eventos e odds de múltiplas casas para os esportes alvo.
Usa /v4/sports (1 request) para descobrir quais esportes têm mercados
ativos antes de buscar odds — economiza requests na cota mensal.
Em caso de erro na API, loga e retorna lista vazia (não quebra o scheduler).

Quota guard:
    Rastreia x-requests-remaining do header após cada chamada.
    Se restar menos de QUOTA_PAUSE_THRESHOLD requests, o pipeline é pausado
    e uma mensagem de aviso é enviada via Telegram.
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime

import httpx

from app.config import get_settings

logger = logging.getLogger(__name__)

BASE_URL = "https://api.the-odds-api.com/v4"

# Quando requests_remaining cair abaixo deste valor, o pipeline para
# e envia alerta Telegram para não esgotarmos a cota antes dos jogos do fim de semana.
QUOTA_PAUSE_THRESHOLD = 30

# Último valor conhecido de requests_remaining (atualizado a cada resposta da API)
_quota_remaining: int | None = None


def get_quota_remaining() -> int | None:
    """Retorna o último valor conhecido de requests_remaining, ou None se desconhecido."""
    return _quota_remaining


def _update_quota(headers: dict) -> int | None:
    """Extrai e persiste x-requests-remaining do header da resposta."""
    global _quota_remaining
    raw = headers.get("x-requests-remaining")
    if raw and raw != "?":
        try:
            _quota_remaining = int(raw)
        except ValueError:
            pass
    return _quota_remaining

# Esportes monitorados conforme CLAUDE.md
TARGET_SPORTS = [
    "tennis_atp",
    "tennis_wta",
    "table_tennis",
    "handball",
    "baseball_mlb",
    "mma_mixed_martial_arts",
    # Futebol Europeu — modelo Dixon-Coles (ver docs/sport_football_european.md)
    "soccer_epl",
    "soccer_spain_la_liga",
    "soccer_germany_bundesliga",
    "soccer_italy_serie_a",
    "soccer_france_ligue_1",
    # Futebol Brasileiro — Dixon-Coles calibrado BR (ver docs/sport_football_brazilian.md)
    "soccer_brazil_campeonato",
]

# Casas prioritárias (sharp + soft)
BOOKMAKERS = "pinnacle,betano,betsson,bet365"


@dataclass
class BookmakerOdds:
    bookmaker: str
    home_odd: float
    away_odd: float
    draw_odd: float | None = None
    last_update: datetime | None = None


@dataclass
class EventOdds:
    external_id: str
    sport: str
    home_team: str
    away_team: str
    commence_time: datetime
    bookmakers: list[BookmakerOdds] = field(default_factory=list)


async def fetch_active_target_sports() -> list[str]:
    """
    Consulta /v4/sports (1 request único) e retorna apenas os esportes
    de TARGET_SPORTS que têm eventos ativos no momento.

    Isso evita chamar /odds para esportes sem mercado aberto,
    economizando requests da cota mensal.
    """
    settings = get_settings()
    params = {"apiKey": settings.odds_api_key}

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(f"{BASE_URL}/sports", params=params)

        remaining = resp.headers.get("x-requests-remaining", "?")
        _update_quota(resp.headers)
        logger.info("Odds API [sports list] status=%s requests_left=%s", resp.status_code, remaining)

        if resp.status_code == 401:
            logger.error("Odds API: chave inválida (401) — abortando pipeline")
            return []

        resp.raise_for_status()

        active_keys: set[str] = {
            s["key"]
            for s in resp.json()
            if s.get("active", False)
        }

        available = [s for s in TARGET_SPORTS if s in active_keys]
        skipped = [s for s in TARGET_SPORTS if s not in active_keys]

        if skipped:
            logger.info("Esportes sem mercado ativo (pulados): %s", skipped)
        logger.info("Esportes com mercado ativo: %s", available)

        return available

    except httpx.TimeoutException:
        logger.error("Odds API timeout ao buscar lista de esportes — usando todos os targets")
        return TARGET_SPORTS
    except Exception as exc:
        logger.error("Odds API erro ao buscar esportes ativos: %s — usando todos os targets", exc)
        return TARGET_SPORTS


async def fetch_events_with_odds(sport: str) -> list[EventOdds]:
    """
    Busca eventos com odds para um esporte específico.

    Returns:
        Lista de EventOdds. Vazia em caso de erro ou sem eventos.
    """
    settings = get_settings()
    params = {
        "apiKey": settings.odds_api_key,
        "regions": "eu,us",
        "markets": "h2h",
        "oddsFormat": "decimal",
        "bookmakers": BOOKMAKERS,
    }

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(f"{BASE_URL}/sports/{sport}/odds", params=params)

        remaining = resp.headers.get("x-requests-remaining", "?")
        _update_quota(resp.headers)
        logger.info("Odds API [%s] status=%s requests_left=%s", sport, resp.status_code, remaining)

        if resp.status_code == 401:
            logger.error("Odds API: chave inválida (401)")
            return []
        if resp.status_code in (404, 422):
            logger.info("Odds API: sem mercados ativos para [%s]", sport)
            return []
        resp.raise_for_status()

        return _parse_response(sport, resp.json())

    except httpx.TimeoutException:
        logger.error("Odds API timeout para esporte %s", sport)
        return []
    except Exception as exc:
        logger.error("Odds API erro inesperado [%s]: %s", sport, exc)
        return []


def _parse_response(sport: str, data: list[dict]) -> list[EventOdds]:
    events: list[EventOdds] = []

    for item in data:
        try:
            commence_time = datetime.fromisoformat(
                item["commence_time"].replace("Z", "+00:00")
            )
            event = EventOdds(
                external_id=item["id"],
                sport=sport,
                home_team=item["home_team"],
                away_team=item["away_team"],
                commence_time=commence_time,
            )

            for bm in item.get("bookmakers", []):
                for market in bm.get("markets", []):
                    if market["key"] != "h2h":
                        continue
                    outcomes = {o["name"]: o["price"] for o in market["outcomes"]}
                    home_odd = outcomes.get(item["home_team"])
                    away_odd = outcomes.get(item["away_team"])
                    draw_odd = outcomes.get("Draw")

                    if home_odd and away_odd:
                        last_update = None
                        if bm.get("last_update"):
                            last_update = datetime.fromisoformat(
                                bm["last_update"].replace("Z", "+00:00")
                            )
                        event.bookmakers.append(BookmakerOdds(
                            bookmaker=bm["key"],
                            home_odd=home_odd,
                            away_odd=away_odd,
                            draw_odd=draw_odd,
                            last_update=last_update,
                        ))

            if event.bookmakers:
                events.append(event)

        except (KeyError, ValueError) as exc:
            logger.warning("Erro ao parsear evento: %s — %s", exc, item.get("id"))

    logger.info("Odds API [%s]: %d eventos com odds", sport, len(events))
    return events


async def fetch_all_sports() -> list[EventOdds]:
    """
    Coleta odds apenas dos esportes alvo com mercados ativos.

    Fluxo:
        1. 1 request para /v4/sports → descobre quais têm eventos
        2. N requests para /v4/sports/{sport}/odds — apenas ativos
    Economiza até 5 requests por run quando os mercados estão fechados.
    """
    active_sports = await fetch_active_target_sports()

    if not active_sports:
        logger.info("Nenhum esporte alvo com mercado ativo no momento")
        return []

    all_events: list[EventOdds] = []
    for sport in active_sports:
        events = await fetch_events_with_odds(sport)
        all_events.extend(events)
    return all_events
