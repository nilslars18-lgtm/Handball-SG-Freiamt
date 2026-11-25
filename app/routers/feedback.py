"""Feedback-Knopf: alle koennen Feedback senden, der Admin wertet es aus."""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app import models
from app.database import get_db
from app.deps import get_current_user, require_owner

router = APIRouter(prefix="/feedback", tags=["feedback"])

CATEGORIES = {
    "fehler": "🐞 Fehler",
    "idee": "💡 Idee / Wunsch",
    "aenderung": "🔧 Änderung",
    "lob": "👍 Lob",
    "sonstiges": "💬 Sonstiges",
    "abschluss": "🎉 Saisonabschluss",  # Vorschlaege fuer den Abschluss
}


class FeedbackIn(BaseModel):
    category: str
    text: str


@router.get("/categories")
def categories(user=Depends(get_current_user)):
    return [{"id": k, "label": v} for k, v in CATEGORIES.items()]


@router.post("")
def send_feedback(body: FeedbackIn, db: Session = Depends(get_db), user: models.User = Depends(get_current_user)):
    if body.category not in CATEGORIES:
        raise HTTPException(400, "Unbekannte Kategorie")
    if not body.text.strip():
        raise HTTPException(400, "Bitte etwas schreiben")
    fb = models.Feedback(user_id=user.id, category=body.category, text=body.text.strip()[:4000])
    db.add(fb)
    db.commit()
    return {"ok": True}


@router.get("/mine")
def my_feedback(category: str = "", db: Session = Depends(get_db), user: models.User = Depends(get_current_user)):
    """Eigene Eintraege (z.B. 'Deine Vorschlaege'). Namen anderer sieht nur der Admin."""
    q = db.query(models.Feedback).filter(models.Feedback.user_id == user.id)
    if category:
        q = q.filter(models.Feedback.category == category)
    return [
        {"id": f.id, "category": f.category, "text": f.text, "created_at": f.created_at}
        for f in q.order_by(models.Feedback.created_at.desc()).all()
    ]


@router.delete("/mine/{feedback_id}")
def delete_my_feedback(feedback_id: int, db: Session = Depends(get_db), user: models.User = Depends(get_current_user)):
    f = db.get(models.Feedback, feedback_id)
    if not f or f.user_id != user.id:
        raise HTTPException(404, "Nicht gefunden")
    db.delete(f)
    db.commit()
    return {"ok": True}


@router.get("")
def list_feedback(db: Session = Depends(get_db), admin=Depends(require_owner)):
    rows = db.query(models.Feedback).order_by(models.Feedback.created_at.desc()).all()
    return [
        {
            "id": f.id,
            "category": f.category,
            "category_label": CATEGORIES.get(f.category, f.category),
            "text": f.text,
            "user_name": f.user.name if f.user else "",
            "is_read": f.is_read,
            "created_at": f.created_at,
        }
        for f in rows
    ]


@router.patch("/{feedback_id}/read")
def mark_read(feedback_id: int, db: Session = Depends(get_db), admin=Depends(require_owner)):
    f = db.get(models.Feedback, feedback_id)
    if not f:
        raise HTTPException(404, "Nicht gefunden")
    f.is_read = not f.is_read
    db.commit()
    return {"ok": True, "is_read": f.is_read}


@router.delete("/{feedback_id}")
def delete_feedback(feedback_id: int, db: Session = Depends(get_db), admin=Depends(require_owner)):
    f = db.get(models.Feedback, feedback_id)
    if f:
        db.delete(f)
        db.commit()
    return {"ok": True}


def unread_count(db: Session) -> int:
    return db.query(models.Feedback).filter(models.Feedback.is_read == False).count()  # noqa: E712
