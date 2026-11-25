"""Automatische Benachrichtigungen (laufen alle 10 Minuten im Hintergrund).

- Ämtli: am Vortag (ab ca. 30 Std. vorher) an den Eingeteilten.
- Eintragen: ca. 30 Std. vor einem Termin an alle, die sich noch nicht
  eingetragen haben.
- Spielbericht: kurz vor dem Spiel ("du bist dran") und 3 Std. nach dem
  Spiel ("bitte schreiben"), falls noch nicht fertig.

Jede Meldung kommt nur einmal (Tabelle push_sent). Nachts (21:30-07:00)
wird nichts verschickt - die Meldung kommt dann am Morgen.
"""
import logging
import threading
import time
from datetime import timedelta

from app import models
from app.services import webpush
from app.services.agenda import now_local

log = logging.getLogger("push_jobs")
WINDOW_H = 30
INTERVAL_S = 600


def _quiet(now) -> bool:
    hm = now.hour * 60 + now.minute
    return hm < 7 * 60 or hm > 21 * 60 + 30


def _when(start, now) -> str:
    wd = ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"][start.weekday()]
    day = "Heute" if start.date() == now.date() else "Morgen" if start.date() == (now + timedelta(days=1)).date() else f"{wd} {start:%d.%m.}"
    return f"{day} {start:%H:%M}"


def run_once(db) -> dict:
    from app.routers.events import _event_label, _missing

    now = now_local()
    sent = {"aemtli": 0, "erinnerung": 0, "bericht": 0}
    try:   # Standard-Anmeldung (alle gruen) fuer neue Termine / neue Spieler
        from app.services import attendance_default
        sent["auto_dabei"] = attendance_default.fill(db, force=True)
    except Exception as e:  # noqa: BLE001
        db.rollback()
        log.warning("default attendance: %s", e)
    try:   # Essen nach dem Spiel - auch spaet abends
        from app.services.food_jobs import run_once as food_run
        sent["essen"] = food_run(db)
    except Exception as e:  # noqa: BLE001
        db.rollback()
        log.warning("food: %s", e)
    try:   # alte Chat-Nachrichten (nur wenn der Admin das eingeschaltet hat)
        from app.routers.chat import cleanup_old
        sent["chat_cleanup"] = cleanup_old(db)
    except Exception as e:  # noqa: BLE001
        db.rollback()
        log.warning("chat cleanup: %s", e)
    if _quiet(now):
        return sent
    horizon = now + timedelta(hours=WINDOW_H)

    games = db.query(models.Game).filter(models.Game.start_time > now, models.Game.start_time <= horizon).all()
    events = (db.query(models.TeamEvent)
              .filter(models.TeamEvent.start_time > now, models.TeamEvent.start_time <= horizon,
                      models.TeamEvent.is_cancelled == False).all())  # noqa: E712
    refs = [("game", g.id, g.start_time, f"{'Heimspiel' if g.is_home else 'Auswärtsspiel' if g.is_home is False else 'Spiel'} gegen {g.opponent}")
            for g in games] + [("event", e.id, e.start_time, _event_label(e)) for e in events]

    # 1) Ämtli
    try:
        from app.services.duty_rotation import sync_duties
        sync_duties(db)
    except Exception as e:  # noqa: BLE001
        log.warning("duties: %s", e)
    for kind, rid, start, title in refs:
        rows = db.query(models.DutyAssignment).filter(models.DutyAssignment.ref_kind == kind,
                                                      models.DutyAssignment.ref_id == rid).all()
        for a in rows:
            if a.user_id and a.duty and a.duty.is_active and webpush.once(db, f"aemtli:{kind}:{rid}:{a.duty_id}:{a.user_id}"):
                webpush.notify([a.user_id], f"🧹 Dein Ämtli: {a.duty.name}",
                               f"{_when(start, now)} · {title}", f"/?open={kind}:{rid}", "aemtli", f"aemtli-{kind}-{rid}")
                sent["aemtli"] += 1

    # 2) Noch nicht eingetragen
    for kind, rid, start, title in refs:
        for p in _missing(db, kind, rid):
            if webpush.once(db, f"resp:{kind}:{rid}:{p.id}"):
                webpush.notify([p.id], "🔔 Bitte trag dich ein", f"{title} · {_when(start, now)}",
                               f"/?open={kind}:{rid}", "erinnerung", f"resp-{kind}-{rid}")
                sent["erinnerung"] += 1

    # 3) Spielbericht
    try:
        from app.services.report_rotation import sync_assignments
        sync_assignments(db)
    except Exception as e:  # noqa: BLE001
        log.warning("reports: %s", e)
    reps = (db.query(models.GameReport).join(models.Game, models.Game.id == models.GameReport.game_id)
            .filter(models.GameReport.writer_id != None, models.GameReport.is_done == False,  # noqa: E711,E712
                    models.Game.start_time > now - timedelta(days=4), models.Game.start_time <= horizon).all())
    for r in reps:
        g = db.get(models.Game, r.game_id)
        title = f"{'Heimspiel' if g.is_home else 'Auswärtsspiel' if g.is_home is False else 'Spiel'} gegen {g.opponent}"
        if g.start_time > now:
            if webpush.once(db, f"bericht-pre:{g.id}:{r.writer_id}"):
                webpush.notify([r.writer_id], "📝 Du schreibst den Spielbericht",
                               f"{title} · {_when(g.start_time, now)}", f"/?open=report:{g.id}", "bericht", f"bericht-{g.id}")
                sent["bericht"] += 1
        elif g.start_time + timedelta(hours=3) <= now:
            if webpush.once(db, f"bericht-post:{g.id}:{r.writer_id}"):
                webpush.notify([r.writer_id], "📝 Spielbericht schreiben",
                               f"{title} ist vorbei – Resultat und Statistik holt die App von handball.ch.",
                               f"/?open=report:{g.id}", "bericht", f"bericht-{g.id}")
                sent["bericht"] += 1
    return sent


_started = {"v": False}


def background_jobs() -> None:
    if _started["v"]:
        return
    _started["v"] = True

    def loop():
        from app.database import SessionLocal
        time.sleep(30)
        while True:
            db = SessionLocal()
            try:
                run_once(db)
            except Exception as e:  # noqa: BLE001
                log.warning("push jobs: %s", e)
            finally:
                db.close()
            time.sleep(INTERVAL_S)

    threading.Thread(target=loop, daemon=True).start()
