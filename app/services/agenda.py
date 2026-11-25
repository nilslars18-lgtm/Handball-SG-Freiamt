"""Gemeinsame Helfer fuer Termine (Spiele + Trainings + Events), Saison und Zeit."""
from datetime import datetime, timedelta
from typing import List, Optional, Tuple
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app import models

TZ = ZoneInfo("Europe/Zurich")

KIND_LABEL = {"spiel": "Spiel", "training": "Training", "event": "Event", "sitzung": "Teamsitzung"}


def now_local() -> datetime:
    """Jetzt in Schweizer Zeit, ohne Zeitzone (so liegen die Termine in der DB)."""
    return datetime.now(TZ).replace(tzinfo=None)


def season_of(dt: datetime) -> int:
    """Saison beginnt am 1. August. Saison 2026 = 1.8.2026 - 31.7.2027."""
    return dt.year if dt.month >= 8 else dt.year - 1


def season_bounds(season: int) -> Tuple[datetime, datetime]:
    return datetime(season, 8, 1), datetime(season + 1, 8, 1)


def season_label(season: int) -> str:
    return f"{season}/{str(season + 1)[-2:]}"


def active_players(db: Session) -> List[models.User]:
    return (
        db.query(models.User)
        .filter(
            models.User.is_active == True,  # noqa: E712
            models.User.base_role == models.BaseRole.PLAYER,
            ~models.User.staff.has(),
        )
        .order_by(models.User.name)
        .all()
    )


def team_members(db: Session) -> List[models.User]:
    """Spieler + Trainer (Staff): alle, die sich an-/abmelden muessen (und gebuesst werden koennen).
    Spieler zuerst, dann Trainer. Fuer Zahlen/Uebersichten weiterhin active_players() nehmen."""
    rows = (
        db.query(models.User)
        .filter(models.User.is_active == True, models.User.base_role == models.BaseRole.PLAYER)  # noqa: E712
        .order_by(models.User.name)
        .all()
    )
    return [u for u in rows if not u.staff] + [u for u in rows if u.staff]


def game_declined_ids(db: Session, game: models.Game) -> set:
    """Spieler, die fuer ein Spiel sicher nicht dabei sind (selbst abgemeldet
    oder vom Trainer nicht/als verletzt aufgeboten)."""
    out = set()
    for a in db.query(models.Availability).filter(models.Availability.game_id == game.id).all():
        if a.status in (models.AvailabilityStatus.VERLETZT, models.AvailabilityStatus.ABWESEND):
            out.add(a.user_id)
    for a in db.query(models.Aufgebot).filter(models.Aufgebot.game_id == game.id).all():
        if a.status in (models.AufgebotStatus.NICHT_AUFGEBOTEN, models.AufgebotStatus.VERLETZT):
            out.add(a.user_id)
    return out


def event_declined_ids(db: Session, event: models.TeamEvent) -> set:
    return {
        r.user_id
        for r in db.query(models.EventResponse).filter(models.EventResponse.event_id == event.id).all()
        if r.status in ("absage", "verletzt", "individuell")
    }


def game_responded_ids(db: Session, game_id: int) -> set:
    return {
        a.user_id
        for a in db.query(models.Availability).filter(models.Availability.game_id == game_id).all()
        if a.status != models.AvailabilityStatus.UNBEKANNT
    }


def event_responded_ids(db: Session, event_id: int) -> set:
    return {
        r.user_id
        for r in db.query(models.EventResponse).filter(models.EventResponse.event_id == event_id).all()
        if r.status in ("zusage", "absage", "verletzt", "individuell")
    }


def ref_title(db: Session, ref_kind: str, ref_id: int) -> Optional[str]:
    if ref_kind == "game":
        g = db.get(models.Game, ref_id)
        if not g:
            return None
        return f"Spiel gegen {g.opponent} ({g.start_time.strftime('%d.%m. %H:%M')})"
    e = db.get(models.TeamEvent, ref_id)
    if not e:
        return None
    name = e.title or KIND_LABEL.get(e.kind, "Termin")
    return f"{name} ({e.start_time.strftime('%d.%m. %H:%M')})"


def ref_start(db: Session, ref_kind: str, ref_id: int) -> Optional[datetime]:
    obj = db.get(models.Game, ref_id) if ref_kind == "game" else db.get(models.TeamEvent, ref_id)
    return obj.start_time if obj else None


def next_season_end(today: Optional[datetime] = None) -> datetime:
    today = today or now_local()
    s = season_of(today)
    return datetime(s + 1, 7, 31, 23, 59)


def daterange_weekdays(start: datetime, end: datetime, weekdays: set):
    d = datetime(start.year, start.month, start.day)
    while d <= end:
        if d.weekday() in weekdays:
            yield d
        d += timedelta(days=1)
