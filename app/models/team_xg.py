"""
Armazena dados xG (Expected Goals) por time por liga por temporada.

Atualizado a cada 3 dias via Understat. Usado para detectar divergência
entre gols reais e xG acumulado — sinal de regressão à média ou skill real.

Regra do doc (sport_football_european.md):
    Se gols reais divergem do xG em > 30% nos últimos 10 jogos:
    → flag "overperforming — regressão provável" ou
    → flag "underperforming — melhora esperada"
"""

from datetime import datetime

from sqlalchemy import DateTime, Float, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.database.connection import Base


class TeamXG(Base):
    __tablename__ = "team_xg"
    __table_args__ = (
        UniqueConstraint("team_name", "league_key", "season", name="uq_team_league_season"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    # Identificação
    team_name: Mapped[str] = mapped_column(String(200), index=True)
    league_key: Mapped[str] = mapped_column(String(100), index=True)
    season: Mapped[str] = mapped_column(String(10))       # ex: "2024"

    # Acumulado na temporada
    matches_played: Mapped[int] = mapped_column(Integer, default=0)
    goals_for: Mapped[int] = mapped_column(Integer, default=0)
    goals_against: Mapped[int] = mapped_column(Integer, default=0)
    xg_for: Mapped[float] = mapped_column(Float, default=0.0)
    xg_against: Mapped[float] = mapped_column(Float, default=0.0)

    # Divergência: (goals_for - xg_for) / xg_for
    # > +0.30 → overperforming (regressão negativa provável)
    # < -0.30 → underperforming (melhora esperada)
    xg_divergence: Mapped[float] = mapped_column(Float, default=0.0)

    last_updated: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow
    )

    def __repr__(self) -> str:
        sign = "+" if self.xg_divergence >= 0 else ""
        return (
            f"<TeamXG {self.team_name} [{self.league_key}] "
            f"gols={self.goals_for} xG={self.xg_for:.1f} "
            f"div={sign}{self.xg_divergence:.0%}>"
        )
