from collections import Counter

from typing import List

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app import models, schemas
from app.config import settings
from app.database import get_db
from app.deps import get_current_user
from app.services.agenda import now_local
from app.services.polls import poll_status, results_visible

router = APIRouter(prefix="/games/{game_id}/poll", tags=["essen-umfrage"])


def _get_poll(db: Session, game_id: int) -> tuple[models.Game, models.EssenPoll]:
    game = db.query(models.Game).filter(models.Game.id == game_id).first()
    if not game:
        raise HTTPException(status_code=404, detail="Spiel nicht gefunden")
    if not game.poll:
        raise HTTPException(status_code=404, detail="Fuer dieses Spiel existiert keine Umfrage")
    return game, game.poll


@router.get("/options", response_model=List[schemas.PollOptionOut])
def list_options(
    game_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    _, poll = _get_poll(db, game_id)
    return [schemas.PollOptionOut(id=o.id, label=o.label) for o in poll.options]


@router.post("/vote", response_model=schemas.PollVoteOut)
def cast_vote(
    game_id: int,
    body: schemas.PollVoteRequest,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    game, poll = _get_poll(db, game_id)
    now = now_local()
    status = poll_status(poll, now)
    if status != "offen":
        raise HTTPException(
            status_code=400,
            detail=f"Umfrage ist aktuell nicht offen (Status: {status})",
        )

    option = (
        db.query(models.PollOption)
        .filter(models.PollOption.id == body.option_id, models.PollOption.poll_id == poll.id)
        .first()
    )
    if not option:
        raise HTTPException(status_code=404, detail="Option gehoert nicht zu dieser Umfrage")

    # Anonym: es wird nur eine Stimme pro User gespeichert (aenderbar solange
    # offen), aber weder Admin noch Captain koennen waehrend die Umfrage
    # offen ist einsehen, wer wie abgestimmt hat - dafuer gibt es schlicht
    # keinen entsprechenden GET-Endpoint waehrend status == "offen".
    existing = (
        db.query(models.PollVote)
        .filter(models.PollVote.poll_id == poll.id, models.PollVote.user_id == current_user.id)
        .first()
    )
    if existing:
        existing.option_id = option.id
        existing.voted_at = now
    else:
        db.add(
            models.PollVote(
                poll_id=poll.id, option_id=option.id, user_id=current_user.id, voted_at=now
            )
        )
    db.commit()

    return schemas.PollVoteOut(status="offen", my_vote_option_id=option.id)


@router.get("/results", response_model=schemas.PollResultsOut)
def get_results(
    game_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    game, poll = _get_poll(db, game_id)
    now = now_local()

    if not results_visible(game, now):
        raise HTTPException(
            status_code=403,
            detail="Auswertung wird erst nach dem Spiel sichtbar",
        )

    votes = db.query(models.PollVote).filter(models.PollVote.poll_id == poll.id).all()
    total = len(votes)
    counts = Counter(v.option_id for v in votes)

    results = []
    max_count = max(counts.values()) if counts else 0
    winning_options = []
    for option in poll.options:
        c = counts.get(option.id, 0)
        pct = round((c / total) * 100, 1) if total else 0.0
        results.append(schemas.PollOptionResult(option_id=option.id, label=option.label, votes=c, percentage=pct))
        if c == max_count and max_count > 0:
            winning_options.append(option.label)

    # Wer kommt mit: alle Waehler ausser die der "komme nicht mit"-Option
    skip_option_ids = {o.id for o in poll.options if o.label.strip().casefold() == settings.poll_skip_option.strip().casefold()}
    mitkommende_ids = {v.user_id for v in votes if v.option_id not in skip_option_ids}
    mitkommende_names = [
        u.name for u in db.query(models.User).filter(models.User.id.in_(mitkommende_ids)).all()
    ] if mitkommende_ids else []

    return schemas.PollResultsOut(
        status=poll_status(poll, now),
        total_votes=total,
        results=results,
        mitkommende=sorted(mitkommende_names),
        winning_options=winning_options,
    )


@router.get("/restaurants", response_model=schemas.RestaurantSuggestionsOut)
def get_restaurant_suggestions(
    game_id: int,
    refresh: bool = False,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """Restaurants zur Gewinner-Kueche in der Naehe der Halle (Google mit Key, sonst OpenStreetMap).
    Sichtbar ab Umfrage-Schluss (Admin/Captain zum Reservieren) bzw. fuer alle nach dem Spiel."""
    game, poll = _get_poll(db, game_id)
    now = now_local()
    early = poll_status(poll, now) == "geschlossen" and (current_user.is_trainer or current_user.is_captain)
    if not results_visible(game, now) and not early:
        raise HTTPException(status_code=403, detail="Restaurantvorschläge sind erst nach dem Spiel verfügbar")
    from app.services.food_jobs import suggestions_for
    data = suggestions_for(db, game, poll, force=refresh)
    if not data["winners"]:
        return schemas.RestaurantSuggestionsOut(based_on_options=[], suggestions=[],
                                                note="Noch keine Stimmen (ausser 'komme nicht mit').")
    sug = [schemas.RestaurantSuggestion(
        name=r["name"] + (f" ⭐{r['rating']}" if r.get("rating") else ""),
        address=" · ".join(x for x in [r.get("address"), f"{r['distance_km']} km von der Halle" if r.get("distance_km") is not None else None] if x) or None,
        maps_link=r["maps_link"]) for r in data["restaurants"]]
    note = None if sug else ("Keine Hallen-Adresse bekannt." if not data["near"] else "Keine passenden Restaurants gefunden.")
    return schemas.RestaurantSuggestionsOut(based_on_options=data["winners"], suggestions=sug, note=note)
