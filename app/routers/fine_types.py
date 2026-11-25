"""Bussenkatalog: vordefinierte Bussen mit Betrag. Ansehen: alle.
Bearbeiten: Bussenchef/Admin. Dazu ein einmaliger Import (app/seed/bussen.json)."""
import json
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from app import models
from app.database import SessionLocal, get_db
from app.deps import get_current_user, require_bussenchef

router = APIRouter(prefix="/fine-types", tags=["bussenkatalog"])

SEED_FILE = Path(__file__).resolve().parent.parent / "seed" / "bussen.json"


class FineTypeIn(BaseModel):
    name: str
    amount: float
    category: Optional[str] = None
    note: Optional[str] = None


def _out(t: models.FineType) -> dict:
    return {"id": t.id, "name": t.name, "amount": t.amount, "category": t.category, "note": t.note}


@router.get("")
def list_types(db: Session = Depends(get_db), user=Depends(get_current_user)):
    rows = (db.query(models.FineType).filter(models.FineType.is_active == True)  # noqa: E712
            .order_by(models.FineType.sort, models.FineType.category, models.FineType.name).all())
    return [_out(t) for t in rows]


@router.post("")
def create_type(body: FineTypeIn, db: Session = Depends(get_db), user=Depends(require_bussenchef)):
    if not body.name.strip() or body.amount < 0:
        raise HTTPException(400, "Name und Betrag angeben")
    t = models.FineType(name=body.name.strip(), amount=body.amount, category=(body.category or "").strip() or None,
                        note=(body.note or "").strip() or None)
    db.add(t)
    db.commit()
    return _out(t)


@router.patch("/{type_id}")
def update_type(type_id: int, body: FineTypeIn, db: Session = Depends(get_db), user=Depends(require_bussenchef)):
    t = db.get(models.FineType, type_id)
    if not t:
        raise HTTPException(404, "Nicht gefunden")
    t.name, t.amount = body.name.strip() or t.name, body.amount
    t.category = (body.category or "").strip() or None
    t.note = (body.note or "").strip() or None
    db.commit()
    return _out(t)


@router.delete("/{type_id}")
def delete_type(type_id: int, db: Session = Depends(get_db), user=Depends(require_bussenchef)):
    t = db.get(models.FineType, type_id)
    if t:
        t.is_active = False
        db.commit()
    return {"ok": True}


# ---------------------------------------------------------------------------
# Einmaliger Import aus app/seed/bussen.json (Katalog, fehlende Spieler, Bussen)
# Jede "version" wird nur einmal angewendet (Markierung in app_assets).
# ---------------------------------------------------------------------------
def _find_user(db: Session, name: str):
    n = " ".join(name.split()).lower()
    users = db.query(models.User).filter(models.User.is_active == True).all()  # noqa: E712
    for u in users:
        if u.name.strip().lower() == n or u.email.lower() == n:
            return u
    first = [u for u in users if u.name.strip().lower().split(" ")[0] == n.split(" ")[0]]
    if len(first) == 1 and (len(n.split()) == 1 or len(first[0].name.split()) == 1):
        return first[0]
    return None


def apply_seed() -> None:
    if not SEED_FILE.exists():
        return
    data = json.loads(SEED_FILE.read_text(encoding="utf-8"))
    marker = f"seed_bussen_v{data.get('version', 1)}"
    db = SessionLocal()
    try:
        if db.get(models.AppAsset, marker):
            return
        from app.routers.handballch import make_password
        from app.security import hash_password

        for i, t in enumerate(data.get("catalog", [])):
            exists = db.query(models.FineType).filter(func.lower(models.FineType.name) == t["name"].lower(),
                                                      models.FineType.is_active == True).first()  # noqa: E712
            if not exists:
                db.add(models.FineType(name=t["name"], amount=float(t["amount"]), category=t.get("category"),
                                       note=t.get("note"), sort=i))
        admin = db.query(models.User).filter(models.User.base_role == models.BaseRole.ADMIN).first()
        for p in data.get("players", []):
            if _find_user(db, p["name"]):
                continue
            login = p.get("login") or p["name"].split()[0].lower()
            cand, k = login, 2
            while db.query(models.User).filter(func.lower(models.User.email) == cand).first():
                cand, k = f"{login}{k}", k + 1
            pw = make_password()
            db.add(models.User(name=p["name"], email=cand, password_hash=hash_password(pw),
                               last_plaintext_password=pw, base_role=models.BaseRole.PLAYER))
            db.flush()
        for f in data.get("fines", []):
            u = _find_user(db, f["name"])
            if not u:
                continue
            date = datetime.fromisoformat(f["date"]) if f.get("date") else datetime.utcnow()
            db.add(models.Fine(user_id=u.id, reason=f["reason"], amount=float(f["amount"]), date=date,
                               status=models.BussenStatus.BEZAHLT if f.get("paid") else models.BussenStatus.OFFEN,
                               created_by_id=admin.id if admin else u.id))
        db.add(models.AppAsset(key=marker, data=b"1", mime="text/plain"))
        db.commit()
    except Exception as exc:  # Import darf den Start nie verhindern
        db.rollback()
        import logging
        logging.getLogger("uvicorn").warning("Bussen-Import fehlgeschlagen: %s", exc)
    finally:
        db.close()
