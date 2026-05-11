"""
Envio de alertas via Telegram Bot API.

Usa httpx async para não bloquear o event loop do FastAPI.
Em caso de erro, loga e retorna None (não quebra o pipeline).
"""

import logging

import httpx

from app.config import get_settings

logger = logging.getLogger(__name__)

BASE_URL = "https://api.telegram.org"


async def send_message(text: str, parse_mode: str = "Markdown") -> int | None:
    """
    Envia uma mensagem para o chat configurado.

    Args:
        text: Texto da mensagem (suporta Markdown)
        parse_mode: "Markdown" ou "HTML"

    Returns:
        message_id do Telegram, ou None em caso de falha.
    """
    settings = get_settings()
    url = f"{BASE_URL}/bot{settings.telegram_token}/sendMessage"
    payload = {
        "chat_id": settings.telegram_chat_id,
        "text": text,
        "parse_mode": parse_mode,
        "disable_web_page_preview": True,
    }

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(url, json=payload)

        data = resp.json()

        if not data.get("ok"):
            logger.error("Telegram erro: %s", data.get("description"))
            return None

        msg_id = data["result"]["message_id"]
        logger.info("Telegram: mensagem enviada (msg_id=%d)", msg_id)
        return msg_id

    except httpx.TimeoutException:
        logger.error("Telegram timeout ao enviar mensagem")
        return None
    except Exception as exc:
        logger.error("Telegram erro inesperado: %s", exc)
        return None


async def send_alert(text: str) -> int | None:
    """Atalho para enviar alerta de value bet."""
    return await send_message(text)


async def send_startup_message() -> None:
    """Notifica no Telegram que o bot foi iniciado."""
    await send_message("🟢 *ValueBot iniciado* — monitorando mercados...", parse_mode="Markdown")


async def send_shutdown_message() -> None:
    """Notifica no Telegram que o bot foi encerrado."""
    await send_message("🔴 *ValueBot encerrado*", parse_mode="Markdown")


async def send_clv_report(
    total: int,
    clv_medio: float,
    positivos: int,
    taxa_positiva: float,
) -> None:
    """Envia relatório semanal de CLV via Telegram."""
    sinal = "+" if clv_medio >= 0 else ""
    avaliacao = (
        "✅ Edge confirmado" if clv_medio >= 2
        else "⚠️  Edge marginal" if clv_medio >= 0
        else "❌ Sem edge detectado"
    )

    text = (
        "📊 *RELATÓRIO CLV SEMANAL*\n\n"
        f"Alertas com CLV registrado: *{total}*\n"
        f"CLV médio: *{sinal}{clv_medio:.1f}%*\n"
        f"CLV positivo: *{positivos}/{total}* ({taxa_positiva:.0f}%)\n\n"
        f"{avaliacao}\n\n"
        "_CLV mede se você apostou acima do preço justo da Pinnacle. "
        "CLV médio \\>+2% em 50\\+ apostas = edge real confirmado._"
    )
    await send_message(text)
