"""Termine: Trainings (Standard = jede Woche, Spezial = Einzeldatum), Events,
Teamsitzungen. Dazu: Zu-/Absage, Praesenz, Gesamt-Agenda inkl. Spiele,
Erinnerungen an Spieler ohne Antwort und In-App-Hinweise."""
from datetime import datetime, timedelta
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app import models
from app.database import get_db
from app.deps import get_current_user, require_admin
from app.routers.games import _aufgebot_status_for
from app.services.agenda import (
    KIND_LABEL, active_players, team_members, daterange_weekdays, event_responded_ids, game_responded_ids,
    next_season_end, now_local, ref_start, ref_title,
)
from app.services.duty_rotation import sync_duties

router = APIRouter(tags=["termine"])

KINDS = ("training", "event", "sitzung")
RESPONSE_STATUSES = ("zusage", "absage", "verletzt", "individuell")


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------
class SeriesIn(BaseModel):
    weekdays: List[int]            # 0=Mo ... 6=So
    start_hhmm: str                # "19:00"
    duration_minutes: int = 90
    location: Optional[str] = None
    valid_from: Optional[str] = None   # "2026-10-20"
    valid_until: Optional[str] = None  # "2027-06-30"


class EventIn(BaseModel):
    kind: str = "training"
    title: Optional[str] = None
    date: str                      # "2026-10-24"
    start_hhmm: str
    duration_minutes: int = 90
    location: Optional[str] = None
    note: Optional[str] = None


class EventPatch(BaseModel):
    title: Optional[str] = None
    location: Optional[str] = None
    note: Optional[str] = None
    is_cancelled: Optional[bool] = None
    date: Optional[str] = None
    start_hhmm: Optional[str] = None
    duration_minutes: Optional[int] = None


class ResponseIn(BaseModel):
    status: str                    # "zusage" | "absage" | "verletzt" | "individuell"
    reason: Optional[str] = None


class PresenceIn(BaseModel):
    user_id: int
    present: Optional[bool] = None


def _parse_hhmm(s: str):
    try:
        h, m = s.strip().split(":")
        h, m = int(h), int(m)
        assert 0 <= h < 24 and 0 <= m < 60
        return h, m
    except Exception:
        raise HTTPException(400, "Zeit bitte als HH:MM angeben, z.B. 19:00")


def _parse_date(s: str) -> datetime:
    try:
        return datetime.strptime(s.strip()[:10], "%Y-%m-%d")
    except Exception:
        raise HTTPException(400, "Datum bitte als JJJJ-MM-TT angeben")


def _event_label(e: models.TeamEvent) -> str:
    return e.title or KIND_LABEL.get(e.kind, "Termin")


def _game_counts(db: Session, game_id: int, n_players: int) -> dict:
    c = {"kann_spielen": 0, "verletzt": 0, "abwesend": 0}
    pids = {p.id for p in active_players(db)}
    for a in db.query(models.Availability).filter(models.Availability.game_id == game_id).all():
        if a.user_id in pids and a.status.value in c:
            c[a.status.value] += 1
    c["offen"] = max(0, n_players - sum(c.values()))
    return c


