from typing import List

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app import models, schemas
from app.database import get_db
from app.deps import get_current_user, require_admin

router = APIRouter(prefix="/feedback", tags=["feedback"])

CATEGORIES = [
    "Allgemein",
    "Spiele & Kalender",
    "Aufgebot",
    "Anfahrt",
    "Essen-Umfrage",
    "Bussen",
    "Harz",
    "Design & Bedienung",
    "Fehler melden",
    "Sonstiges",
]
MAX_MESSAGE_LEN = 3000


def _out(f: models.Feedback) -> schemas.FeedbackOut:
    return schemas.FeedbackOut(
        id=f.id,
        user_id=f.user_id,
        player_name=f.player.name,
        category=f.category,
        message=f.message,
        is_done=bool(f.is_done),
        created_at=f.created_at,
    )


@router.get("/categories", response_model=List[str])
def list_categories(current_user: models.User = Depends(get_current_user)):
    return CATEGORIES


@router.post("")
def create_feedback(
    body: schemas.FeedbackCreate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    if body.category not in CATEGORIES:
        raise HTTPException(status_code=400, detail="Unbekannte Kategorie")
    message = (body.message or "").strip()
    if not message:
        raise HTTPException(status_code=400, detail="Bitte eine Nachricht eingeben")
    if len(message) > MAX_MESSAGE_LEN:
        raise HTTPException(status_code=400, detail="Nachricht ist zu lang")
    db.add(models.Feedback(user_id=current_user.id, category=body.category, message=message))
    db.commit()
    return {"message": "Danke für dein Feedback!"}


@router.get("", response_model=List[schemas.FeedbackOut])
def list_feedback(db: Session = Depends(get_db), admin: models.User = Depends(require_admin)):
    """Nur Admin: alle Rueckmeldungen, offene zuerst, neueste oben."""
    items = db.query(models.Feedback).order_by(models.Feedback.created_at.desc()).all()
    items.sort(key=lambda f: 1 if f.is_done else 0)
    return [_out(f) for f in items]


@router.patch("/{feedback_id}/done", response_model=schemas.FeedbackOut)
def toggle_done(
    feedback_id: int,
    db: Session = Depends(get_db),
    admin: models.User = Depends(require_admin),
):
    f = db.query(models.Feedback).filter(models.Feedback.id == feedback_id).first()
    if not f:
        raise HTTPException(status_code=404, detail="Feedback nicht gefunden")
    f.is_done = not bool(f.is_done)
    db.commit()
    db.refresh(f)
    return _out(f)
