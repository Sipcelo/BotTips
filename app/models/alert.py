from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.connection import Base


class Alert(Base):
    __tablename__ = "alerts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    event_id: Mapped[int] = mapped_column(Integer, ForeignKey("events.id"), index=True)
    bookmaker: Mapped[str] = mapped_column(String(100))
    side: Mapped[str] = mapped_column(String(10))  # "home" | "away"
    odd: Mapped[float] = mapped_column(Float)
    fair_odd: Mapped[float] = mapped_column(Float)
    ev: Mapped[float] = mapped_column(Float)
    prob_real: Mapped[float] = mapped_column(Float)
    overround: Mapped[float] = mapped_column(Float)
    kelly_fraction: Mapped[float] = mapped_column(Float)
    stake_pct: Mapped[float] = mapped_column(Float)
    stake_amount: Mapped[float] = mapped_column(Float)
    bankroll: Mapped[float] = mapped_column(Float)
    odd_opening: Mapped[float | None] = mapped_column(Float, nullable=True)
    telegram_msg_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    sent_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow
    )

    # CLV — Closing Line Value (métrica primária de edge)
    # Atualizado a cada pipeline run com a última odd da Pinnacle antes do jogo
    pinnacle_closing_odd: Mapped[float | None] = mapped_column(Float, nullable=True)
    clv_percent: Mapped[float | None] = mapped_column(Float, nullable=True)
    clv_recorded_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    event: Mapped["Event"] = relationship(back_populates="alerts")  # noqa: F821

    def __repr__(self) -> str:
        return f"<Alert ev={self.ev:.1%} odd={self.odd} [{self.side}]>"
