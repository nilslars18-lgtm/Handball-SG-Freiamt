from datetime import datetime, timedelta
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, UploadFile
from sqlalchemy.orm import Session

from app import models, schemas
from app.config import settings
from app.database import get_db
from app.deps import get_current_user, require_admin
from app.ics_import import parse_ics_bytes
from app.services.places import build_maps_link, resolve_address
from app.services.directions import compute_departure_time
from app.services.polls import create_poll_for_game, poll_status, results_visible

router = APIRouter(prefix="/games", tags=["games"])


def _aufgebot_status_for(db: Session, game: models.Game, user: models.User) -> tuple[str, bool]:
    """Gibt (status, is_decided) zurueck. Vor dem Aufgebots-Fenster (mehr als
    AUFGEBOT_DAYS_BEFORE Tage vor dem Spiel) ist der Status immer 'pending',
    unabhaengig davon, ob eine DB-Zeile existiert."""
    days_until = (game.start_time - datetime.utcnow()).total_seconds() / 86400
    entry = (
        db.query(models.Aufgebot)
        .filter(models.Aufgebot.game_id == game.id, models.Aufgebot.user_id == user.id)
        .first()
    )
    if days_until > settings.aufgebot_days_before:
        return models.AufgebotStatus.PENDING.value, False
    if entry is None:
        return models.AufgebotStatus.PENDING.value, False
    return entry.status.value, True


def _availability_status_for(db: Session, game_id: int, user_id: int) -> str:
    entry = (
        db.query(models.Availability)
        .filter(models.Availability.game_id == game_id, models.Availability.user_id == user_id)
        .first()
    )
    return entry.status.value if entry else models.AvailabilityStatus.UNBEKANNT.value


def _build_list_item(db: Session, game: models.Game, user: models.User) -> schemas.GameListItem:
    my_status, decided = _aufgebot_status_for(db, game, user)
    return schemas.GameListItem(
        id=game.id,
        opponent=game.opponent,
        is_home=game.is_home,
        start_time=game.start_time,
        end_time=game.end_time,
        location_name=game.location_name,
        resolved_address=game.resolved_address,
        maps_link=game.maps_link,
        my_aufgebot_status=my_status,
        my_aufgebot_decided=decided,
        my_availability_status=_availability_status_for(db, game.id, user.id),
    )


def _build_detail(db: Session, game: models.Game, user: models.User) -> schemas.GameDetail:
    base = _build_list_item(db, game, user)
    poll_out = None
    if game.poll:
        now = datetime.utcnow()
        status = poll_status(game.poll, now)
        my_vote = (
            db.query(models.PollVote)
            .filter(models.PollVote.poll_id == game.poll.id, models.PollVote.user_id == user.id)
            .first()
        )
        poll_out = schemas.PollSummary(
            id=game.poll.id,
            status=status,
            opens_at=game.poll.opens_at,
            closes_at=game.poll.closes_at,
            my_vote_option_id=my_vote.option_id if my_vote else None,
            results_visible=results_visible(game, now),
        )
    return schemas.GameDetail(**base.model_dump(), poll=poll_out)


@router.post("/import", response_model=schemas.ImportResult)
async def import_ics(
    file: UploadFile,
    db: Session = Depends(get_db),
    admin: models.User = Depends(require_admin),
):
    if not file.filename.lower().endswith(".ics"):
        raise HTTPException(status_code=400, detail="Bitte eine .ics-Datei hochladen")

    content = await file.read()
    try:
        parsed_games = parse_ics_bytes(content)
    except Exception as exc:  # ungültige/kaputte ICS-Datei
        raise HTTPException(status_code=400, detail=f"ICS-Datei konnte nicht gelesen werden: {exc}")

    imported = 0
    skipped_existing = 0
    unmatched = 0
    details: List[schemas.ImportResultItem] = []

    for pg in parsed_games:
        # Dedup: zuerst über ics_uid (zuverlässigste Methode), sonst über
        # opponent + start_time (falls die Quelle mal keine UID liefert)
        existing = None
        if pg.ics_uid:
            existing = db.query(models.Game).filter(models.Game.ics_uid == pg.ics_uid).first()
        if existing is None:
            existing = (
                db.query(models.Game)
                .filter(
                    models.Game.opponent == pg.opponent,
                    models.Game.start_time == pg.start_time,
                )
                .first()
            )

        if existing:
            skipped_existing += 1
            details.append(
                schemas.ImportResultItem(
                    opponent=pg.opponent,
                    start_time=pg.start_time,
                    status="übersprungen (bereits vorhanden)",
                )
            )
            continue

        resolved_address = None
        if pg.location_name:
            resolved_address = await resolve_address(pg.location_name)

        maps_destination = resolved_address or pg.location_name or pg.opponent
        maps_link = build_maps_link(maps_destination)

        game = models.Game(
            opponent=pg.opponent,
            is_home=pg.is_home,
            start_time=pg.start_time,
            end_time=pg.end_time,
            location_name=pg.location_name,
            resolved_address=resolved_address,
            maps_link=maps_link,
            ics_uid=pg.ics_uid,
        )
        db.add(game)
        db.flush()  # game.id verfuegbar machen

        create_poll_for_game(db, game)  # Feature 4: Umfrage automatisch anlegen

        imported += 1

        status = "importiert" if pg.is_home is not None else "gegner nicht erkannt (importiert, bitte prüfen)"
        if pg.is_home is None:
            unmatched += 1
        details.append(
            schemas.ImportResultItem(opponent=pg.opponent, start_time=pg.start_time, status=status)
        )

    db.commit()

    return schemas.ImportResult(
        imported=imported,
        skipped_existing=skipped_existing,
        unmatched_team_name=unmatched,
        details=details,
    )


