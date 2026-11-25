from datetime import datetime

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app import models, schemas
from app.database import get_db
from app.deps import get_current_user
from app.services.polls import poll_status

router = APIRouter(prefix="/notifications", tags=["benachrichtigungen"])


@router.get("/count", response_model=schemas.NotificationCount)
def notification_count(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """Zaehlt fuer den eingeloggten User: offene Umfragen, an denen er noch
    nicht teilgenommen hat, sowie seine eigenen offenen (unbezahlten) Bussen.
    Wird fuers blinkende Benachrichtigungs-Badge oben links verwendet."""
    now = datetime.utcnow()

    polls_open = 0
    polls = db.query(models.EssenPoll).all()
    for poll in polls:
        if poll_status(poll, now) != "offen":
            continue
        voted = (
            db.query(models.PollVote)
            .filter(models.PollVote.poll_id == poll.id, models.PollVote.user_id == current_user.id)
            .first()
        )
        if not voted:
            polls_open += 1

    fines_open = (
        db.query(models.Fine)
        .filter(models.Fine.user_id == current_user.id, models.Fine.status == models.BussenStatus.OFFEN)
        .count()
    )

    # Spielberichte, die ich schreiben muss (Spiel schon gespielt, noch nicht fertig)
    reports_open = (
        db.query(models.GameReport)
        .join(models.Game, models.Game.id == models.GameReport.game_id)
        .filter(
            models.GameReport.writer_id == current_user.id,
            models.GameReport.is_done == False,  # noqa: E712
            models.Game.start_time < now,
        )
        .count()
    )

    # Bussenchef/Admin: offene Beschwerden; Admin: ungelesenes Feedback
    from app.routers.complaints import open_complaint_count
    from app.routers.feedback import unread_count

    is_admin = current_user.is_trainer
    complaints_open = open_complaint_count(db) if (is_admin or current_user.is_bussenchef) else 0
    feedback_unread = unread_count(db) if current_user.base_role == models.BaseRole.ADMIN else 0
    from app.routers.events import open_notice_count
    notices_open = open_notice_count(db, current_user.id)
    from app.routers.activity import unread_count as responses_unread_count
    responses_unread = responses_unread_count(db) if is_admin else 0

    return schemas.NotificationCount(
        polls_open=polls_open, fines_open=fines_open, reports_open=reports_open,
        complaints_open=complaints_open, feedback_unread=feedback_unread, notices_open=notices_open,
        responses_unread=responses_unread,
        total=polls_open + fines_open + reports_open + complaints_open + feedback_unread + notices_open + responses_unread,
    )
