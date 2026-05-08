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
    logger.info("Pipeline disparado manualmente via API")
    import asyncio
    asyncio.create_task(run_pipeline())
    return {"status": "pipeline triggered"}
