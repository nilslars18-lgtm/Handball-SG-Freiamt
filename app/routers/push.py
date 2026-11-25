"""App-Installation (PWA), Push-Benachrichtigungen und Kalender-Abo."""
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, Optional
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from icalendar import Calendar, Event
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app import models
from app.database import get_db
from app.deps import get_current_user, require_admin
from app.services import webpush
from app.services.agenda import now_local

router = APIRouter(tags=["app"])
STATIC = Path(__file__).resolve().parent.parent / "static"
TZ = ZoneInfo("Europe/Zurich")
APP_NAME = "SG Freiamt U19"


# ---------------------------------------------------------------------------
# App-Installation: Manifest, Service Worker, Icons
# ---------------------------------------------------------------------------
@router.get("/manifest.webmanifest", include_in_schema=False)
def manifest():
    data = {
        "name": "SG Freiamt PLUS · U19 Inter",
        "short_name": APP_NAME,
        "description": "Team-App der U19 Inter: Termine, Aufgebot, Statistik, Bussen",
        "id": "/",
        "start_url": "/?source=app",
        "scope": "/",
        "display": "standalone",
        "orientation": "portrait",
        "background_color": "#1d4ed8",
        "theme_color": "#1d4ed8",
        "lang": "de-CH",
        "icons": [
            {"src": "/icon-192.png", "sizes": "192x192", "type": "image/png", "purpose": "any"},
            {"src": "/icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "any"},
            {"src": "/icon-maskable-512.png", "sizes": "512x512", "type": "image/png", "purpose": "maskable"},
        ],
        "shortcuts": [
            {"name": "Termine", "url": "/?open=tab:spiele", "icons": [{"src": "/icon-192.png", "sizes": "192x192"}]},
            {"name": "Statistik", "url": "/?open=tile:statistik", "icons": [{"src": "/icon-192.png", "sizes": "192x192"}]},
        ],
    }
    return JSONResponse(data, media_type="application/manifest+json",
                        headers={"Cache-Control": "public, max-age=3600"})


@router.get("/sw.js", include_in_schema=False)
def service_worker():
    return FileResponse(STATIC / "sw.js", media_type="application/javascript",
                        headers={"Cache-Control": "no-cache", "Service-Worker-Allowed": "/"})


def _icon(name: str):
    return FileResponse(STATIC / name, media_type="image/png", headers={"Cache-Control": "public, max-age=604800"})


@router.get("/icon-192.png", include_in_schema=False)
def icon192():
    return _icon("icon-192.png")


@router.get("/icon-512.png", include_in_schema=False)
def icon512():
    return _icon("icon-512.png")


@router.get("/icon-maskable-512.png", include_in_schema=False)
def icon_maskable():
    return _icon("icon-maskable-512.png")


@router.get("/apple-touch-icon.png", include_in_schema=False)
def apple_icon():
    return _icon("apple-touch-icon.png")


@router.get("/badge-96.png", include_in_schema=False)
def badge():
    return _icon("badge-96.png")


# ---------------------------------------------------------------------------
# Anleitung (HTML mit Bildern, nur fuer eingeloggte)
# ---------------------------------------------------------------------------
GUIDE = Path(__file__).resolve().parent.parent / "seed" / "anleitung.html"


_GUIDE_CACHE = {}


@router.get("/guide", include_in_schema=False)
def guide(request: Request, user: models.User = Depends(get_current_user)):
    """Anleitung (gross, mit Bildern): mit ETag - das Handy laedt sie nur neu, wenn sie sich geaendert hat."""
    if "data" not in _GUIDE_CACHE:
        import hashlib
        data = GUIDE.read_bytes()
        _GUIDE_CACHE.update(data=data, etag='"' + hashlib.sha1(data).hexdigest()[:16] + '"')
    etag = _GUIDE_CACHE["etag"]
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers={"ETag": etag, "Cache-Control": "private, no-cache"})
    return Response(_GUIDE_CACHE["data"], media_type="text/html; charset=utf-8",
                    headers={"ETag": etag, "Cache-Control": "private, no-cache"})


