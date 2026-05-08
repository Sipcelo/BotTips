from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.connection import Base


class OddsSnapshot(Base):
    __tablename__ = "odds_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    event_id: Mapped[int] = mapped_column(Integer, ForeignKey("events.id"), index=True)
    bookmaker: Mapped[str] = mapped_column(String(100))
    home_odd: Mapped[float] = mapped_column(Float)
    away_odd: Mapped[float] = mapped_column(Float)
    draw_odd: Mapped[float | None] = mapped_column(Float, nullable=True)
    collected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, index=True
    )

    event: Mapped["Event"] = relationship(back_populates="odds_snapshots")  # noqa: F821

    def __repr__(self) -> str:
        return (
            f"<OddsSnapshot {self.bookmaker} "
            f"H:{self.home_odd} A:{self.away_odd} @ {self.collected_at}>"
        )
