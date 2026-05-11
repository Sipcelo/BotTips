# Importa todos os models para que Base.metadata.create_all os encontre
from app.models.alert import Alert
from app.models.event import Event
from app.models.odds import OddsSnapshot
from app.models.team_xg import TeamXG

__all__ = ["Alert", "Event", "OddsSnapshot", "TeamXG"]
