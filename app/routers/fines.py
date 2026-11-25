from typing import List

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app import models, schemas
from app.database import get_db
from app.deps import get_current_user, require_bussenchef, require_bussenchef_or_harzchef, require_harzchef

router = APIRouter(tags=["bussen-und-harz"])


def _totals(db: Session) -> tuple[float, float, float]:
    fines = db.query(models.Fine).all()
    total_offen = sum(f.amount for f in fines if f.status == models.BussenStatus.OFFEN)
    total_bezahlt = sum(f.amount for f in fines if f.status == models.BussenStatus.BEZAHLT)
    total_harz = sum(h.amount for h in db.query(models.HarzExpense).all())
    kassenstand = total_bezahlt - total_harz
    return total_offen, total_bezahlt, kassenstand


def _pending_complaint_for(db: Session, fine_id: int):
    """Offene Beschwerde, die diese Busse betrifft (als angefochtene Busse oder
    als Beschwerdegebuehr) - solange sie offen ist, darf nichts bezahlt werden."""
    return (
        db.query(models.Complaint)
        .filter(
            models.Complaint.status == models.ComplaintStatus.OFFEN,
            (models.Complaint.fine_id == fine_id) | (models.Complaint.fee_fine_id == fine_id),
        )
        .first()
    )


def _fine_outs_with_complaints(db: Session, fines: list, player_names: dict) -> list:
    """FineOut inkl. Beschwerde-Infos (is_complaint_fee, complaint_status)."""
    ids = [f.id for f in fines]
    by_fine: dict[int, models.Complaint] = {}
    fee_ids: set[int] = set()
    if ids:
        for c in db.query(models.Complaint).filter(
            (models.Complaint.fine_id.in_(ids)) | (models.Complaint.fee_fine_id.in_(ids))
        ):
            if c.fine_id is not None:
                by_fine[c.fine_id] = c
            if c.fee_fine_id is not None:
                fee_ids.add(c.fee_fine_id)
    out = []
    for f in fines:
        c = by_fine.get(f.id)
        out.append(
            schemas.FineOut(
                id=f.id,
                user_id=f.user_id,
                player_name=player_names[f.user_id],
                reason=f.reason,
                amount=f.amount,
                date=f.date,
                status=f.status.value,
                is_complaint_fee=f.id in fee_ids,
                complaint_id=c.id if c else None,
                complaint_status=c.status.value if c else None,
            )
        )
    return out


# ---------------------------------------------------------------------------
# Feature 6: Bussenliste
# ---------------------------------------------------------------------------
@router.post("/fines", response_model=schemas.FineOut)
def create_fine(
    body: schemas.FineCreate,
    db: Session = Depends(get_db),
    bussenchef: models.User = Depends(require_bussenchef),
):
    player = db.query(models.User).filter(models.User.id == body.user_id).first()
    if not player:
        raise HTTPException(status_code=404, detail="Spieler nicht gefunden")

    fine = models.Fine(
        user_id=body.user_id,
        reason=body.reason,
        amount=body.amount,
        created_by_id=bussenchef.id,
    )
    db.add(fine)
    db.commit()
    db.refresh(fine)
    return schemas.FineOut(
        id=fine.id,
        user_id=fine.user_id,
        player_name=player.name,
        reason=fine.reason,
        amount=fine.amount,
        date=fine.date,
        status=fine.status.value,
    )


@router.patch("/fines/{fine_id}/pay", response_model=schemas.FineOut)
def mark_fine_paid(
    fine_id: int,
    db: Session = Depends(get_db),
    bussenchef: models.User = Depends(require_bussenchef),
):
    fine = db.query(models.Fine).filter(models.Fine.id == fine_id).first()
    if not fine:
        raise HTTPException(status_code=404, detail="Busse nicht gefunden")
    if _pending_complaint_for(db, fine.id):
        raise HTTPException(
            status_code=400,
            detail="Zu dieser Busse gibt es eine offene Beschwerde - bitte zuerst die Beschwerde entscheiden",
        )
    fine.status = models.BussenStatus.BEZAHLT
    db.commit()
    db.refresh(fine)
    return schemas.FineOut(
        id=fine.id,
        user_id=fine.user_id,
        player_name=fine.player.name,
        reason=fine.reason,
        amount=fine.amount,
        date=fine.date,
        status=fine.status.value,
    )


@router.patch("/fines/{fine_id}", response_model=schemas.FineOut)
def update_fine(
    fine_id: int,
    body: schemas.FineUpdate,
    db: Session = Depends(get_db),
    bussenchef: models.User = Depends(require_bussenchef),
):
    """Bussenchef (und Admin) koennen Grund und/oder Betrag einer bestehenden
    Busse nachtraeglich anpassen."""
    fine = db.query(models.Fine).filter(models.Fine.id == fine_id).first()
    if not fine:
        raise HTTPException(status_code=404, detail="Busse nicht gefunden")
    if body.reason is not None:
        fine.reason = body.reason
    if body.amount is not None:
        fine.amount = body.amount
    db.commit()
    db.refresh(fine)
    return schemas.FineOut(
        id=fine.id,
        user_id=fine.user_id,
        player_name=fine.player.name,
        reason=fine.reason,
        amount=fine.amount,
        date=fine.date,
        status=fine.status.value,
    )


