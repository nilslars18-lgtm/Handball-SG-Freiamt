from datetime import datetime
from typing import List

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app import models, schemas
from app.config import settings
from app.database import get_db
from app.deps import get_current_user, require_bussenchef

router = APIRouter(prefix="/complaints", tags=["beschwerden"])

MAX_MESSAGE_LEN = 2000


def _out(c: models.Complaint) -> schemas.ComplaintOut:
    # Solange die Busse noch existiert, die aktuellen Werte zeigen (der
    # Bussenchef kann den Betrag zwischendurch angepasst haben).
    reason = c.fine.reason if c.fine else c.fine_reason
    amount = c.fine.amount if c.fine else c.fine_amount
    return schemas.ComplaintOut(
        id=c.id,
        user_id=c.user_id,
        player_name=c.player.name,
        fine_id=c.fine_id,
        fine_reason=reason,
        fine_amount=amount,
        fine_date=c.fine_date,
        fee_amount=c.fee_amount,
        message=c.message,
        status=c.status.value,
        decision_comment=c.decision_comment,
        decided_by_name=c.decided_by.name if c.decided_by else None,
        created_at=c.created_at,
        decided_at=c.decided_at,
    )


def _get_complaint(db: Session, complaint_id: int) -> models.Complaint:
    c = db.query(models.Complaint).filter(models.Complaint.id == complaint_id).first()
    if not c:
        raise HTTPException(status_code=404, detail="Beschwerde nicht gefunden")
    return c


@router.post("", response_model=schemas.ComplaintOut)
def create_complaint(
    body: schemas.ComplaintCreate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """Spieler fechtet eine EIGENE offene Busse an. Die Beschwerdegebuehr wird
    sofort als zusaetzliche offene Busse aufs Konto gebucht."""
    message = (body.message or "").strip()
    if not message:
        raise HTTPException(status_code=400, detail="Bitte eine Begruendung eingeben")
    if len(message) > MAX_MESSAGE_LEN:
        raise HTTPException(status_code=400, detail="Begruendung ist zu lang")

    # Fremde Bussen werden wie "nicht vorhanden" behandelt (kein Rueckschluss moeglich)
    fine = (
        db.query(models.Fine)
        .filter(models.Fine.id == body.fine_id, models.Fine.user_id == current_user.id)
        .first()
    )
    if not fine:
        raise HTTPException(status_code=404, detail="Busse nicht gefunden")
    if fine.status != models.BussenStatus.OFFEN:
        raise HTTPException(status_code=400, detail="Nur offene Bussen koennen angefochten werden")

    is_fee = (
        db.query(models.Complaint).filter(models.Complaint.fee_fine_id == fine.id).first()
    )
    if is_fee:
        raise HTTPException(status_code=400, detail="Eine Beschwerdegebuehr kann nicht angefochten werden")
    existing = db.query(models.Complaint).filter(models.Complaint.fine_id == fine.id).first()
    if existing:
        raise HTTPException(status_code=400, detail="Zu dieser Busse gibt es bereits eine Beschwerde")

    fee = models.Fine(
        user_id=current_user.id,
        reason=f"Beschwerdegebühr ({fine.reason})"[:200],
        amount=settings.complaint_fee,
        status=models.BussenStatus.OFFEN,
        created_by_id=current_user.id,
    )
    db.add(fee)
    db.flush()  # fee.id

    complaint = models.Complaint(
        user_id=current_user.id,
        fine_id=fine.id,
        fee_fine_id=fee.id,
        fine_reason=fine.reason,
        fine_amount=fine.amount,
        fine_date=fine.date,
        fee_amount=settings.complaint_fee,
        message=message,
    )
    db.add(complaint)
    db.commit()
    db.refresh(complaint)
    return _out(complaint)


@router.get("/me", response_model=List[schemas.ComplaintOut])
def my_complaints(
    db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)
):
    """Nur die eigenen Beschwerden."""
    items = (
        db.query(models.Complaint)
        .filter(models.Complaint.user_id == current_user.id)
        .order_by(models.Complaint.created_at.desc())
        .all()
    )
    return [_out(c) for c in items]


@router.get("", response_model=List[schemas.ComplaintOut])
def all_complaints(
    db: Session = Depends(get_db), chef: models.User = Depends(require_bussenchef)
):
    """Alle Beschwerden - nur Bussenchef/Admin. Offene zuerst."""
    items = db.query(models.Complaint).order_by(models.Complaint.created_at.desc()).all()
    items.sort(key=lambda c: 0 if c.status == models.ComplaintStatus.OFFEN else 1)
    return [_out(c) for c in items]


@router.post("/{complaint_id}/decision", response_model=schemas.ComplaintOut)
def decide_complaint(
    complaint_id: int,
    body: schemas.ComplaintDecision,
    db: Session = Depends(get_db),
    chef: models.User = Depends(require_bussenchef),
):
    c = _get_complaint(db, complaint_id)
    if c.status != models.ComplaintStatus.OFFEN:
        raise HTTPException(status_code=400, detail="Diese Beschwerde wurde bereits entschieden")
    # Wer selbst eine Beschwerde einreicht, darf sie nicht selbst entscheiden
    # (ausser Admin) - sonst waere die Gebuehr umgehbar.
    if c.user_id == chef.id and chef.base_role != models.BaseRole.ADMIN:
        raise HTTPException(
            status_code=403, detail="Eigene Beschwerden kann nur der Admin entscheiden"
        )
    if body.decision not in ("annehmen", "ablehnen"):
        raise HTTPException(status_code=400, detail="decision muss 'annehmen' oder 'ablehnen' sein")

    comment = (body.comment or "").strip() or None
    if comment and len(comment) > MAX_MESSAGE_LEN:
        raise HTTPException(status_code=400, detail="Kommentar ist zu lang")

    now = datetime.utcnow()
    c.decision_comment = comment
    c.decided_by_id = chef.id
    c.decided_at = now

    if body.decision == "ablehnen":
        # Busse und Beschwerdegebuehr bleiben bestehen
        c.status = models.ComplaintStatus.ABGELEHNT
    else:
        c.status = models.ComplaintStatus.ANGENOMMEN
        fine, fee = c.fine, c.fee_fine
        if fine is not None:  # aktuelle Werte als Momentaufnahme festhalten
            c.fine_reason, c.fine_amount, c.fine_date = fine.reason, fine.amount, fine.date
        # Verweise zuerst loesen, dann Busse + Gebuehr loeschen
        c.fine_id = None
        c.fee_fine_id = None
        db.flush()
        if fine is not None:
            db.delete(fine)
        if fee is not None:
            db.delete(fee)

    db.commit()
    db.refresh(c)
    return _out(c)


@router.put("/{complaint_id}/comment", response_model=schemas.ComplaintOut)
def update_comment(
    complaint_id: int,
    body: schemas.ComplaintComment,
    db: Session = Depends(get_db),
    chef: models.User = Depends(require_bussenchef),
):
    """Bussenchef/Admin kann jederzeit (auch nach der Entscheidung) den Kommentar ergaenzen."""
    c = _get_complaint(db, complaint_id)
    comment = (body.comment or "").strip()
    if len(comment) > MAX_MESSAGE_LEN:
        raise HTTPException(status_code=400, detail="Kommentar ist zu lang")
    c.decision_comment = comment or None
    db.commit()
    db.refresh(c)
    return _out(c)