# ---------------------------------------------------------------------------
# Agenda: Spiele + Termine zusammen (Startseite, Liste, Kalender)
# ---------------------------------------------------------------------------
@router.get("/agenda")
def agenda(days_ahead: int = 21, days_back: int = 0, all: bool = False,
           db: Session = Depends(get_db), user: models.User = Depends(get_current_user)):
    sync_duties(db)
    from app.services import attendance_default
    attendance_default.fill(db)
    now = now_local()
    if all:
        start, end = datetime(2000, 1, 1), datetime(2100, 1, 1)
    else:
        start = now - timedelta(days=days_back, hours=3)
        end = now + timedelta(days=days_ahead)

    duty_rows = (
        db.query(models.DutyAssignment)
        .join(models.Duty, models.Duty.id == models.DutyAssignment.duty_id)
        .filter(models.Duty.is_active == True)  # noqa: E712
        .all()
    )
    duties = {}
    for a in duty_rows:
        duties.setdefault((a.ref_kind, a.ref_id), []).append(
            {"duty": a.duty.name, "user_name": a.user.name if a.user else None, "mine": a.user_id == user.id})

    reports = {r.game_id: r for r in db.query(models.GameReport).all()}
    player_ids = {p.id for p in active_players(db)}
    n_players = len(player_ids)
    items = []
    for g in db.query(models.Game).filter(models.Game.start_time >= start, models.Game.start_time < end).all():
        av = db.query(models.Availability).filter(
            models.Availability.game_id == g.id, models.Availability.user_id == user.id).first()
        status, _ = _aufgebot_status_for(db, g, user)
        r = reports.get(g.id)
        result = None
        if r and r.score_home is not None and r.score_away is not None:
            result = {"us": r.score_home, "them": r.score_away}
        items.append({
            "ref_kind": "game", "id": g.id, "kind": "spiel",
            "title": f"{'Heimspiel' if g.is_home else 'Auswärtsspiel' if g.is_home is False else 'Spiel'} gegen {g.opponent}",
            "opponent": g.opponent, "is_home": g.is_home,
            "start_time": g.start_time, "end_time": g.end_time,
            "location": g.location_name, "maps_link": g.maps_link, "is_cancelled": False,
            "my_status": av.status.value if av else "unbekannt",
            "my_aufgebot_status": status, "result": result,
            "i_write_report": bool(r and r.writer_id == user.id and not r.is_done),
            "counts": _game_counts(db, g.id, n_players),
            "duties": duties.get(("game", g.id), []),
        })
    for e in db.query(models.TeamEvent).filter(models.TeamEvent.start_time >= start, models.TeamEvent.start_time < end).all():
        mine = next((x for x in e.responses if x.user_id == user.id), None)
        items.append({
            "ref_kind": "event", "id": e.id, "kind": e.kind, "title": _event_label(e),
            "custom_title": e.title, "start_time": e.start_time, "end_time": e.end_time,
            "location": e.location, "note": e.note, "is_cancelled": e.is_cancelled,
            "series_id": e.series_id,
            "maps_link": f"https://www.google.com/maps/search/?api=1&query={e.location}" if e.location else None,
            "my_status": mine.status if mine and mine.status else "unbekannt",
            "count_zusage": sum(1 for x in e.responses if x.user_id in player_ids and x.status == "zusage"),
            "count_absage": sum(1 for x in e.responses if x.user_id in player_ids and x.status == "absage"),
            "count_verletzt": sum(1 for x in e.responses if x.user_id in player_ids and x.status == "verletzt"),
            "count_individuell": sum(1 for x in e.responses if x.user_id in player_ids and x.status == "individuell"),
            "count_offen": max(0, n_players - sum(1 for x in e.responses if x.status and x.user_id in player_ids)),
            "duties": duties.get(("event", e.id), []),
        })
    items.sort(key=lambda x: x["start_time"])
    return items


# ---------------------------------------------------------------------------
# Trainings-Serien (Standard) und Einzeltermine (Spezial / Event / Sitzung)
# ---------------------------------------------------------------------------
@router.get("/events/series")
def list_series(db: Session = Depends(get_db), admin=Depends(require_admin)):
    out = []
    for s in db.query(models.TrainingSeries).order_by(models.TrainingSeries.id).all():
        upcoming = db.query(models.TeamEvent).filter(
            models.TeamEvent.series_id == s.id, models.TeamEvent.start_time >= now_local()).count()
        out.append({"id": s.id, "weekdays": [int(x) for x in s.weekdays.split(",") if x != ""],
                    "start_hhmm": s.start_hhmm, "duration_minutes": s.duration_minutes,
                    "location": s.location, "valid_from": s.valid_from, "valid_until": s.valid_until,
                    "upcoming": upcoming})
    return out


