"""
Ponto de entrada do ValueBot — FastAPI + APScheduler.

Lifespan: inicia scheduler e banco ao subir, encerra ao parar.
"""

import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import FastAPI

from app.alerts.telegram import send_shutdown_message, send_startup_message
from app.config import get_settings
from app.database.connection import create_tables
from app.models import alert, event, odds, team_xg  # noqa: F401 — registra modelos no Base
from app.scheduler import build_scheduler

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
)
logger = logging.getLogger(__name__)

scheduler = build_scheduler()


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    logger.info("ValueBot iniciando [env=%s]", settings.environment)

    await create_tables()
    scheduler.start()
    logger.info("Scheduler iniciado — intervalo: %ds", settings.poll_interval_seconds)

    if settings.environment == "production":
        await send_startup_message()

    yield

    scheduler.shutdown(wait=False)
    logger.info("Scheduler encerrado")

    if settings.environment == "production":
        await send_shutdown_message()


app = FastAPI(
    title="ValueBot",
    description="Bot de value betting — alertas via Telegram",
    version="1.0.0",
    lifespan=lifespan,
)


@app.get("/health")
async def health():
    """Endpoint de health check para Railway e monitoramento."""
    next_run = None
    job = scheduler.get_job("value_bet_pipeline")
    if job and job.next_run_time:
        next_run = job.next_run_time.isoformat()

    return {
        "status": "ok",
        "timestamp": datetime.now(tz=timezone.utc).isoformat(),
        "scheduler_running": scheduler.running,
        "next_pipeline_run": next_run,
    }


@app.get("/pipeline/run")
async def trigger_pipeline():
    """Dispara o pipeline manualmente (útil para debug)."""
    from app.scheduler import run_pipeline
    import asyncio
    logger.info("Pipeline disparado manualmente via API")
    asyncio.create_task(run_pipeline())
    return {"status": "pipeline triggered"}


@app.get("/admin/dc-status")
async def dc_status():
    """
    Estado atual dos modelos Dixon-Coles em memória.

    Mostra: ligas monitoradas, se modelo está fitted, quantas partidas,
    parâmetros gamma/rho, e quando foi ajustado.
    """
    from app.engine.model_store import FOOTBALL_LEAGUES, get_model, needs_refit

    result = {}
    for league in FOOTBALL_LEAGUES:
        model = get_model(league)
        if model:
            result[league] = {
                "fitted": True,
                "n_teams": len(model.teams),
                "n_matches": model.n_matches,
                "gamma": round(model.gamma, 3),
                "rho": round(model.rho, 3),
                "fitted_on": model.fitted_on.isoformat() if model.fitted_on else None,
                "needs_refit": needs_refit(league),
            }
        else:
            result[league] = {
                "fitted": False,
                "needs_refit": True,
            }

    return {"dc_models": result}


@app.get("/admin/dc-fit")
async def trigger_dc_fit():
    """
    Força o fitting do Dixon-Coles para todas as ligas europeias agora.
    Útil para verificar se o backfill está funcionando.
    Pode demorar 30-60s (download + otimização MLE).
    """
    from app.scheduler import _fit_dc_model
    from app.engine.model_store import FOOTBALL_LEAGUES
    import asyncio

    async def _fit_all():
        for league in FOOTBALL_LEAGUES:
            if "brazil" not in league:
                await _fit_dc_model(league)

    asyncio.create_task(_fit_all())
    return {
        "status": "fitting started",
        "leagues": [l for l in FOOTBALL_LEAGUES if "brazil" not in l],
        "note": "verifique /admin/dc-status em ~60s para ver resultado",
    }


@app.get("/admin/clv-status")
async def clv_status():
    """
    Resumo do CLV registrado até agora.

    Mostra alertas com CLV calculado e os que ainda estão pendentes.
    """
    from app.database.connection import AsyncSessionLocal
    from app.models.alert import Alert
    from app.models.event import Event
    from sqlalchemy import select, func

    async with AsyncSessionLocal() as session:
        # Alertas com CLV calculado
        res_calculated = await session.execute(
            select(
                func.count(Alert.id).label("total"),
                func.avg(Alert.clv_percent).label("clv_medio"),
                func.min(Alert.clv_percent).label("clv_min"),
                func.max(Alert.clv_percent).label("clv_max"),
            ).where(Alert.clv_percent.is_not(None))
        )
        calc = res_calculated.one()

        # Alertas aguardando CLV (jogo ainda não aconteceu)
        res_pending = await session.execute(
            select(func.count(Alert.id)).where(
                Alert.clv_percent.is_(None),
                Alert.pinnacle_closing_odd.is_not(None),
            )
        )
        pending_with_closing = res_pending.scalar_one()

        res_no_closing = await session.execute(
            select(func.count(Alert.id)).where(
                Alert.clv_percent.is_(None),
                Alert.pinnacle_closing_odd.is_(None),
            )
        )
        pending_no_closing = res_no_closing.scalar_one()

        # Últimos 5 alertas com CLV
        res_recent = await session.execute(
            select(Alert, Event)
            .join(Event)
            .where(Alert.clv_percent.is_not(None))
            .order_by(Alert.clv_recorded_at.desc())
            .limit(5)
        )
        recent = [
            {
                "event": f"{row.Event.home_team} vs {row.Event.away_team}",
                "sport": row.Event.sport,
                "odd": row.Alert.odd,
                "closing_odd": row.Alert.pinnacle_closing_odd,
                "clv": f"{row.Alert.clv_percent * 100:+.1f}%",
                "recorded_at": row.Alert.clv_recorded_at.isoformat() if row.Alert.clv_recorded_at else None,
            }
            for row in res_recent
        ]

    total = calc.total or 0
    return {
        "clv_calculado": {
            "total_alertas": total,
            "clv_medio": f"{(calc.clv_medio or 0) * 100:+.1f}%" if total else "sem dados",
            "clv_min": f"{(calc.clv_min or 0) * 100:+.1f}%" if total else None,
            "clv_max": f"{(calc.clv_max or 0) * 100:+.1f}%" if total else None,
        },
        "pendente": {
            "aguardando_jogo_comecar": pending_with_closing,
            "sem_closing_odd": pending_no_closing,
        },
        "ultimos_clv": recent,
        "nota": "CLV positivo em 50+ apostas = edge confirmado (Buchdahl)",
    }
