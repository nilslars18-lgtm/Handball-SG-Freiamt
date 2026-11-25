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

    # Bussenchef/Admin: offene Beschwerden, die sie bearbeiten muessen
    # (eigene Beschwerden zaehlen nicht - die kann man nicht selbst entscheiden,
    # ausser als Admin)
    complaints_open = 0
    is_admin = current_user.base_role == models.BaseRole.ADMIN
    if is_admin or current_user.is_bussenchef:
        q = db.query(models.Complaint).filter(
            models.Complaint.status == models.ComplaintStatus.OFFEN
        )
        if not is_admin:
            q = q.filter(models.Complaint.user_id != current_user.id)
        complaints_open = q.count()

    return schemas.NotificationCount(
        polls_open=polls_open,
        fines_open=fines_open,
        complaints_open=complaints_open,
        total=polls_open + fines_open + complaints_open,
    )
