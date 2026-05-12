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
from app.collectors.football_collector import fetch_historical_results
from app.collectors.mlb_collector import PitcherMatchup, fetch_probable_pitchers, find_pitcher_matchup
from app.collectors.odds_collector import (
    EventOdds,
    QUOTA_PAUSE_THRESHOLD,
    fetch_all_sports,
    get_quota_remaining,
)
from app.collectors.understat_collector import fetch_all_leagues_xg
from app.config import get_settings
from app.database.connection import AsyncSessionLocal
from app.engine.calculator import analyze_market, calculate_overround
from app.engine.dixon_coles import fit as dc_fit, predict as dc_predict
from app.engine.elo import elo_model
from app.engine.filter import _compute_confidence, apply_filters
from app.engine.kelly import calculate_kelly
from app.engine.model_store import (
    FOOTBALL_LEAGUES,
    get_model,
    is_football,
    needs_refit,
    store_model,
)
from app.models.alert import Alert
from app.models.event import Event
from app.models.odds import OddsSnapshot
from app.models.team_xg import TeamXG

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
    # xG Understat — a cada 3 dias (cadência de rodada europeia)
    # Roda às 06:00 UTC (03:00 BRT) para não competir com o pipeline
    scheduler.add_job(
        update_xg_data,
        trigger="interval",
        days=3,
        id="xg_update",
        replace_existing=True,
        next_run_time=datetime.now(tz=timezone.utc),  # roda uma vez no startup
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

    # Quota guard: para o pipeline se a cota estiver quase esgotada.
    # Protege contra exaurir os requests antes dos jogos do fim de semana.
    quota = get_quota_remaining()
    if quota is not None and quota <= QUOTA_PAUSE_THRESHOLD:
        logger.warning(
            "⚠️ QUOTA BAIXA: apenas %d requests restantes — pipeline pausado. "
            "Aumente POLL_INTERVAL_SECONDS no Railway ou aguarde renovação da cota.",
            quota,
        )
        from app.alerts.telegram import send_message
        await send_message(
            f"⚠️ <b>ValueBot — Cota da Odds API baixa</b>\n\n"
            f"Restam apenas <b>{quota} requests</b> do mês.\n"
            f"Pipeline pausado para preservar cota até os próximos jogos.\n\n"
            f"💡 Solução: aumente <code>POLL_INTERVAL_SECONDS</code> no Railway "
            f"(recomendado: 3600 ou mais).",
            parse_mode="HTML",
        )
        return

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

        # Ajusta modelos Dixon-Coles para ligas de futebol que precisam de refit
        football_leagues_active = {e.sport for e in upcoming if is_football(e.sport)}
        for league in football_leagues_active:
            if needs_refit(league):
                await _fit_dc_model(league)

        async with AsyncSessionLocal() as session:
            for event_odds in upcoming:
                await _process_event(session, event_odds, mlb_pitchers)
            # Calcula CLV para jogos que já começaram
            await _finalize_clv(session)

        logger.info("Pipeline concluído")

    except Exception as exc:
        logger.error("Pipeline falhou: %s", exc, exc_info=True)


async def _fit_dc_model(league_key: str) -> None:
    """
    Baixa dados históricos e ajusta Dixon-Coles para uma liga.

    Falhas são logadas mas não propagadas — pipeline continua sem modelo.
    """
    logger.info("DC: iniciando fitting para %s", league_key)
    try:
        matches = await fetch_historical_results(league_key)
        if len(matches) < 50:
            logger.warning(
                "DC: apenas %d partidas para %s — modelo não ajustado (mínimo 50)",
                len(matches), league_key,
            )
            return
        model = dc_fit(matches)
        store_model(league_key, model)
    except Exception as exc:
        logger.error("DC fitting falhou [%s]: %s — usando Pinnacle puro", league_key, exc)


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

    # Futebol: mercado 1X2 — inclui draw_odd na análise
    sport_is_football = is_football(event_odds.sport)

    # Obtém odds do Pinnacle como referência de fair odds
    sharp = next((b for b in event_odds.bookmakers if b.bookmaker in SHARP_BOOKS), None)
    if sharp is None:
        logger.debug("Sem Pinnacle para %s vs %s", event_odds.home_team, event_odds.away_team)
        return

    # Monta lista de odds do Pinnacle: 2-way para outros esportes, 3-way para futebol
    if sport_is_football and sharp.draw_odd:
        sharp_odds = [sharp.home_odd, sharp.draw_odd, sharp.away_odd]
    else:
        sharp_odds = [sharp.home_odd, sharp.away_odd]

    sharp_analysis = analyze_market(sharp_odds)
    fair_probs = sharp_analysis.fair_probs

    # Obtém probabilidades do modelo específico por esporte
    surface = None
    if "tennis" in event_odds.sport:
        surface = "clay"  # default; será refinado quando tennis_collector fornecer dados
        elo_probs = elo_model.predict(surface, event_odds.home_team, event_odds.away_team)
        probs_reais = [
            0.6 * elo_probs[0] + 0.4 * fair_probs[0],
            0.6 * elo_probs[1] + 0.4 * fair_probs[1],
        ]
    elif sport_is_football:
        # Futebol: Pinnacle é o benchmark primário (Wisdom of the Crowd).
        # O alerta só é válido se soft_odd > pinnacle_fair_odd.
        # DC entra apenas como flag de contexto (via warnings), não como fonte de EV.
        # Quando DC tiver CLV positivo validado em 50+ apostas, revisitar blend.
        probs_reais = fair_probs

        # Salva probs DC para flags de contexto (se modelo disponível)
        dc_model = get_model(event_odds.sport)
    else:
        probs_reais = fair_probs

    # Salva ou recupera o evento no banco
    db_event = await _upsert_event(session, event_odds, surface)

    # Avalia cada casa soft contra as fair odds
    settings = get_settings()
    for bm in event_odds.bookmakers:
        if bm.bookmaker in SHARP_BOOKS:
            continue

        # Futebol: inclui draw_odd se disponível
        if sport_is_football and bm.draw_odd:
            soft_odds = [bm.home_odd, bm.draw_odd, bm.away_odd]
        else:
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

        # Mapeia índice → side (futebol tem 3 outcomes: 0=home, 1=draw, 2=away)
        if sport_is_football and bm.draw_odd:
            side_map = {0: "home", 1: "draw", 2: "away"}
        else:
            side_map = {0: "home", 1: "away"}
        side = side_map[analysis.best_side_index]

        # Fair odd real = Pinnacle devigged (referência de valor, não a Betsson devigged)
        # Garante que o índice é válido para sharp_analysis (mesmo número de outcomes)
        sharp_idx = min(analysis.best_side_index, len(sharp_analysis.fair_odds) - 1)
        pinnacle_fair_odd = sharp_analysis.fair_odds[sharp_idx]

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

        # DC como flag de contexto: se modelo discorda da direção da aposta, adiciona aviso
        if sport_is_football and dc_model:
            dc_probs = dc_predict(event_odds.home_team, event_odds.away_team, dc_model)
            dc_prob_side = [dc_probs.home_win, dc_probs.draw, dc_probs.away_win]
            dc_best_idx = dc_prob_side.index(max(dc_prob_side))
            if dc_best_idx != analysis.best_side_index:
                active_warnings.append(
                    "Modelo DC aponta direção diferente do Pinnacle — confirmar antes de apostar"
                )

        # xG divergence flag — futebol europeu com dados Understat
        if sport_is_football:
            for team_name, team_side in [
                (event_odds.home_team, "home"),
                (event_odds.away_team, "away"),
            ]:
                xg_row = await _get_team_xg(session, team_name, event_odds.sport)
                if xg_row and xg_row.matches_played >= 5:
                    div = xg_row.xg_divergence
                    if div > 0.30:
                        active_warnings.append(
                            f"{team_name} marcando {div:+.0%} acima do xG "
                            f"({xg_row.goals_for}g / {xg_row.xg_for:.1f}xG) "
                            f"— regressão negativa provável"
                        )
                    elif div < -0.30:
                        active_warnings.append(
                            f"{team_name} marcando {div:+.0%} abaixo do xG "
                            f"({xg_row.goals_for}g / {xg_row.xg_for:.1f}xG) "
                            f"— melhora esperada"
                        )

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


async def update_xg_data() -> None:
    """
    Coleta xG do Understat para todas as ligas europeias monitoradas
    e persiste no banco. Roda a cada 3 dias.

    Divergência > 30% gera flag automática nos próximos alertas daquela equipe.
    """
    football_keys = [k for k in FOOTBALL_LEAGUES if "brazil" not in k]
    logger.info("xG update iniciado para %d ligas", len(football_keys))

    try:
        xg_list = await fetch_all_leagues_xg(football_keys)

        async with AsyncSessionLocal() as session:
            for data in xg_list:
                # Upsert: atualiza se existe, cria se não existe
                result = await session.execute(
                    select(TeamXG).where(
                        TeamXG.team_name == data.team_name,
                        TeamXG.league_key == data.league_key,
                        TeamXG.season == data.season,
                    )
                )
                row = result.scalar_one_or_none()

                if row:
                    row.matches_played = data.matches_played
                    row.goals_for = data.goals_for
                    row.goals_against = data.goals_against
                    row.xg_for = data.xg_for
                    row.xg_against = data.xg_against
                    row.xg_divergence = data.xg_divergence
                    row.last_updated = data.fetched_at
                else:
                    session.add(TeamXG(
                        team_name=data.team_name,
                        league_key=data.league_key,
                        season=data.season,
                        matches_played=data.matches_played,
                        goals_for=data.goals_for,
                        goals_against=data.goals_against,
                        xg_for=data.xg_for,
                        xg_against=data.xg_against,
                        xg_divergence=data.xg_divergence,
                        last_updated=data.fetched_at,
                    ))

            await session.commit()

        logger.info("xG update concluído: %d registros salvos", len(xg_list))

    except Exception as exc:
        logger.error("xG update falhou: %s", exc, exc_info=True)


async def _get_team_xg(
    session: AsyncSession,
    team_name: str,
    league_key: str,
) -> TeamXG | None:
    """Retorna dados xG de um time na temporada atual, ou None se indisponível."""
    from app.collectors.understat_collector import _current_season
    season = _current_season()
    result = await session.execute(
        select(TeamXG).where(
            TeamXG.team_name == team_name,
            TeamXG.league_key == league_key,
            TeamXG.season == season,
        )
    )
    return result.scalar_one_or_none()


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
