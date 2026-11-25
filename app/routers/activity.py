"""An-/Abmeldungen fuer den Admin: Protokoll, ungelesene Aenderungen,
Uebersicht wer bei einem Spiel/Termin kommt."""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app import models
from app.database import get_db
from app.deps import get_current_user, require_admin
from app.services.agenda import ref_start, ref_title, team_members

router = APIRouter(tags=["an-abmeldungen"])

LABEL = {
    "kann_spielen": ("✅", "anwesend"), "verletzt": ("🟠", "verletzt"), "abwesend": ("🔴", "abwesend"),
    "zusage": ("✅", "anwesend"), "absage": ("❌", "abwesend"), "individuell": ("🏃", "trainiert individuell"),
    "unbekannt": ("⏳", "noch offen"),
}
POSITIVE = {"kann_spielen", "zusage"}


def log_response(db: Session, user_id: int, ref_kind: str, ref_id: int, status: str,
                 previous: Optional[str] = None, reason: Optional[str] = None) -> None:
    if previous == status and not reason:
        return
    db.add(models.ResponseLog(user_id=user_id, ref_kind=ref_kind, ref_id=ref_id, status=status,
                              previous=previous, reason=reason))
    if status in ("absage", "abwesend", "verletzt") and previous != status:
        try:
            start = ref_start(db, ref_kind, ref_id)
            from app.services.agenda import now_local
            if start and start > now_local():
                from app.services import webpush
                u = db.get(models.User, user_id)
                icon, text = LABEL.get(status, ("•", status))
                webpush.notify_admins(db, f"{icon} {u.name if u else '?'}: {text}",
                                      (ref_title(db, ref_kind, ref_id) or "") + (f" – „{reason}“" if reason else ""),
                                      f"/?open={ref_kind}:{ref_id}", "abmeldungen", f"abm-{ref_kind}-{ref_id}-{user_id}")
        except Exception:  # noqa: BLE001
            pass


def unread_count(db: Session) -> int:
    return db.query(models.ResponseLog).filter(models.ResponseLog.is_read == False).count()  # noqa: E712


def _out(db: Session, e: models.ResponseLog, titles: dict) -> dict:
    key = (e.ref_kind, e.ref_id)
    if key not in titles:
        titles[key] = (ref_title(db, e.ref_kind, e.ref_id), ref_start(db, e.ref_kind, e.ref_id))
    icon, text = LABEL.get(e.status, ("•", e.status))
    return {
        "id": e.id, "name": e.user.name if e.user else "?", "ref_kind": e.ref_kind, "ref_id": e.ref_id,
        "title": titles[key][0] or "(gelöscht)", "start_time": titles[key][1],
        "status": e.status, "icon": icon, "text": text, "positive": e.status in POSITIVE,
        "previous": LABEL.get(e.previous, (None, None))[1] if e.previous else None,
        "reason": e.reason, "is_read": e.is_read, "created_at": e.created_at,
    }


@router.get("/activity")
def activity(ref_kind: Optional[str] = None, ref_id: Optional[int] = None, unread_only: bool = False,
             limit: int = 50, db: Session = Depends(get_db), admin=Depends(require_admin)):
    q = db.query(models.ResponseLog)
    if ref_kind and ref_id:
        q = q.filter(models.ResponseLog.ref_kind == ref_kind, models.ResponseLog.ref_id == ref_id)
    if unread_only:
        q = q.filter(models.ResponseLog.is_read == False)  # noqa: E712
    rows = q.order_by(models.ResponseLog.created_at.desc()).limit(min(limit, 300)).all()
    titles = {}
    return {"unread": unread_count(db), "items": [_out(db, e, titles) for e in rows]}


@router.post("/activity/read-all")
def read_all(db: Session = Depends(get_db), admin=Depends(require_admin)):
    db.query(models.ResponseLog).filter(models.ResponseLog.is_read == False).update({"is_read": True})  # noqa: E712
    db.commit()
    return {"ok": True}


