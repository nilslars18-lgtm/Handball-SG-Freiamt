"""
Feature 4: Essen-Umfrage - Zeitlogik.

Die Umfrage oeffnet automatisch am konfigurierten Wochentag/Uhrzeit
(Standard: Donnerstag 14:00) VOR dem jeweiligen Spiel, und schliesst
automatisch X Minuten vor Spielbeginn (Standard: 30 Minuten).
"""
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from app import models
from app.config import settings


def compute_poll_open_time(game_start: datetime) -> datetime:
    """Findet den Donnerstag (oder konfigurierten Wochentag) 14:00 Uhr
    am oder vor dem Spieltag."""
    days_back = (game_start.weekday() - settings.poll_open_weekday) % 7
    candidate_day = game_start - timedelta(days=days_back)
    return candidate_day.replace(
        hour=settings.poll_open_hour,
        minute=settings.poll_open_minute,
        second=0,
        microsecond=0,
    )


def compute_poll_close_time(game_start: datetime) -> datetime:
    return game_start - timedelta(minutes=settings.poll_close_minutes_before_game)


def create_poll_for_game(db: Session, game: models.Game) -> models.EssenPoll:
    """Legt automatisch eine Essen-Umfrage mit Standardoptionen fuer ein
    neu importiertes Spiel an, falls noch keine existiert."""
    existing = db.query(models.EssenPoll).filter(models.EssenPoll.game_id == game.id).first()
    if existing:
        return existing

    poll = models.EssenPoll(
        game_id=game.id,
        opens_at=compute_poll_open_time(game.start_time),
        closes_at=compute_poll_close_time(game.start_time),
    )
    db.add(poll)
    db.flush()  # damit poll.id verfuegbar ist

    for label in settings.poll_default_options_list:
        db.add(models.PollOption(poll_id=poll.id, label=label))

    return poll


def poll_status(poll: models.EssenPoll, now: datetime) -> str:
    """'bevorstehend' | 'offen' | 'geschlossen'"""
    if poll.is_closed_manually or now >= poll.closes_at:
        return "geschlossen"
    if now >= poll.opens_at:
        return "offen"
    return "bevorstehend"


def results_visible(game: models.Game, now: datetime) -> bool:
    """Auswertung wird erst NACH dem Spiel sichtbar (Feature 4)."""
    end = game.end_time
    if end is None:
        end = game.start_time + timedelta(minutes=settings.game_default_duration_minutes)
    return now >= end


def poll_summary(db: Session, poll: models.EssenPoll) -> dict:
    """Auswertung: Stimmen pro Option, Gewinner (ohne 'komme nicht mit'), wer mitkommt."""
    skip = settings.poll_skip_option.strip().casefold()
    votes = db.query(models.PollVote).filter(models.PollVote.poll_id == poll.id).all()
    label = {o.id: o.label for o in poll.options}
    counts = {}
    for v in votes:
        counts[label.get(v.option_id, "?")] = counts.get(label.get(v.option_id, "?"), 0) + 1
    food = {k: c for k, c in counts.items() if k.strip().casefold() != skip}
    top = max(food.values()) if food else 0
    winners = [k for k, c in food.items() if c == top and top > 0]
    come_ids = {v.user_id for v in votes if label.get(v.option_id, "").strip().casefold() != skip}
    names = sorted(u.name for u in db.query(models.User).filter(models.User.id.in_(come_ids)).all()) if come_ids else []
    voted = {v.user_id for v in votes}
    return {"counts": counts, "winners": winners, "winner_votes": top, "mitkommende": names, "voted_ids": voted}