@router.post("/events/series")
def create_series(body: SeriesIn, db: Session = Depends(get_db), admin=Depends(require_admin)):
    days = sorted({d for d in body.weekdays if 0 <= d <= 6})
    if not days:
        raise HTTPException(400, "Bitte mindestens einen Wochentag wählen")
    h, m = _parse_hhmm(body.start_hhmm)
    today = now_local()
    vfrom = _parse_date(body.valid_from) if body.valid_from else datetime(today.year, today.month, today.day)
    vuntil = _parse_date(body.valid_until).replace(hour=23, minute=59) if body.valid_until else next_season_end(today)
    if vuntil < vfrom:
        raise HTTPException(400, "Enddatum liegt vor dem Startdatum")
    if (vuntil - vfrom).days > 400:
        raise HTTPException(400, "Maximal ein gutes Jahr auf einmal")
    s = models.TrainingSeries(weekdays=",".join(map(str, days)), start_hhmm=f"{h:02d}:{m:02d}",
                              duration_minutes=body.duration_minutes, location=body.location,
                              valid_from=vfrom, valid_until=vuntil)
    db.add(s)
    db.flush()
    n = 0
    for d in daterange_weekdays(vfrom, vuntil, set(days)):
        start = d.replace(hour=h, minute=m)
        db.add(models.TeamEvent(kind="training", start_time=start,
                                end_time=start + timedelta(minutes=body.duration_minutes),
                                location=body.location, series_id=s.id))
        n += 1
    db.commit()
    if n:
        from app.services import webpush
        wd = ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"]
        webpush.notify(webpush.player_ids(db), "🏋️ Neue Trainingszeiten",
                       f"{' + '.join(wd[d] for d in days)} {h:02d}:{m:02d}{' · ' + body.location if body.location else ''}"
                       f" – jede Woche bis {vuntil:%d.%m.%Y}", "/?open=tab:spiele", "termine", f"series-{s.id}")
    return {"id": s.id, "created": n}


@router.delete("/events/series/{series_id}")
def delete_series(series_id: int, db: Session = Depends(get_db), admin=Depends(require_admin)):
    """Loescht alle KOMMENDEN Trainings dieser Serie. Vergangene bleiben fuer die Praesenz-Statistik."""
    s = db.get(models.TrainingSeries, series_id)
    if not s:
        raise HTTPException(404, "Serie nicht gefunden")
    now = now_local()
    removed = 0
    for e in db.query(models.TeamEvent).filter(models.TeamEvent.series_id == series_id).all():
        if e.start_time >= now:
            db.delete(e)
            removed += 1
        else:
            e.series_id = None
    db.flush()
    db.delete(s)
    db.commit()
    return {"removed": removed}


@router.post("/events")
def create_event(body: EventIn, db: Session = Depends(get_db), admin=Depends(require_admin)):
    if body.kind not in KINDS:
        raise HTTPException(400, "Art muss training, event oder sitzung sein")
    h, m = _parse_hhmm(body.start_hhmm)
    start = _parse_date(body.date).replace(hour=h, minute=m)
    e = models.TeamEvent(kind=body.kind, title=(body.title or "").strip() or None, start_time=start,
                         end_time=start + timedelta(minutes=body.duration_minutes or 90),
                         location=body.location, note=body.note)
    db.add(e)
    db.commit()
    if e.start_time > now_local():
        from app.services import webpush
        icon = {"training": "🏋️", "event": "🎉", "sitzung": "🗣️"}.get(e.kind, "📅")
        webpush.notify(webpush.player_ids(db), f"{icon} Neuer Termin: {_event_label(e)}",
                       f"{webpush.fmt_when(e.start_time)}{' · ' + e.location if e.location else ''} – bitte eintragen",
                       f"/?open=event:{e.id}", "termine", f"event-{e.id}")
    return {"id": e.id}


@router.get("/events/{event_id}")
def get_event(event_id: int, db: Session = Depends(get_db), user: models.User = Depends(get_current_user)):
    e = db.get(models.TeamEvent, event_id)
    if not e:
        raise HTTPException(404, "Termin nicht gefunden")
    from app.services import attendance_default
    if attendance_default.fill(db):
        db.refresh(e)
    goalies = attendance_default.goalie_ids(db)
    responses = {r.user_id: r for r in e.responses}
    players = []
    for p in team_members(db):
        r = responses.get(p.id)
        players.append({"user_id": p.id, "name": p.name, "is_staff": bool(p.staff), "staff_title": p.staff_title,
                        "is_goalie": p.id in goalies,
                        "status": r.status if r and r.status else "unbekannt",
                        "reason": r.reason if r and (user.is_trainer or p.id == user.id) else None,
                        "has_reason": bool(r and r.reason),
                        "auto": bool(r and r.set_at is None),
                        "present": r.present if r else None})
    mine = responses.get(user.id)
    from app.services.duty_rotation import duties_for
    return {
        "id": e.id, "kind": e.kind, "title": _event_label(e), "custom_title": e.title,
        "start_time": e.start_time, "end_time": e.end_time, "location": e.location, "note": e.note,
        "is_cancelled": e.is_cancelled, "series_id": e.series_id,
        "my_status": mine.status if mine and mine.status else "unbekannt",
        "my_reason": mine.reason if mine else None,
        "players": players,
        "duties": duties_for(db, "event", e.id),
        "is_past": e.start_time < now_local(),
    }