@router.get("/datenschutz", include_in_schema=False)
def datenschutz():
    """Datenschutzerklaerung - oeffentlich (auch ohne Login lesbar)."""
    return Response((GUIDE.parent / "datenschutz.html").read_bytes(), media_type="text/html; charset=utf-8",
                    headers={"Cache-Control": "public, max-age=3600"})


@router.get("/guide-docx", include_in_schema=False)
def guide_docx(user: models.User = Depends(get_current_user)):
    f = GUIDE.parent / "Anleitung_Team-App_U19_Spieler.docx"
    return Response(f.read_bytes(), media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                    headers={"Content-Disposition": 'attachment; filename="Anleitung_Team-App_U19.docx"'})


# ---------------------------------------------------------------------------
# Push
# ---------------------------------------------------------------------------
class SubIn(BaseModel):
    endpoint: str
    keys: Dict[str, str]
    user_agent: Optional[str] = None


class EndpointIn(BaseModel):
    endpoint: str


class PrefIn(BaseModel):
    key: str
    enabled: bool


@router.get("/push/public-key")
def push_public_key(db: Session = Depends(get_db)):
    return {"key": webpush.public_key_b64(db)}


@router.post("/push/subscribe")
def push_subscribe(body: SubIn, db: Session = Depends(get_db), user: models.User = Depends(get_current_user)):
    p256dh, auth = body.keys.get("p256dh"), body.keys.get("auth")
    if not body.endpoint.startswith("https://") or not p256dh or not auth:
        raise HTTPException(400, "Ungültiges Abo")
    sub = db.query(models.PushSubscription).filter(models.PushSubscription.endpoint == body.endpoint).first()
    if sub is None:
        sub = models.PushSubscription(endpoint=body.endpoint, user_id=user.id, p256dh=p256dh, auth=auth)
        db.add(sub)
    sub.user_id, sub.p256dh, sub.auth = user.id, p256dh, auth   # Handy wechselt den Benutzer -> umhaengen
    sub.user_agent = (body.user_agent or "")[:250] or None
    db.commit()
    return {"ok": True, "devices": _device_count(db, user.id)}


@router.post("/push/unsubscribe")
def push_unsubscribe(body: EndpointIn, db: Session = Depends(get_db), user: models.User = Depends(get_current_user)):
    db.query(models.PushSubscription).filter(models.PushSubscription.endpoint == body.endpoint,
                                             models.PushSubscription.user_id == user.id).delete()
    db.commit()
    return {"ok": True}


def _device_count(db: Session, user_id: int) -> int:
    return db.query(models.PushSubscription).filter(models.PushSubscription.user_id == user_id).count()


@router.get("/push/settings")
def push_settings(db: Session = Depends(get_db), user: models.User = Depends(get_current_user)):
    cats = dict(webpush.CATEGORIES)
    if user.is_trainer:
        cats.update(webpush.ADMIN_CATEGORIES)
    if user.base_role == models.BaseRole.ADMIN:
        cats.update(webpush.OWNER_CATEGORIES)
    return {
        "devices": _device_count(db, user.id),
        "categories": [{"key": k, "label": v, "enabled": webpush.wants(db, user.id, k)} for k, v in cats.items()],
    }


@router.put("/push/settings")
def push_set_pref(body: PrefIn, db: Session = Depends(get_db), user: models.User = Depends(get_current_user)):
    if body.key not in webpush.CATEGORIES and body.key not in webpush.ADMIN_CATEGORIES and body.key not in webpush.OWNER_CATEGORIES:
        raise HTTPException(400, "Unbekannte Kategorie")
    p = db.get(models.NotifyPref, (user.id, body.key))
    if p is None:
        p = models.NotifyPref(user_id=user.id, key=body.key)
        db.add(p)
    p.enabled = body.enabled
    db.commit()
    return {"ok": True}


