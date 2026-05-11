"""
Jobs agendados do ValueBot via APScheduler.

Pipeline principal: coleta odds → calcula EV → filtra → envia alertas.
Roda a cada POLL_INTERVAL_SECONDS (default: 300s).
"""

import logging
from datetime import datetime, timezone, timedelta

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.alerts.context import generate_context
from app.alerts.formatter import AlertData, format_alert
from app.alerts.telegram import send_alert, send_clv_report
from app.collectors.mlb_collector import PitcherMatchup, fetch_probable_pitchers, find_pitcher_matchup
from app.collectors.odds_collector import EventOdds, fetch_all_sports
from app.config import get_settings
from app.database.connection import AsyncSessionLocal
from app.engine.calculator import analyze_market, calculate_overround
from app.engine.elo import elo_model
from app.engine.filter import _compute_confidence, apply_filters
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
    # Relatório CLV semanal — domingo às 09:00 BRT (12:00 UTC)
    scheduler.add_job(
        weekly_clv_report,
        trigger="cron",
        day_of_week="sun",
        hour=12,
        minute=0,
        id="weekly_clv_report",
        replace_existing=True,
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

        # Busca pitchers MLB uma vez por run (1 request externo, sem cota)
        mlb_pitchers: list[PitcherMatchup] = []
        if any(e.sport == "baseball_mlb" for e in upcoming):
            mlb_pitchers = await fetch_probable_pitchers()

        async with AsyncSessionLocal() as session:
            for event_odds in upcoming:
                await _process_event(session, event_odds, mlb_pitchers)
            # Calcula CLV para jogos que já começaram
            await _finalize_clv(session)

        logger.info("Pipeline concluído")

    except Exception as exc:
        logger.error("Pipeline falhou: %s", exc, exc_info=True)


async def _process_event(
    session: AsyncSession,
    event_odds: EventOdds,
    mlb_pitchers: list[PitcherMatchup] | None = None,
) -> None:
    """Processa um evento: calcula EV, filtra e envia alerta se aplicável."""

    # MLB: resolve pitcher matchup (grátis via MLB Stats API)
    home_pitcher: str | None = None
    away_pitcher: str | None = None
    pitcher_confirmed = True

    if event_odds.sport == "baseball_mlb" and mlb_pitchers:
        matchup = find_pitcher_matchup(
            event_odds.home_team, event_odds.away_team, mlb_pitchers
        )
        if matchup:
            home_pitcher = matchup.home_pitcher
            away_pitcher = matchup.away_pitcher
            pitcher_confirmed = bool(home_pitcher and away_pitcher)
            if not pitcher_confirmed:
                logger.info(
                    "MLB pitcher não confirmado: %s vs %s",
                    event_odds.home_team, event_odds.away_team,
                )

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

        side = "home" if analysis.best_side_index == 0 else "away"

        # Fair odd real = Pinnacle devigged (referência de valor, não a Betsson devigged)
        pinnacle_fair_odd = sharp_analysis.fair_odds[analysis.best_side_index]

        # Verifica se já existe alerta — se sim, atualiza closing odd e pula
        existing = await _get_existing_alert(session, db_event.id, bm.bookmaker)
        if existing:
            existing.pinnacle_closing_odd = pinnacle_fair_odd
            await session.commit()
            continue

        kelly_result = calculate_kelly(
            prob_real=analysis.best_prob_real,
            odd=analysis.best_odd,
            bankroll=DEFAULT_BANKROLL,
        )

        # Piso mínimo de Kelly — edge muito pequeno após vig e atrito → skip
        if kelly_result.stake_amount == 0.0:
            logger.debug(
                "Kelly abaixo do piso mínimo [%s/%s] — descartado",
                event_odds.home_team, bm.bookmaker,
            )
            continue

        # Limite diário de alertas (anti-fadiga)
        today_count = await _daily_alert_count(session)
        if today_count >= DAILY_ALERT_CAP:
            logger.warning(
                "Cap diário atingido (%d alertas) — pipeline pausado até amanhã",
                DAILY_ALERT_CAP,
            )
            return

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

        # Warnings acumulados — pitcher MLB adiciona 1 flag extra
        active_warnings = list(result.warnings or [])
        if event_odds.sport == "baseball_mlb" and not pitcher_confirmed:
            active_warnings.append("Pitcher não confirmado — confiança reduzida")

        # Recalcula confiança após adicionar flags extra (pitcher, etc.)
        confidence = _compute_confidence(active_warnings)

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
            home_pitcher=home_pitcher,
            away_pitcher=away_pitcher,
            pitcher_confirmed=pitcher_confirmed,
            confidence=confidence,
            warnings=active_warnings or None,
        )

        msg = format_alert(alert_data)
        msg_id = await send_alert(msg)

        # Persiste alerta no banco
        db_alert = Alert(
            event_id=db_event.id,
            bookmaker=bm.bookmaker,
            side=side,
            odd=analysis.best_odd,
            fair_odd=pinnacle_fair_odd,
            ev=analysis.best_ev,
            prob_real=analysis.best_prob_real,
            overround=overround_soft,
            kelly_fraction=kelly_result.kelly_fraction,
            stake_pct=kelly_result.stake_pct,
            stake_amount=kelly_result.stake_amount,
            bankroll=DEFAULT_BANKROLL,
            telegram_msg_id=msg_id,
            pinnacle_closing_odd=pinnacle_fair_odd,  # será atualizado a cada run
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


DAILY_ALERT_CAP = 10


async def _daily_alert_count(session: AsyncSession) -> int:
    """Conta alertas enviados hoje (UTC). Usado para impor limite diário."""
    start_of_day = datetime.now(tz=timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    result = await session.execute(
        select(func.count(Alert.id)).where(Alert.sent_at >= start_of_day)
    )
    return result.scalar_one() or 0


async def _get_existing_alert(
    session: AsyncSession, event_id: int, bookmaker: str
) -> Alert | None:
    """Retorna alerta existente para este evento + casa, ou None."""
    result = await session.execute(
        select(Alert).where(
            Alert.event_id == event_id,
            Alert.bookmaker == bookmaker,
        )
    )
    return result.scalar_one_or_none()


async def _finalize_clv(session: AsyncSession) -> None:
    """
    Calcula CLV para alertas cujo jogo já começou.

    CLV = (odd_apostada / pinnacle_closing_odd) - 1

    Roda ao final de cada pipeline. Como os jogos em andamento são filtrados
    da coleta de odds, pinnacle_closing_odd já contém a última odd registrada
    antes do início — isso é a closing line.
    """
    now = datetime.now(tz=timezone.utc)
    result = await session.execute(
        select(Alert)
        .join(Event)
        .where(
            Alert.clv_percent.is_(None),
            Alert.pinnacle_closing_odd.is_not(None),
            Event.commence_time < now,
        )
    )
    alerts = result.scalars().all()

    for alert in alerts:
        clv = (alert.odd / alert.pinnacle_closing_odd) - 1
        alert.clv_percent = clv
        alert.clv_recorded_at = now
        logger.info(
            "CLV registrado: odd=%.2f closing=%.2f CLV=%+.1f%%",
            alert.odd, alert.pinnacle_closing_odd, clv * 100,
        )

    if alerts:
        await session.commit()


async def weekly_clv_report() -> None:
    """
    Gera e envia relatório semanal de CLV via Telegram.
    Roda todo domingo às 09:00 BRT.
    """
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(
                func.count(Alert.id).label("total"),
                func.avg(Alert.clv_percent).label("clv_medio"),
                func.sum(
                    func.cast(Alert.clv_percent > 0, type_=None)
                ).label("positivos"),
            ).where(Alert.clv_percent.is_not(None))
        )
        row = result.one()

    total = row.total or 0
    if total == 0:
        logger.info("CLV report: sem dados ainda")
        return

    clv_medio = (row.clv_medio or 0) * 100
    positivos = row.positivos or 0
    taxa = (positivos / total * 100) if total else 0

    await send_clv_report(total, clv_medio, positivos, taxa)
