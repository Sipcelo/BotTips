"""
Jobs agendados do ValueBot via APScheduler.

Pipeline principal: coleta odds → calcula EV → filtra → envia alertas.
Roda a cada POLL_INTERVAL_SECONDS (default: 300s).
"""

import logging
from datetime import datetime, timezone

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.alerts.context import generate_context
from app.alerts.formatter import AlertData, format_alert
from app.alerts.telegram import send_alert
from app.collectors.odds_collector import EventOdds, fetch_all_sports
from app.config import get_settings
from app.database.connection import AsyncSessionLocal
from app.engine.calculator import analyze_market, calculate_overround
from app.engine.elo import elo_model
from app.engine.filter import apply_filters
from app.engine.kelly import calculate_kelly
from app.models.alert import Alert
from app.models.event import Event
from app.models.odds import OddsSnapshot

logger = logging.getLogger(__name__)

# Referência de bankroll padrão (pode ser configurado no futuro)
DEFAULT_BANKROLL = 50.0

# Casas "sharp" — usadas como referência de fair odds
SHARP_BOOKS = {"pinnacle"}


def build_scheduler() -> AsyncIOScheduler:
    settings = get_settings()
    scheduler = AsyncIOScheduler(timezone="UTC")
    scheduler.add_job(
        run_pipeline,
        trigger="interval",
        seconds=settings.poll_interval_seconds,
        id="value_bet_pipeline",
        replace_existing=True,
        next_run_time=datetime.now(tz=timezone.utc),  # roda imediatamente ao iniciar
    )
    return scheduler


async def run_pipeline() -> None:
    """
    Executa o pipeline completo de detecção de value bets.

    Fluxo:
        1. Coleta odds de todos os esportes alvo
        2. Para cada evento, compara odds da casa alvo com fair odds (Pinnacle)
        3. Calcula EV, aplica filtros, calcula Kelly
        4. Salva no banco e envia alerta no Telegram
    """
    now = datetime.now(tz=timezone.utc)
    logger.info("Pipeline iniciado — %s", now.isoformat())

    try:
        events = await fetch_all_sports()
        logger.info("Pipeline: %d eventos com odds", len(events))

        # Filtra apenas jogos que ainda não começaram — odds live são não confiáveis
        # sem dados de placar em tempo real (risco de falso positivo alto)
        upcoming = [e for e in events if e.commence_time > now]
        skipped = len(events) - len(upcoming)
        if skipped:
            logger.info("Pipeline: %d evento(s) em andamento ignorado(s)", skipped)

        async with AsyncSessionLocal() as session:
            for event_odds in upcoming:
                await _process_event(session, event_odds)

        logger.info("Pipeline concluído")

    except Exception as exc:
        logger.error("Pipeline falhou: %s", exc, exc_info=True)