@router.patch("/events/{event_id}")
def update_event(event_id: int, body: EventPatch, db: Session = Depends(get_db), admin=Depends(require_admin)):
    e = db.get(models.TeamEvent, event_id)
    if not e:
        raise HTTPException(404, "Termin nicht gefunden")
    from app.services.attendance_default import invalidate
    invalidate()   # verschoben / wieder aktiv -> Standard-Anmeldung neu pruefen
    data = body.model_dump(exclude_unset=True)
    old_start, old_cancel = e.start_time, e.is_cancelled
    if "title" in data:
        e.title = (data["title"] or "").strip() or None
    for f in ("location", "note", "is_cancelled"):
        if f in data:
            setattr(e, f, data[f])
    if "date" in data or "start_hhmm" in data or "duration_minutes" in data:
        d = _parse_date(data["date"]) if data.get("date") else e.start_time
        h, m = _parse_hhmm(data["start_hhmm"]) if data.get("start_hhmm") else (e.start_time.hour, e.start_time.minute)
        dur = data.get("duration_minutes") or int(((e.end_time or e.start_time + timedelta(minutes=90)) - e.start_time).total_seconds() // 60)
        e.start_time = d.replace(hour=h, minute=m)
        e.end_time = e.start_time + timedelta(minutes=dur)
    db.commit()
    if e.start_time > now_local() - timedelta(hours=1):
        from app.services import webpush
        msg = None
        if e.is_cancelled and not old_cancel:
            msg = (f"❌ Fällt aus: {_event_label(e)}", webpush.fmt_when(e.start_time))
        elif old_cancel and not e.is_cancelled:
            msg = (f"✅ Findet doch statt: {_event_label(e)}", webpush.fmt_when(e.start_time))
        elif not e.is_cancelled and e.start_time != old_start:
            msg = (f"🕒 Verschoben: {_event_label(e)}", f"neu {webpush.fmt_when(e.start_time)} (vorher {webpush.fmt_when(old_start)})")
        if msg:
            webpush.notify(webpush.player_ids(db), msg[0], msg[1], f"/?open=event:{e.id}", "termine", f"event-{e.id}")
    return {"ok": True}


@router.delete("/events/{event_id}")
def delete_event(event_id: int, db: Session = Depends(get_db), admin=Depends(require_admin)):
    e = db.get(models.TeamEvent, event_id)
    if e:
        db.query(models.DutyAssignment).filter(models.DutyAssignment.ref_kind == "event",
                                               models.DutyAssignment.ref_id == event_id).delete()
        db.delete(e)
        db.commit()
    return {"ok": True}


@router.put("/events/{event_id}/response")
def respond(event_id: int, body: ResponseIn, db: Session = Depends(get_db), user: models.User = Depends(get_current_user)):
    if body.status not in RESPONSE_STATUSES:
        raise HTTPException(400, "status muss zusage, absage, verletzt oder individuell sein")
    e = db.get(models.TeamEvent, event_id)
    if not e:
        raise HTTPException(404, "Termin nicht gefunden")
    if e.is_cancelled:
        raise HTTPException(400, "Dieser Termin fällt aus")
    r = db.query(models.EventResponse).filter(models.EventResponse.event_id == event_id,
                                              models.EventResponse.user_id == user.id).first()
    if r is None:
        r = models.EventResponse(event_id=event_id, user_id=user.id)
        db.add(r)
    previous = r.status
    r.status = body.status
    r.reason = (body.reason or "").strip() or None
    from app.routers.activity import log_response
    log_response(db, user.id, "event", event_id, body.status, previous, r.reason)
    r.set_at = datetime.utcnow()
    # Erinnerungen zu diesem Termin sind damit erledigt
    db.query(models.Notice).filter(models.Notice.user_id == user.id, models.Notice.ref_kind == "event",
                                   models.Notice.ref_id == event_id).update({"is_read": True})
    db.commit()
    return {"ok": True}


@router.put("/events/{event_id}/presence")
def set_presence(event_id: int, body: PresenceIn, db: Session = Depends(get_db), admin=Depends(require_admin)):
    e = db.get(models.TeamEvent, event_id)
    if not e:
        raise HTTPException(404, "Termin nicht gefunden")
    r = db.query(models.EventResponse).filter(models.EventResponse.event_id == event_id,
                                              models.EventResponse.user_id == body.user_id).first()
    if r is None:
        r = models.EventResponse(event_id=event_id, user_id=body.user_id)
        db.add(r)
    r.present = body.present
    db.commit()
    return {"ok": True}


# ---------------------------------------------------------------------------
# Wer hat noch nicht geantwortet? + Erinnerung
# ---------------------------------------------------------------------------
def _check_ref(db: Session, ref_kind: str, ref_id: int):
    if ref_kind not in ("game", "event"):
        raise HTTPException(400, "ref_kind muss game oder event sein")
    if ref_start(db, ref_kind, ref_id) is None:
        raise HTTPException(404, "Termin nicht gefunden")


def _missing(db: Session, ref_kind: str, ref_id: int) -> List[models.User]:
    responded = game_responded_ids(db, ref_id) if ref_kind == "game" else event_responded_ids(db, ref_id)
    return [p for p in team_members(db) if p.id not in responded]   # Trainer muessen sich auch melden


@router.get("/reminders/{ref_kind}/{ref_id}")
def who_is_missing(ref_kind: str, ref_id: int, db: Session = Depends(get_db), admin=Depends(require_admin)):
    _check_ref(db, ref_kind, ref_id)
    missing = _missing(db, ref_kind, ref_id)
    last = (
        db.query(models.Notice)
        .filter(models.Notice.ref_kind == ref_kind, models.Notice.ref_id == ref_id)
        .order_by(models.Notice.created_at.desc())
        .first()
    )
    return {"title": ref_title(db, ref_kind, ref_id), "missing": [{"user_id": p.id, "name": p.name} for p in missing],
            "total_players": len(team_members(db)), "last_reminder": last.created_at if last else None}


@router.post("/reminders/{ref_kind}/{ref_id}")
def send_reminder(ref_kind: str, ref_id: int, db: Session = Depends(get_db), admin=Depends(require_admin)):
    _check_ref(db, ref_kind, ref_id)
    title = ref_title(db, ref_kind, ref_id)
    missing = _missing(db, ref_kind, ref_id)
    for p in missing:
        db.add(models.Notice(user_id=p.id, text=f"Bitte trag dich ein: {title}", ref_kind=ref_kind, ref_id=ref_id))
    db.commit()
    from app.services import webpush
    webpush.notify([p.id for p in missing], "🔔 Bitte trag dich ein", title or "", f"/?open={ref_kind}:{ref_id}",
                   "erinnerung", f"resp-{ref_kind}-{ref_id}")
    names = ", ".join(p.name for p in missing)
    whatsapp = (f"Hoi zäme 👋 Bitte tragt euch in der Team-App ein für: {title}. "
                f"Noch offen: {names}" if missing else "")
    return {"sent": len(missing), "names": [p.name for p in missing], "whatsapp_text": whatsapp}


@router.get("/notices")
def my_notices(db: Session = Depends(get_db), user: models.User = Depends(get_current_user)):
    rows = (
        db.query(models.Notice)
        .filter(models.Notice.user_id == user.id, models.Notice.is_read == False)  # noqa: E712
        .order_by(models.Notice.created_at.desc())
        .all()
    )
    # Erinnerungen fuer vergangene Termine nicht mehr anzeigen
    now = now_local()
    out = []
    for n in rows:
        start = ref_start(db, n.ref_kind, n.ref_id) if n.ref_kind else None
        if start and start < now - timedelta(hours=3):
            continue
        out.append({"id": n.id, "text": n.text, "ref_kind": n.ref_kind, "ref_id": n.ref_id, "created_at": n.created_at})
    return out


@router.post("/notices/{notice_id}/read")
def read_notice(notice_id: int, db: Session = Depends(get_db), user: models.User = Depends(get_current_user)):
    n = db.get(models.Notice, notice_id)
    if n and n.user_id == user.id:
        n.is_read = True
        db.commit()
    return {"ok": True}


def open_notice_count(db: Session, user_id: int) -> int:
    now = now_local()
    c = 0
    for n in db.query(models.Notice).filter(models.Notice.user_id == user_id, models.Notice.is_read == False).all():  # noqa: E712
        start = ref_start(db, n.ref_kind, n.ref_id) if n.ref_kind else None
        if start and start < now - timedelta(hours=3):
            continue
        c += 1
    return c