@router.get("/attendance/{ref_kind}/{ref_id}")
def attendance(ref_kind: str, ref_id: int, db: Session = Depends(get_db), admin=Depends(require_admin)):
    """Wer kommt? Alle aktiven Spieler mit ihrem aktuellen Status."""
    if ref_kind == "game":
        if not db.get(models.Game, ref_id):
            raise HTTPException(404, "Spiel nicht gefunden")
        cur = {a.user_id: (a.status.value, None, a.set_at) for a in
               db.query(models.Availability).filter(models.Availability.game_id == ref_id).all()}
    elif ref_kind == "event":
        if not db.get(models.TeamEvent, ref_id):
            raise HTTPException(404, "Termin nicht gefunden")
        cur = {r.user_id: (r.status or "unbekannt", r.reason, r.set_at) for r in
               db.query(models.EventResponse).filter(models.EventResponse.event_id == ref_id).all()}
    else:
        raise HTTPException(400, "ref_kind muss game oder event sein")
    from app.services import attendance_default
    goalies = attendance_default.goalie_ids(db)
    players = []
    for p in team_members(db):
        st, reason, at = cur.get(p.id, ("unbekannt", None, None))
        icon, text = LABEL.get(st, ("⏳", "offen"))
        players.append({"user_id": p.id, "name": p.name, "status": st, "icon": icon, "text": text,
                        "reason": reason, "set_at": at, "is_staff": bool(p.staff), "staff_title": p.staff_title,
                        "is_goalie": p.id in goalies})
    return {"players": players}


@router.get("/attendance-overview")
def attendance_overview(days: int = 21, db: Session = Depends(get_db), admin=Depends(require_admin)):
    """Matrix fuer den Admin: jeder Spieler x jeder kommende Termin -> Status."""
    from datetime import timedelta

    from app.services.agenda import KIND_LABEL, now_local

    now = now_local()
    end = now + timedelta(days=min(days, 60))
    cols = []
    for g in db.query(models.Game).filter(models.Game.start_time >= now - timedelta(hours=3),
                                          models.Game.start_time < end).all():
        st = {a.user_id: a.status.value for a in db.query(models.Availability).filter(models.Availability.game_id == g.id)}
        cols.append({"ref_kind": "game", "id": g.id, "kind": "spiel", "title": g.opponent, "start_time": g.start_time, "st": st})
    for e in db.query(models.TeamEvent).filter(models.TeamEvent.start_time >= now - timedelta(hours=3),
                                               models.TeamEvent.start_time < end,
                                               models.TeamEvent.is_cancelled == False).all():  # noqa: E712
        st = {r.user_id: r.status for r in e.responses if r.status}
        cols.append({"ref_kind": "event", "id": e.id, "kind": e.kind, "title": e.title or KIND_LABEL.get(e.kind, "Termin"),
                     "start_time": e.start_time, "st": st})
    cols.sort(key=lambda c: c["start_time"])
    players = team_members(db)
    rows = []
    for p in players:
        cells = []
        for c in cols:
            s = c["st"].get(p.id, "unbekannt")
            icon, text = LABEL.get(s, ("⏳", "offen"))
            cells.append({"status": s, "icon": icon, "text": text})
        rows.append({"name": p.name, "cells": cells, "is_staff": bool(p.staff), "staff_title": p.staff_title,
                     "offen": sum(1 for x in cells if x["status"] == "unbekannt")})
    return {"columns": [{k: c[k] for k in ("ref_kind", "id", "kind", "title", "start_time")} for c in cols], "rows": rows}