@router.post("/push/test")
def push_test(db: Session = Depends(get_db), user: models.User = Depends(get_current_user)):
    n = _device_count(db, user.id)
    if not n:
        raise HTTPException(400, "Auf diesem Konto sind noch keine Benachrichtigungen aktiviert.")
    webpush.notify([user.id], "🤾 Test erfolgreich", f"Hoi {user.name.split(' ')[0]}, die Benachrichtigungen funktionieren!",
                   "/?open=tile:einstellungen", None, "test", wait=True)
    return {"ok": True, "devices": n}


@router.get("/push/overview")
def push_overview(db: Session = Depends(get_db), admin=Depends(require_admin)):
    """Admin: wer hat Benachrichtigungen aktiviert?"""
    from app.services.agenda import team_members
    ids = {s.user_id for s in db.query(models.PushSubscription).all()}
    players = team_members(db)
    return {"active": [p.name for p in players if p.id in ids], "missing": [p.name for p in players if p.id not in ids]}


@router.post("/push/run-jobs")
def push_run_jobs(db: Session = Depends(get_db), admin=Depends(require_admin)):
    from app.services.push_jobs import run_once
    return run_once(db)


# ---------------------------------------------------------------------------
# Kalender-Abo (iPhone / Google / Outlook)
# ---------------------------------------------------------------------------
def _token(db: Session, user: models.User, renew: bool = False) -> str:
    row = db.get(models.CalendarToken, user.id)
    if row is None:
        row = models.CalendarToken(user_id=user.id, token=secrets.token_urlsafe(24))
        db.add(row)
    elif renew:
        row.token = secrets.token_urlsafe(24)
        row.created_at = datetime.utcnow()
    db.commit()
    return row.token


def _cal_urls(request: Request, token: str) -> dict:
    base = str(request.base_url).rstrip("/")
    if base.startswith("http://") and "localhost" not in base and "127.0.0.1" not in base:
        base = "https://" + base[len("http://"):]   # Render liefert intern http
    https = f"{base}/cal/{token}.ics"
    webcal = "webcal://" + https.split("://", 1)[1]
    from urllib.parse import quote
    return {"url": https, "webcal": webcal, "google": f"https://calendar.google.com/calendar/r?cid={quote(webcal, safe='')}"}


@router.get("/calendar/me")
def calendar_me(request: Request, db: Session = Depends(get_db), user: models.User = Depends(get_current_user)):
    return _cal_urls(request, _token(db, user))


@router.post("/calendar/renew")
def calendar_renew(request: Request, db: Session = Depends(get_db), user: models.User = Depends(get_current_user)):
    return _cal_urls(request, _token(db, user, renew=True))


def _utc(dt: datetime) -> datetime:
    return dt.replace(tzinfo=TZ).astimezone(timezone.utc)