@router.get("/fines/me", response_model=List[schemas.FineOut])
def my_fines(
    db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)
):
    fines = (
        db.query(models.Fine)
        .filter(models.Fine.user_id == current_user.id)
        .order_by(models.Fine.date.desc())
        .all()
    )
    return _fine_outs_with_complaints(db, fines, {current_user.id: current_user.name})


@router.get("/fines", response_model=List[schemas.FineOut])
def list_all_fines(
    db: Session = Depends(get_db),
    bussenchef: models.User = Depends(require_bussenchef),
):
    fines = db.query(models.Fine).all()
    return [
        schemas.FineOut(
            id=f.id,
            user_id=f.user_id,
            player_name=f.player.name,
            reason=f.reason,
            amount=f.amount,
            date=f.date,
            status=f.status.value,
        )
        for f in fines
    ]


@router.get("/fines/overview", response_model=schemas.FineOverviewOut)
def fines_overview(
    db: Session = Depends(get_db),
    user: models.User = Depends(require_bussenchef_or_harzchef),
):
    total_offen, total_bezahlt, kassenstand = _totals(db)
    return schemas.FineOverviewOut(
        total_offen=total_offen, total_bezahlt=total_bezahlt, kassenstand=kassenstand
    )


@router.get("/fines/by-player", response_model=List[schemas.PlayerFineSummary])
def fines_by_player(
    db: Session = Depends(get_db),
    bussenchef: models.User = Depends(require_bussenchef),
):
    """Uebersicht pro Spieler: offener und insgesamt bezahlter Betrag.
    Zeigt auch Spieler ohne jede Busse (mit 0.0/0.0)."""
    players = db.query(models.User).filter(models.User.is_active == True).all()  # noqa: E712
    fines = db.query(models.Fine).all()

    offen_by_user: dict[int, float] = {}
    bezahlt_by_user: dict[int, float] = {}
    for f in fines:
        if f.status == models.BussenStatus.OFFEN:
            offen_by_user[f.user_id] = offen_by_user.get(f.user_id, 0.0) + f.amount
        else:
            bezahlt_by_user[f.user_id] = bezahlt_by_user.get(f.user_id, 0.0) + f.amount

    return [
        schemas.PlayerFineSummary(
            user_id=p.id,
            name=p.name,
            total_offen=offen_by_user.get(p.id, 0.0),
            total_bezahlt=bezahlt_by_user.get(p.id, 0.0),
        )
        for p in players
    ]


@router.get("/fines/player/{user_id}", response_model=List[schemas.FineOut])
def fines_for_player(
    user_id: int,
    db: Session = Depends(get_db),
    bussenchef: models.User = Depends(require_bussenchef),
):
    """Alle Bussen (offen und bezahlt) eines einzelnen Spielers - fuer die
    Detailansicht beim Draufklicken in der Bussenliste."""
    player = db.query(models.User).filter(models.User.id == user_id).first()
    if not player:
        raise HTTPException(status_code=404, detail="Spieler nicht gefunden")
    fines = (
        db.query(models.Fine)
        .filter(models.Fine.user_id == user_id)
        .order_by(models.Fine.date.desc())
        .all()
    )
    return _fine_outs_with_complaints(db, fines, {player.id: player.name})


# ---------------------------------------------------------------------------
# Feature 7: Harz-Ausgaben (gleicher Topf wie Bussenkasse)
# ---------------------------------------------------------------------------
@router.post("/harz-expenses", response_model=schemas.HarzExpenseOut)
def create_harz_expense(
    body: schemas.HarzExpenseCreate,
    db: Session = Depends(get_db),
    harzchef: models.User = Depends(require_harzchef),
):
    expense = models.HarzExpense(amount=body.amount, note=body.note, created_by_id=harzchef.id)
    db.add(expense)
    db.commit()
    db.refresh(expense)
    return schemas.HarzExpenseOut(
        id=expense.id,
        amount=expense.amount,
        date=expense.date,
        note=expense.note,
        created_by_name=harzchef.name,
    )


@router.get("/harz-expenses", response_model=List[schemas.HarzExpenseOut])
def list_harz_expenses(
    db: Session = Depends(get_db),
    harzchef: models.User = Depends(require_harzchef),
):
    expenses = db.query(models.HarzExpense).order_by(models.HarzExpense.date.desc()).all()
    return [
        schemas.HarzExpenseOut(
            id=e.id,
            amount=e.amount,
            date=e.date,
            note=e.note,
            created_by_name=e.created_by.name,
        )
        for e in expenses
    ]