@router.get("/terminplan")
def terminplan(days: int = 28, db: Session = Depends(get_db), user: models.User = Depends(get_current_user)):
    """Terminplan wie bei Gump: Spieler (Zeilen) x kommende Termine (Spalten), farbig.

    Alle sehen die Farben. Gruende sehen nur Trainer/Admin (Spieler sehen nur,
    ob ein Grund angegeben wurde - ausser bei sich selbst)."""
    from datetime import timedelta

    from app.services.agenda import KIND_LABEL, now_local

    see_reasons = user.is_trainer
    from app.services import attendance_default
    attendance_default.fill(db)
    goalies = attendance_default.goalie_ids(db)
    now = now_local()
    end = now + timedelta(days=max(7, min(days, 90)))
    cols = []
    for g in db.query(models.Game).filter(models.Game.start_time >= now - timedelta(hours=3),
                                          models.Game.start_time < end).all():
        st = {a.user_id: (a.status.value, None) for a in
              db.query(models.Availability).filter(models.Availability.game_id == g.id)}
        cols.append({"ref_kind": "game", "id": g.id, "kind": "spiel", "title": g.opponent, "is_home": g.is_home,
                     "start_time": g.start_time, "end_time": None, "location": g.location_name, "st": st})
    for e in db.query(models.TeamEvent).filter(models.TeamEvent.start_time >= now - timedelta(hours=3),
                                               models.TeamEvent.start_time < end).all():
        st = {r.user_id: (r.status, r.reason) for r in e.responses if r.status}
        cols.append({"ref_kind": "event", "id": e.id, "kind": e.kind, "title": e.title or KIND_LABEL.get(e.kind, "Termin"),
                     "is_home": None, "start_time": e.start_time, "end_time": e.end_time, "location": e.location,
                     "is_cancelled": bool(e.is_cancelled), "st": st})
    cols.sort(key=lambda c: c["start_time"])
    members = team_members(db)
    player_ids = {p.id for p in members if not p.staff}
    from app.services.avatars import avatar_urls
    avs = avatar_urls(db, [p.id for p in members])
    rows = []
    for p in members:
        cells = []
        for c in cols:
            s, reason = c["st"].get(p.id, ("unbekannt", None))
            show = see_reasons or p.id == user.id
            cells.append({"status": s or "unbekannt", "has_reason": bool(reason), "reason": reason if show else None})
        rows.append({"user_id": p.id, "name": p.name, "is_staff": bool(p.staff), "staff_title": p.staff_title,
                     "me": p.id == user.id, "cells": cells, "avatar": avs.get(p.id), "is_goalie": p.id in goalies})
    out_cols = []
    for c in cols:
        cnt = {}
        for uid, (s, _) in c["st"].items():
            if uid in player_ids and s:
                cnt[s] = cnt.get(s, 0) + 1
        out_cols.append(dict({k: c.get(k) for k in ("ref_kind", "id", "kind", "title", "is_home", "start_time", "end_time",
                                                "location", "is_cancelled")}, counts=cnt))
    from app.services.attendance_default import enabled as _def_on
    return {"columns": out_cols, "rows": rows, "players_total": len(player_ids), "default_in": _def_on(db),
            "can_edit_roles": bool(user.is_trainer)}


class GoalieIn(BaseModel):
    goalie: Optional[bool] = None     # None = automatisch (handball.ch)


@router.put("/terminplan/goalie/{user_id}")
def set_goalie(user_id: int, body: GoalieIn, db: Session = Depends(get_db), admin=Depends(require_admin)):
    row = db.get(models.PlayerRole, user_id)
    if row is None:
        row = models.PlayerRole(user_id=user_id)
        db.add(row)
    row.goalie = body.goalie
    db.commit()
    from app.services.attendance_default import invalidate
    invalidate()
    return {"ok": True}


class DefaultIn(BaseModel):
    enabled: bool


@router.put("/terminplan/default")
def set_default(body: DefaultIn, db: Session = Depends(get_db), admin=Depends(require_admin)):
    """Standard-Anmeldung (alle gruen) ein/aus - nur Trainer/Admin."""
    from app.services.attendance_default import set_enabled
    set_enabled(db, body.enabled)
    return {"enabled": body.enabled}