@router.get("/cal/{token}.ics", include_in_schema=False)
def calendar_feed(token: str, request: Request, db: Session = Depends(get_db)):
    app_url = _cal_urls(request, "x")["url"].rsplit("/cal/", 1)[0]
    row = db.query(models.CalendarToken).filter(models.CalendarToken.token == token).first()
    if not row:
        raise HTTPException(404, "Kalender nicht gefunden")
    user = db.get(models.User, row.user_id)
    if not user or not user.is_active:
        raise HTTPException(404, "Kalender nicht gefunden")
    from app.routers.events import _event_label
    from app.routers.games import _aufgebot_status_for

    now = now_local()
    start, end = now - timedelta(days=60), now + timedelta(days=365)
    my_duties: Dict[tuple, list] = {}
    for a in db.query(models.DutyAssignment).filter(models.DutyAssignment.user_id == user.id).all():
        if a.duty and a.duty.is_active:
            my_duties.setdefault((a.ref_kind, a.ref_id), []).append(a.duty.name)
    reports = {r.game_id: r for r in db.query(models.GameReport).all()}
    resp = {r.event_id: r.status for r in db.query(models.EventResponse).filter(models.EventResponse.user_id == user.id).all()}
    avail = {a.game_id: a.status.value for a in db.query(models.Availability).filter(models.Availability.user_id == user.id).all()}
    AUF = {"aufgeboten": "✅ Du bist aufgeboten", "verletzt": "🟠 Verletzt (Bank)",
           "nicht_aufgeboten": "❌ Nicht aufgeboten", "pending": "⏳ Aufgebot folgt"}
    RESP = {"zusage": "✅ dabei", "absage": "❌ abgemeldet", "verletzt": "🟠 verletzt", "individuell": "🏃 individuell",
            "kann_spielen": "✅ kann spielen", "abwesend": "❌ abwesend"}

    cal = Calendar()
    cal.add("prodid", "-//SG Freiamt PLUS//U19 Team-App//DE")
    cal.add("version", "2.0")
    cal.add("calscale", "GREGORIAN")
    cal.add("method", "PUBLISH")
    cal.add("x-wr-calname", "SG Freiamt U19")
    cal.add("x-wr-timezone", "Europe/Zurich")
    cal.add("refresh-interval;value=duration", "PT1H")
    cal.add("x-published-ttl", "PT1H")
    stamp = datetime.now(timezone.utc)

    for g in db.query(models.Game).filter(models.Game.start_time >= start, models.Game.start_time < end).all():
        ev = Event()
        kind = "Heimspiel" if g.is_home else "Auswärtsspiel" if g.is_home is False else "Spiel"
        ev.add("uid", f"game-{g.id}@sgfreiamt-u19")
        ev.add("dtstamp", stamp)
        ev.add("summary", f"🤾 {kind} gegen {g.opponent}")
        ev.add("dtstart", _utc(g.start_time))
        ev.add("dtend", _utc(g.end_time or g.start_time + timedelta(hours=2)))
        if g.location_name:
            ev.add("location", g.location_name)
        lines = []
        if not user.is_trainer:
            st, _ = _aufgebot_status_for(db, g, user)
            lines.append(AUF.get(st, ""))
            if g.id in avail and avail[g.id] != "unbekannt":
                lines.append("Deine Meldung: " + RESP.get(avail[g.id], avail[g.id]))
        r = reports.get(g.id)
        if r and r.score_home is not None and r.score_away is not None:
            lines.append(f"🏆 Resultat {r.score_home}:{r.score_away}")
        if r and r.writer_id == user.id and not r.is_done:
            lines.append("📝 Du schreibst den Spielbericht")
        for d in my_duties.get(("game", g.id), []):
            lines.append(f"🧹 Dein Ämtli: {d}")
        if g.maps_link:
            lines.append(f"Route: {g.maps_link}")
        lines.append(f"In der App öffnen: {app_url}/?open=game:{g.id}")
        ev.add("description", "\n".join(x for x in lines if x))
        ev.add("categories", ["Spiel"])
        cal.add_component(ev)

    for e in db.query(models.TeamEvent).filter(models.TeamEvent.start_time >= start, models.TeamEvent.start_time < end).all():
        ev = Event()
        icon = {"training": "🏋️", "event": "🎉", "sitzung": "🗣️"}.get(e.kind, "📅")
        label = _event_label(e)
        ev.add("uid", f"event-{e.id}@sgfreiamt-u19")
        ev.add("dtstamp", stamp)
        ev.add("summary", f"{'❌ ABGESAGT: ' if e.is_cancelled else ''}{icon} {label}")
        ev.add("dtstart", _utc(e.start_time))
        ev.add("dtend", _utc(e.end_time or e.start_time + timedelta(minutes=90)))
        if e.is_cancelled:
            ev.add("status", "CANCELLED")
        if e.location:
            ev.add("location", e.location)
        lines = []
        if e.note:
            lines.append(e.note)
        if e.id in resp:
            lines.append("Deine Antwort: " + RESP.get(resp[e.id], resp[e.id]))
        for d in my_duties.get(("event", e.id), []):
            lines.append(f"🧹 Dein Ämtli: {d}")
        lines.append(f"In der App öffnen: {app_url}/?open=event:{e.id}")
        ev.add("description", "\n".join(lines))
        ev.add("categories", ["Training" if e.kind == "training" else "Event"])
        cal.add_component(ev)

    return Response(cal.to_ical(), media_type="text/calendar; charset=utf-8",
                    headers={"Content-Disposition": 'inline; filename="sg-freiamt-u19.ics"',
                             "Cache-Control": "no-cache"})
