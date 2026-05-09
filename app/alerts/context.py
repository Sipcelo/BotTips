"""
Geração de contexto qualitativo via OpenRouter.

Adiciona 2-3 frases de análise ao alerta (forma recente, superfície,
H2H) usando um modelo de linguagem barato/gratuito via OpenRouter.

Se a chave não estiver configurada ou a API falhar, retorna None
sem quebrar o pipeline.
"""

import logging

import httpx

from app.config import get_settings

logger = logging.getLogger(__name__)

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"


def _build_prompt(
    sport: str,
    home_team: str,
    away_team: str,
    surface: str | None,
    side: str,
    ev: float,
    odd: float,
) -> str:
    team = home_team if side == "home" else away_team
    surface_info = f" na superfície {surface}" if surface else ""

    return (
        f"Você é um analista de value betting. Em 2 frases curtas e diretas, "
        f"explique por que '{team}' pode ser uma value bet com odd {odd:.2f} "
        f"e EV de +{ev:.1%} no jogo {home_team} vs {away_team} ({sport}{surface_info}). "
        f"Mencione apenas fatores objetivos como forma recente ou vantagem de superfície. "
        f"Não use markdown, não repita os números já informados."
    )


async def generate_context(
    sport: str,
    home_team: str,
    away_team: str,
    surface: str | None,
    side: str,
    ev: float,
    odd: float,
) -> str | None:
    """
    Gera contexto qualitativo via OpenRouter.

    Returns:
        String com 2-3 frases de contexto, ou None se indisponível.
    """
    settings = get_settings()

    if not settings.openrouter_api_key:
        logger.debug("OpenRouter não configurado — contexto omitido")
        return None

    prompt = _build_prompt(sport, home_team, away_team, surface, side, ev, odd)

    headers = {
        "Authorization": f"Bearer {settings.openrouter_api_key}",
        "HTTP-Referer": "https://github.com/Sipcelo/BotTips",
        "X-Title": "ValueBot",
        "Content-Type": "application/json",
    }
    payload = {
        "model": settings.openrouter_model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 120,
        "temperature": 0.4,
    }

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(OPENROUTER_URL, headers=headers, json=payload)

        if resp.status_code != 200:
            logger.warning("OpenRouter status %d: %s", resp.status_code, resp.text[:200])
            return None

        data = resp.json()
        text = data["choices"][0]["message"]["content"].strip()
        logger.debug("OpenRouter contexto gerado: %s", text[:80])
        return text

    except httpx.TimeoutException:
        logger.warning("OpenRouter timeout — contexto omitido")
        return None
    except Exception as exc:
        logger.warning("OpenRouter erro: %s", exc)
        return None
