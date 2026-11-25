"""Aemtliliste: Trainer legt Aemtli an, die App verteilt sie reihum."""
from datetime import timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app import models
from app.database import get_db
from app.deps import get_current_user, require_admin
from app.services.agenda import KIND_LABEL, active_players, now_local, season_bounds, season_of
from app.services.duty_rotation import LOOKAHEAD_DAYS, sync_duties

router = APIRouter(prefix="/duties", tags=["aemtli"])


class DutyIn(BaseModel):
    name: str
    applies_to: str = "spiel"  # spiel | training | beide


class AssignIn(BaseModel):
    duty_id: int
    ref_kind: str
    ref_id: int
    user_id: Optional[int] = None  # None = wieder automatisch


@router.get("")
def list_duties(db: Session = Depends(get_db), user=Depends(get_current_user)):
    return [{"id": d.id, "name": d.name, "applies_to": d.applies_to}
            for d in db.query(models.Duty).filter(models.Duty.is_active == True).order_by(models.Duty.name).all()]  # noqa: E712


@router.post("")
def create_duty(body: DutyIn, db: Session = Depends(get_db), admin=Depends(require_admin)):
    if body.applies_to not in ("spiel", "training", "beide"):
        raise HTTPException(400, "applies_to muss spiel, training oder beide sein")
    if not body.name.strip():
        raise HTTPException(400, "Name fehlt")
    d = models.Duty(name=body.name.strip(), applies_to=body.applies_to)
    db.add(d)
    db.commit()
    return {"id": d.id}


@router.delete("/{duty_id}")
def delete_duty(duty_id: int, db: Session = Depends(get_db), admin=Depends(require_admin)):
    d = db.get(models.Duty, duty_id)
    if d:
        d.is_active = False
        # kommende Einteilungen entfernen, vergangene bleiben als Historie
        now = now_local()
        for a in db.query(models.DutyAssignment).filter(models.DutyAssignment.duty_id == duty_id).all():
            obj = db.get(models.Game, a.ref_id) if a.ref_kind == "game" else db.get(models.TeamEvent, a.ref_id)
            if obj is None or obj.start_time >= now:
                db.delete(a)
        db.commit()
    return {"ok": True}


@router.get("/plan")
def plan(db: Session = Depends(get_db), user: models.User = Depends(get_current_user)):
    """Kommende Termine mit Aemtli-Einteilung + Zaehler pro Spieler (Saison)."""
    sync_duties(db)
    now = now_local()
    duties = {d.id: d for d in db.query(models.Duty).filter(models.Duty.is_active == True).all()}  # noqa: E712
    rows = db.query(models.DutyAssignment).filter(models.DutyAssignment.duty_id.in_(list(duties) or [-1])).all()
    s_start, s_end = season_bounds(season_of(now))
    by_ref = {}
    counts = {}
    for a in rows:
        obj = db.get(models.Game, a.ref_id) if a.ref_kind == "game" else db.get(models.TeamEvent, a.ref_id)
        if obj is None:
            continue
        if s_start <= obj.start_time < s_end and a.user_id:
            counts.setdefault(a.user_id, {}).setdefault(a.duty_id, 0)
            counts[a.user_id][a.duty_id] += 1
        if obj.start_time < now - timedelta(hours=3) or obj.start_time > now + timedelta(days=LOOKAHEAD_DAYS):
            continue
        key = (a.ref_kind, a.ref_id)
        if key not in by_ref:
            if a.ref_kind == "game":
                title, kind = f"Spiel gegen {obj.opponent}", "spiel"
            else:
                title, kind = obj.title or KIND_LABEL.get(obj.kind, "Termin"), obj.kind
            by_ref[key] = {"ref_kind": a.ref_kind, "ref_id": a.ref_id, "title": title, "kind": kind,
                           "start_time": obj.start_time, "assignments": []}
        by_ref[key]["assignments"].append({
            "duty_id": a.duty_id, "duty": duties[a.duty_id].name, "user_id": a.user_id,
            "user_name": a.user.name if a.user else None, "manual": a.manual, "mine": a.user_id == user.id})
    upcoming = sorted(by_ref.values(), key=lambda x: x["start_time"])
    for u in upcoming:
        u["assignments"].sort(key=lambda x: x["duty"])
    table = []
    for p in active_players(db):
        per = counts.get(p.id, {})
        table.append({"name": p.name, "counts": {duties[d].name: per.get(d, 0) for d in duties},
                      "total": sum(per.values())})
    return {"duties": [{"id": d.id, "name": d.name, "applies_to": d.applies_to} for d in duties.values()],
            "upcoming": upcoming, "table": table}


@router.put("/assign")
def assign(body: AssignIn, db: Session = Depends(get_db), admin=Depends(require_admin)):
    a = db.query(models.DutyAssignment).filter(
        models.DutyAssignment.duty_id == body.duty_id, models.DutyAssignment.ref_kind == body.ref_kind,
        models.DutyAssignment.ref_id == body.ref_id).first()
    if a is None:
        a = models.DutyAssignment(duty_id=body.duty_id, ref_kind=body.ref_kind, ref_id=body.ref_id)
        db.add(a)
    if body.user_id is None:
        a.manual = False
    else:
        a.user_id = body.user_id
        a.manual = True
    db.commit()
    sync_duties(db)
    return {"ok": True}