@router.get("", response_model=List[schemas.GameListItem])
def list_games(
    upcoming_only: bool = True,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    query = db.query(models.Game).order_by(models.Game.start_time)
    if upcoming_only:
        query = query.filter(models.Game.start_time >= datetime.utcnow() - timedelta(hours=3))
    games = query.all()
    return [_build_list_item(db, g, current_user) for g in games]


@router.get("/next", response_model=schemas.NextGameOut)
def get_next_game(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """Fuer die Startseite: das naechste kommende Spiel (oder das zuletzt
    vergangene innerhalb der letzten 3h, falls es gerade laeuft/eben zu Ende
    war)."""
    game = (
        db.query(models.Game)
        .filter(models.Game.start_time >= datetime.utcnow() - timedelta(hours=3))
        .order_by(models.Game.start_time)
        .first()
    )
    if not game:
        return schemas.NextGameOut(game=None)
    return schemas.NextGameOut(game=_build_detail(db, game, current_user))


@router.get("/{game_id}", response_model=schemas.GameDetail)
def get_game(
    game_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    game = db.query(models.Game).filter(models.Game.id == game_id).first()
    if not game:
        raise HTTPException(status_code=404, detail="Spiel nicht gefunden")
    return _build_detail(db, game, current_user)


@router.put("/{game_id}/availability", response_model=schemas.AvailabilityOut)
def set_my_availability(
    game_id: int,
    body: schemas.AvailabilitySetRequest,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """Spieler-Selbstauskunft: kann ich spielen, bin ich abwesend oder
    verletzt. Jederzeit aenderbar, unabhaengig vom Trainer-Aufgebot."""
    valid = {
        models.AvailabilityStatus.KANN_SPIELEN.value,
        models.AvailabilityStatus.VERLETZT.value,
        models.AvailabilityStatus.ABWESEND.value,
    }
    if body.status not in valid:
        raise HTTPException(status_code=400, detail=f"status muss einer von {sorted(valid)} sein")

    game = db.query(models.Game).filter(models.Game.id == game_id).first()
    if not game:
        raise HTTPException(status_code=404, detail="Spiel nicht gefunden")

    entry = (
        db.query(models.Availability)
        .filter(models.Availability.game_id == game_id, models.Availability.user_id == current_user.id)
        .first()
    )
    if entry is None:
        entry = models.Availability(game_id=game_id, user_id=current_user.id)
        db.add(entry)
    entry.status = models.AvailabilityStatus(body.status)
    entry.set_at = datetime.utcnow()
    db.commit()
    db.refresh(entry)
    return schemas.AvailabilityOut(status=entry.status.value, set_at=entry.set_at)


@router.get("/{game_id}/anfahrt", response_model=schemas.DepartureInfo)
async def get_anfahrt(
    game_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """Feature 3: berechnet, um wie viel Uhr der eingeloggte Spieler losfahren
    muss, um ARRIVAL_BUFFER_MINUTES vor Spielbeginn in der Halle zu sein."""
    game = db.query(models.Game).filter(models.Game.id == game_id).first()
    if not game:
        raise HTTPException(status_code=404, detail="Spiel nicht gefunden")

    origin = current_user.full_address
    destination = game.resolved_address or game.location_name or game.opponent
    arrival_target = game.start_time - timedelta(minutes=settings.arrival_buffer_minutes)

    if not origin:
        return schemas.DepartureInfo(
            origin="",
            destination=destination or "",
            arrival_target=arrival_target,
            note="Bitte zuerst deine Adresse im Profil hinterlegen (PUT /users/me/address).",
        )

    result = await compute_departure_time(origin, destination, arrival_target)
    if result is None:
        note = (
            "Kein GOOGLE_MAPS_API_KEY konfiguriert - Anfahrtsberechnung nicht verfuegbar."
            if not settings.google_maps_api_key
            else "Route konnte nicht berechnet werden (Adresse pruefen oder Google-API-Fehler)."
        )
        return schemas.DepartureInfo(
            origin=origin, destination=destination or "", arrival_target=arrival_target, note=note
        )

    departure_time = arrival_target - timedelta(seconds=result.duration_seconds)
    return schemas.DepartureInfo(
        origin=origin,
        destination=destination or "",
        arrival_target=arrival_target,
        departure_time=departure_time,
        duration_text=result.duration_text,
        distance_text=result.distance_text,
    )
