"""Bussen-Beschwerden.

- Spieler ficht eine eigene, offene Busse an -> es wird sofort eine
  Beschwerdegebuehr von CHF 5 als neue Busse eingetragen.
- Bussenchef/Admin entscheidet:
    annehmen  -> Busse UND Gebuehr werden geloescht
    ablehnen  -> beides bleibt bestehen
  jeweils mit optionalem Kommentar.
- Sichtbar nur fuer den Beschwerdefuehrer, Bussenchef und Admin.
"""
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app import models
from app.database import get_db
from app.deps import get_current_user, require_bussenchef

router = APIRouter(tags=["beschwerden"])

COMPLAINT_FEE = 5.0


class ComplaintIn(BaseModel):
    reason: str


class DecisionIn(BaseModel):
    accept: bool
    comment: Optional[str] = None


def _out(c: models.FineComplaint, db: Session) -> dict:
    fine = db.get(models.Fine, c.fine_id) if c.fine_id else None
    fee = db.get(models.Fine, c.fee_fine_id) if c.fee_fine_id else None
    return {
        "id": c.id,
        "fine_id": c.fine_id,
        "user_id": c.user_id,
        "player_name": c.user.name if c.user else "",
        "fine_reason": fine.reason if fine else c.fine_reason,
        "fine_amount": fine.amount if fine else c.fine_amount,
        "fine_date": fine.date if fine else None,
        "fee_amount": fee.amount if fee else COMPLAINT_FEE,
        "reason": c.reason,
        "status": c.status,
        "response_comment": c.response_comment,
        "created_at": c.created_at,
        "decided_at": c.decided_at,
        "decided_by_name": c.decided_by.name if c.decided_by else None,
    }


@router.post("/fines/{fine_id}/complaint")
def file_complaint(fine_id: int, body: ComplaintIn, db: Session = Depends(get_db),
                   user: models.User = Depends(get_current_user)):
    fine = db.get(models.Fine, fine_id)
    if not fine or fine.user_id != user.id:
        raise HTTPException(404, "Busse nicht gefunden")
    if fine.status != models.BussenStatus.OFFEN:
        raise HTTPException(400, "Nur offene Bussen können angefochten werden")
    if not body.reason.strip():
        raise HTTPException(400, "Bitte eine Begründung angeben")
    is_fee = db.query(models.FineComplaint).filter(models.FineComplaint.fee_fine_id == fine_id).first()
    if is_fee:
        raise HTTPException(400, "Die Beschwerdegebühr selbst kann nicht angefochten werden")
    existing = db.query(models.FineComplaint).filter(models.FineComplaint.fine_id == fine_id).first()
    if existing:
        raise HTTPException(400, "Gegen diese Busse läuft bereits eine Beschwerde")

    fee = models.Fine(
        user_id=user.id,
        reason=f"Beschwerdegebühr ({fine.reason})",
        amount=COMPLAINT_FEE,
        created_by_id=user.id,
    )
    db.add(fee)
    db.flush()
    c = models.FineComplaint(
        fine_id=fine.id, fee_fine_id=fee.id, user_id=user.id, reason=body.reason.strip(),
        fine_reason=fine.reason, fine_amount=fine.amount,
    )
    db.add(c)
    db.commit()
    return _out(c, db)


@router.get("/complaints/mine")
def my_complaints(db: Session = Depends(get_db), user: models.User = Depends(get_current_user)):
    rows = (
        db.query(models.FineComplaint)
        .filter(models.FineComplaint.user_id == user.id)
        .order_by(models.FineComplaint.created_at.desc())
        .all()
    )
    return [_out(c, db) for c in rows]


@router.get("/complaints")
def all_complaints(db: Session = Depends(get_db), user=Depends(require_bussenchef)):
    rows = db.query(models.FineComplaint).order_by(models.FineComplaint.created_at.desc()).all()
    # offene zuerst
    rows.sort(key=lambda c: 0 if c.status == "offen" else 1)
    return [_out(c, db) for c in rows]


@router.post("/complaints/{complaint_id}/decide")
def decide(complaint_id: int, body: DecisionIn, db: Session = Depends(get_db),
           user: models.User = Depends(require_bussenchef)):
    c = db.get(models.FineComplaint, complaint_id)
    if not c:
        raise HTTPException(404, "Beschwerde nicht gefunden")
    if c.status != "offen":
        raise HTTPException(400, "Diese Beschwerde wurde bereits entschieden")
    c.response_comment = (body.comment or "").strip() or None
    c.decided_at = datetime.utcnow()
    c.decided_by_id = user.id
    if body.accept:
        c.status = "angenommen"
        ids = [i for i in (c.fine_id, c.fee_fine_id) if i]
        c.fine_id = None
        c.fee_fine_id = None
        db.flush()
        for fid in ids:
            f = db.get(models.Fine, fid)
            if f:
                db.delete(f)
    else:
        c.status = "abgelehnt"
    db.commit()
    from app.services import webpush
    webpush.notify([c.user_id], "⚖️ Beschwerde " + ("angenommen ✅" if body.accept else "abgelehnt ❌"),
                   ("Busse und Gebühr wurden gelöscht." if body.accept else "Busse und Gebühr bleiben.")
                   + (f" Kommentar: {c.response_comment}" if c.response_comment else ""),
                   "/?open=tile:einstellungen", "bussen", f"beschwerde-{c.id}")
    return _out(c, db)


def open_complaint_count(db: Session) -> int:
    return db.query(models.FineComplaint).filter(models.FineComplaint.status == "offen").count()


def complaint_fine_ids(db: Session, user_id: int) -> dict:
    """fine_id -> status, sowie die IDs der Gebuehren (fuer 'Meine Bussen')."""
    rows = db.query(models.FineComplaint).filter(models.FineComplaint.user_id == user_id).all()
    return {
        "complained": {c.fine_id: c.status for c in rows if c.fine_id},
        "fees": [c.fee_fine_id for c in rows if c.fee_fine_id],
    }


@router.get("/complaints/my-fine-flags")
def my_fine_flags(db: Session = Depends(get_db), user: models.User = Depends(get_current_user)):
    return complaint_fine_ids(db, user.id)