async def _process_event(session: AsyncSession, event_odds: EventOdds) -> None:
    """Processa um evento: calcula EV, filtra e envia alerta se aplicável."""

    # Obtém odds do Pinnacle como referência de fair odds
    sharp = next((b for b in event_odds.bookmakers if b.bookmaker in SHARP_BOOKS), None)
    if sharp is None:
        logger.debug("Sem Pinnacle para %s vs %s", event_odds.home_team, event_odds.away_team)
        return

    # Calcula fair odds com base no Pinnacle (mercado mais eficiente)
    sharp_analysis = analyze_market([sharp.home_odd, sharp.away_odd])
    fair_probs = sharp_analysis.fair_probs

    # Obtém probabilidades do modelo Elo (se disponível)
    surface = None
    if "tennis" in event_odds.sport:
        surface = "clay"  # default; será refinado quando tennis_collector fornecer dados
        elo_probs = elo_model.predict(surface, event_odds.home_team, event_odds.away_team)
        # Média ponderada: 60% Elo + 40% fair odds do Pinnacle
        probs_reais = [
            0.6 * elo_probs[0] + 0.4 * fair_probs[0],
            0.6 * elo_probs[1] + 0.4 * fair_probs[1],
        ]
    else:
        probs_reais = fair_probs

    # Salva ou recupera o evento no banco
    db_event = await _upsert_event(session, event_odds, surface)

    # Avalia cada casa soft contra as fair odds
    settings = get_settings()
    for bm in event_odds.bookmakers:
        if bm.bookmaker in SHARP_BOOKS:
            continue

        soft_odds = [bm.home_odd, bm.away_odd]
        analysis = analyze_market(soft_odds, probs_reais=probs_reais)

        overround_soft = calculate_overround(soft_odds)
        result = apply_filters(
            ev=analysis.best_ev,
            odd=analysis.best_odd,
            overround=overround_soft,
        )

        if not result.passed:
            logger.debug(
                "Filtrado [%s/%s]: %s",
                event_odds.home_team, bm.bookmaker, result.failures,
            )
            continue

        # Já enviamos alerta para este evento+casa hoje?
        already_alerted = await _already_alerted(session, db_event.id, bm.bookmaker)
        if already_alerted:
            continue

        side = "home" if analysis.best_side_index == 0 else "away"

        # Fair odd real = Pinnacle devigged (referência de valor, não a Betsson devigged)
        pinnacle_fair_odd = sharp_analysis.fair_odds[analysis.best_side_index]

        kelly_result = calculate_kelly(
            prob_real=analysis.best_prob_real,
            odd=analysis.best_odd,
            bankroll=DEFAULT_BANKROLL,
        )

        # Contexto qualitativo via OpenRouter (falha silenciosamente)
        ai_context = await generate_context(
            sport=event_odds.sport,
            home_team=event_odds.home_team,
            away_team=event_odds.away_team,
            surface=surface,
            side=side,
            ev=analysis.best_ev,
            odd=analysis.best_odd,
        )

        alert_data = AlertData(
            sport=event_odds.sport,
            tournament=event_odds.home_team.split(" ")[0],  # placeholder
            home_team=event_odds.home_team,
            away_team=event_odds.away_team,
            surface=surface,
            commence_time=event_odds.commence_time,
            bookmaker=bm.bookmaker,
            side=side,
            odd=analysis.best_odd,
            fair_odd=pinnacle_fair_odd,   # referência Pinnacle, não Betsson
            ev=analysis.best_ev,
            prob_real=analysis.best_prob_real,
            overround=overround_soft,
            bankroll=DEFAULT_BANKROLL,
            kelly_fraction=kelly_result.kelly_fraction,
            stake_pct=kelly_result.stake_pct,
            stake_amount=kelly_result.stake_amount,
            ai_context=ai_context,
            warnings=result.warnings or None,
        )

        msg = format_alert(alert_data)
        msg_id = await send_alert(msg)

        # Persiste alerta no banco
        db_alert = Alert(
            event_id=db_event.id,
            bookmaker=bm.bookmaker,
            side=side,
            odd=analysis.best_odd,
            fair_odd=analysis.best_fair_odd,
            ev=analysis.best_ev,
            prob_real=analysis.best_prob_real,
            overround=overround_soft,
            kelly_fraction=kelly_result.kelly_fraction,
            stake_pct=kelly_result.stake_pct,
            stake_amount=kelly_result.stake_amount,
            bankroll=DEFAULT_BANKROLL,
            telegram_msg_id=msg_id,
        )
        session.add(db_alert)

        # Salva snapshot de odds
        snapshot = OddsSnapshot(
            event_id=db_event.id,
            bookmaker=bm.bookmaker,
            home_odd=bm.home_odd,
            away_odd=bm.away_odd,
        )
        session.add(snapshot)

        await session.commit()
        logger.info(
            "Alerta enviado: %s vs %s [%s] EV=%.1f%% odd=%.2f",
            event_odds.home_team, event_odds.away_team,
            bm.bookmaker, analysis.best_ev * 100, analysis.best_odd,
        )


async def _upsert_event(
    session: AsyncSession, event_odds: EventOdds, surface: str | None
) -> Event:
    """Cria ou recupera o Event no banco pelo external_id."""
    result = await session.execute(
        select(Event).where(Event.external_id == event_odds.external_id)
    )
    existing = result.scalar_one_or_none()

    if existing:
        return existing

    event = Event(
        external_id=event_odds.external_id,
        sport=event_odds.sport,
        home_team=event_odds.home_team,
        away_team=event_odds.away_team,
        surface=surface,
        commence_time=event_odds.commence_time,
    )
    session.add(event)
    await session.flush()  # gera o id sem commit
    return event


async def _already_alerted(session: AsyncSession, event_id: int, bookmaker: str) -> bool:
    """Verifica se já enviamos alerta para este evento + casa hoje."""
    result = await session.execute(
        select(Alert).where(
            Alert.event_id == event_id,
            Alert.bookmaker == bookmaker,
        )
    )
    return result.scalar_one_or_none() is not None
