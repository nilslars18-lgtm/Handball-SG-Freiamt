from collections import Counter
from datetime import datetime
from typing import List

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app import models, schemas
from app.config import settings
from app.database import get_db
from app.deps import get_current_user
from app.services.places import search_open_restaurants
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
    now = datetime.utcnow()
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
    now = datetime.utcnow()

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
async def get_restaurant_suggestions(
    game_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    game, poll = _get_poll(db, game_id)
    now = datetime.utcnow()

    if not results_visible(game, now):
        raise HTTPException(
            status_code=403,
            detail="Restaurantvorschläge sind erst nach dem Spiel verfügbar",
        )

    votes = db.query(models.PollVote).filter(models.PollVote.poll_id == poll.id).all()
    counts = Counter(v.option_id for v in votes)
    skip_option_ids = {o.id for o in poll.options if o.label.strip().casefold() == settings.poll_skip_option.strip().casefold()}

    relevant_counts = {oid: c for oid, c in counts.items() if oid not in skip_option_ids}
    if not relevant_counts:
        return schemas.RestaurantSuggestionsOut(
            based_on_options=[], suggestions=[], note="Noch keine Stimmen (ausser 'komme nicht mit')."
        )

    max_count = max(relevant_counts.values())
    winning_option_ids = [oid for oid, c in relevant_counts.items() if c == max_count]
    label_by_id = {o.id: o.label for o in poll.options}
    winning_labels = [label_by_id[oid] for oid in winning_option_ids]

    near = game.resolved_address or game.location_name or ""
    if not near:
        return schemas.RestaurantSuggestionsOut(
            based_on_options=winning_labels,
            suggestions=[],
            note="Keine Hallen-Adresse bekannt - Vorschläge nicht möglich.",
        )
    if not settings.google_maps_api_key:
        return schemas.RestaurantSuggestionsOut(
            based_on_options=winning_labels,
            suggestions=[],
            note="Kein GOOGLE_MAPS_API_KEY konfiguriert - Restaurantvorschläge nicht verfügbar.",
        )

    suggestions: list[schemas.RestaurantSuggestion] = []
    for label in winning_labels:
        found = await search_open_restaurants(label, near, limit=3 if len(winning_labels) == 1 else 2)
        for f in found:
            dest = f.get("address") or f.get("name")
            suggestions.append(
                schemas.RestaurantSuggestion(
                    name=f["name"],
                    address=f.get("address"),
                    maps_link=f"https://www.google.com/maps/dir/?api=1&destination={dest}",
                )
            )

    return schemas.RestaurantSuggestionsOut(based_on_options=winning_labels, suggestions=suggestions[:3])
