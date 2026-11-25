"""Automatische Bussen: wer sich bis zur Frist nicht an- oder abmeldet, bekommt
automatisch die eingestellte Busse (z. B. "Nicht abgemeldet", CHF 15).

- Schalter ist beim Erstellen AUS. Nur der Admin kann ihn ein-/ausschalten.
- Gilt nur fuer Termine, die NACH dem Einschalten beginnen (nie rueckwirkend).
- Jede Busse wird pro Spieler und Termin hoechstens einmal eingetragen
  (auch wenn sie danach von Hand geloescht wird).
- Gilt auch fuer Trainer (Staff). Abgesagte Termine sind ausgenommen.
"""
import json
import logging
import threading
import time
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from app import models
from app.database import SessionLocal, get_db
from app.deps import get_current_user, require_owner
from app.services.agenda import game_responded_ids, event_responded_ids, now_local, team_members

router = APIRouter(prefix="/auto-fines", tags=["bussen"])
log = logging.getLogger("auto_fines")
KEY = "auto_fines_settings"

DEFAULTS = {
    "enabled": False,
    "amount": None,              # None = Betrag aus dem Katalog ("Nicht abgemeldet")
    "reason": "Nicht abgemeldet",
    "trainings": True,
    "spiele": True,
    "events": True,              # Events + Teamsitzungen
    "hours_before": 0,           # Frist: so viele Stunden vor Beginn
    "enabled_at": None,          # ISO-Zeit (lokal) des Einschaltens
}


def load(db: Session) -> dict:
    row = db.get(models.AppAsset, KEY)
    data = dict(DEFAULTS)
    if row:
        try:
            data.update(json.loads(row.data.decode() if isinstance(row.data, (bytes, bytearray)) else row.data))
        except Exception:  # noqa: BLE001
            pass
    return data


def _save(db: Session, data: dict) -> None:
    row = db.get(models.AppAsset, KEY)
    raw = json.dumps(data).encode()
    if row is None:
        db.add(models.AppAsset(key=KEY, data=raw, mime="application/json"))
    else:
        row.data = raw
        row.updated_at = datetime.utcnow()
    db.commit()


def catalog_amount(db: Session, reason: str) -> Optional[float]:
    t = (db.query(models.FineType)
         .filter(func.lower(models.FineType.name) == (reason or "").strip().lower(),
                 models.FineType.is_active == True).first())  # noqa: E712
    return float(t.amount) if t else None


def effective_amount(db: Session, s: dict) -> float:
    if s.get("amount") not in (None, ""):
        return float(s["amount"])
    return catalog_amount(db, s.get("reason")) or 15.0


class SettingsIn(BaseModel):
    enabled: bool
    amount: Optional[float] = None
    reason: Optional[str] = None
    trainings: bool = True
    spiele: bool = True
    events: bool = True
    hours_before: int = 0


@router.get("/status")
def status(db: Session = Depends(get_db), user=Depends(get_current_user)):
    """Fuer alle: ist die automatische Busse aktiv? (Hinweis in der App)"""
    s = load(db)
    return {"enabled": bool(s.get("enabled")), "amount": effective_amount(db, s), "reason": s.get("reason"),
            "hours_before": s.get("hours_before") or 0}


@router.get("/settings")
def get_settings(db: Session = Depends(get_db), owner=Depends(require_owner)):
    s = load(db)
    count = db.query(models.PushSent).filter(models.PushSent.key.like("autofine:%")).count()
    return {**s, "effective_amount": effective_amount(db, s), "catalog_amount": catalog_amount(db, s.get("reason")),
            "fines_so_far": count}


@router.put("/settings")
def put_settings(body: SettingsIn, db: Session = Depends(get_db), owner=Depends(require_owner)):
    s = load(db)
    was = bool(s.get("enabled"))
    s.update(body.model_dump())
    s["reason"] = (body.reason or "Nicht abgemeldet").strip()[:80]
    s["hours_before"] = max(0, min(72, int(body.hours_before or 0)))
    if body.amount is not None and body.amount <= 0:
        s["amount"] = None
    if body.enabled and not was:
        s["enabled_at"] = now_local().isoformat(timespec="minutes")   # ab jetzt, nie rueckwirkend
    _save(db, s)
    return get_settings(db, owner)


@router.post("/run")
def run_now(db: Session = Depends(get_db), owner=Depends(require_owner)):
    return run_once(db)


# ---------------------------------------------------------------------------
def run_once(db: Session) -> dict:
    s = load(db)
    out = {"enabled": bool(s.get("enabled")), "added": 0}
    if not s.get("enabled") or not s.get("enabled_at"):
        return out
    enabled_at = datetime.fromisoformat(s["enabled_at"])
    now = now_local()
    deadline_offset = timedelta(hours=int(s.get("hours_before") or 0))
    # Termine, deren Frist abgelaufen ist (hoechstens 7 Tage zurueck), und die nach dem Einschalten beginnen
    lo = max(enabled_at, now - timedelta(days=7))
    hi = now + deadline_offset
    refs = []
    if s.get("spiele"):
        for g in db.query(models.Game).filter(models.Game.start_time >= lo, models.Game.start_time <= hi).all():
            refs.append(("game", g.id, g.start_time, f"Spiel gegen {g.opponent}"))
    kinds = [k for k, on in (("training", s.get("trainings")), ("event", s.get("events")), ("sitzung", s.get("events"))) if on]
    if kinds:
        from app.routers.events import _event_label
        for e in (db.query(models.TeamEvent)
                  .filter(models.TeamEvent.start_time >= lo, models.TeamEvent.start_time <= hi,
                          models.TeamEvent.is_cancelled == False, models.TeamEvent.kind.in_(kinds)).all()):  # noqa: E712
            refs.append(("event", e.id, e.start_time, _event_label(e)))
    if not refs:
        return out

    amount = effective_amount(db, s)
    reason = s.get("reason") or "Nicht abgemeldet"
    owner = db.query(models.User).filter(models.User.base_role == models.BaseRole.ADMIN).first()
    players = team_members(db)   # Trainer zahlen auch
    from app.services import webpush
    for kind, rid, start, title in refs:
        responded = game_responded_ids(db, rid) if kind == "game" else event_responded_ids(db, rid)
        for p in players:
            if p.id in responded:
                continue
            key = f"autofine:{kind}:{rid}:{p.id}"
            if db.get(models.PushSent, key):
                continue
            db.add(models.PushSent(key=key))
            db.add(models.Fine(user_id=p.id, reason=f"{reason} – {title} ({start:%d.%m.})", amount=amount,
                               date=start, created_by_id=owner.id if owner else p.id))
            db.commit()
            out["added"] += 1
            webpush.notify([p.id], f"💰 Automatische Busse: CHF {amount:.2f}",
                           f"{reason}: {title} am {start:%d.%m. %H:%M}. Du kannst sie unter Mehr → Meine Bussen anfechten.",
                           "/?open=tile:einstellungen", "bussen", f"autofine-{kind}-{rid}")
    return out


_started = {"v": False}


def background() -> None:
    if _started["v"]:
        return
    _started["v"] = True

    def loop():
        time.sleep(45)
        while True:
            db = SessionLocal()
            try:
                run_once(db)
            except Exception as e:  # noqa: BLE001
                log.warning("auto fines: %s", e)
            finally:
                db.close()
            time.sleep(600)

    threading.Thread(target=loop, daemon=True).start()
