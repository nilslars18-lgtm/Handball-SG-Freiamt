from datetime import datetime
from typing import List

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app import models, schemas
from app.config import settings
from app.database import get_db
from app.deps import require_admin

router = APIRouter(prefix="/games/{game_id}/aufgebot", tags=["aufgebot"])

VALID_STATUSES = {
    models.AufgebotStatus.AUFGEBOTEN.value,
    models.AufgebotStatus.VERLETZT.value,
    models.AufgebotStatus.NICHT_AUFGEBOTEN.value,
}


def _get_game(db: Session, game_id: int) -> models.Game:
    game = db.query(models.Game).filter(models.Game.id == game_id).first()
    if not game:
        raise HTTPException(status_code=404, detail="Spiel nicht gefunden")
    return game


@router.get("", response_model=List[schemas.AufgebotPlayerOut])
def list_aufgebot(
    game_id: int,
    db: Session = Depends(get_db),
    admin: models.User = Depends(require_admin),
):
    """Uebersicht fuer den Trainer: Status aller aktiven Spieler fuer dieses Spiel."""
    game = _get_game(db, game_id)
    days_until = (game.start_time - datetime.utcnow()).total_seconds() / 86400
    within_window = days_until <= settings.aufgebot_days_before

    players = (
        db.query(models.User)
        .filter(models.User.base_role == models.BaseRole.PLAYER, models.User.is_active == True)  # noqa: E712
        .all()
    )
    entries = {
        e.user_id: e
        for e in db.query(models.Aufgebot).filter(models.Aufgebot.game_id == game_id).all()
    }
    availabilities = {
        a.user_id: a.status.value
        for a in db.query(models.Availability).filter(models.Availability.game_id == game_id).all()
    }

    result = []
    for player in players:
        entry = entries.get(player.id)
        if not within_window or entry is None:
            status = models.AufgebotStatus.PENDING.value
            decided = False
        else:
            status = entry.status.value
            decided = True
        result.append(
            schemas.AufgebotPlayerOut(
                user_id=player.id,
                name=player.name,
                status=status,
                decided=decided,
                availability_status=availabilities.get(player.id, models.AvailabilityStatus.UNBEKANNT.value),
            )
        )
    return result


@router.put("/{user_id}", response_model=schemas.AufgebotPlayerOut)
def set_aufgebot(
    game_id: int,
    user_id: int,
    body: schemas.AufgebotSetRequest,
    db: Session = Depends(get_db),
    admin: models.User = Depends(require_admin),
):
    if body.status not in VALID_STATUSES:
        raise HTTPException(
            status_code=400,
            detail=f"status muss einer von {sorted(VALID_STATUSES)} sein",
        )

    game = _get_game(db, game_id)
    days_until = (game.start_time - datetime.utcnow()).total_seconds() / 86400
    if days_until > settings.aufgebot_days_before:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Aufgebot ist erst ab {settings.aufgebot_days_before} Tagen vor dem "
                "Spiel moeglich"
            ),
        )

    player = db.query(models.User).filter(models.User.id == user_id).first()
    if not player:
        raise HTTPException(status_code=404, detail="Spieler nicht gefunden")

    entry = (
        db.query(models.Aufgebot)
        .filter(models.Aufgebot.game_id == game_id, models.Aufgebot.user_id == user_id)
        .first()
    )
    if entry is None:
        entry = models.Aufgebot(game_id=game_id, user_id=user_id)
        db.add(entry)

    entry.status = models.AufgebotStatus(body.status)
    entry.set_at = datetime.utcnow()
    db.commit()
    db.refresh(entry)

    return schemas.AufgebotPlayerOut(
        user_id=player.id, name=player.name, status=entry.status.value, decided=True
    )
