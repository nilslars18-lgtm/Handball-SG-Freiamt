"""Einmalig: Standard-Trainings eintragen (Di 19:00-20:30, Do 20:30-22:00).

Wird nur einmal angewendet (Markierung in app_assets). Gibt es fuer einen
Wochentag schon ein Standard-Training, wird dieser Tag uebersprungen
(keine doppelten Trainings). Spaetere Aenderungen in der App unter
Verwaltung → Trainings & Termine.
"""
import logging
from datetime import datetime, timedelta

from app import models
from app.database import SessionLocal
from app.services.agenda import daterange_weekdays, next_season_end, now_local

VERSION = 1
TRAININGS = [
    # (Wochentag 0=Mo, Beginn, Dauer in Minuten)
    (1, "19:00", 90),   # Dienstag 19:00-20:30
    (3, "20:30", 90),   # Donnerstag 20:30-22:00
]


def apply_training_seed() -> None:
    marker = f"seed_trainings_v{VERSION}"
    db = SessionLocal()
    try:
        if db.get(models.AppAsset, marker):
            return
        now = now_local()
        today = datetime(now.year, now.month, now.day)
        until = next_season_end(now)
        existing_days = set()
        for s in db.query(models.TrainingSeries).filter(models.TrainingSeries.valid_until >= today).all():
            existing_days |= {int(d) for d in s.weekdays.split(",") if d.strip() != ""}
        for wd, hhmm, dur in TRAININGS:
            if wd in existing_days:
                continue
            h, m = map(int, hhmm.split(":"))
            s = models.TrainingSeries(weekdays=str(wd), start_hhmm=hhmm, duration_minutes=dur,
                                      location=None, valid_from=today, valid_until=until)
            db.add(s)
            db.flush()
            for d in daterange_weekdays(today, until, {wd}):
                start = d.replace(hour=h, minute=m)
                clash = db.query(models.TeamEvent).filter(models.TeamEvent.kind == "training",
                                                          models.TeamEvent.start_time == start).first()
                if clash:
                    continue
                db.add(models.TeamEvent(kind="training", start_time=start, end_time=start + timedelta(minutes=dur),
                                        series_id=s.id))
        db.add(models.AppAsset(key=marker, data=b"1", mime="text/plain"))
        db.commit()
    except Exception as exc:  # darf den Start nie verhindern
        db.rollback()
        logging.getLogger("uvicorn").warning("Trainings-Import fehlgeschlagen: %s", exc)
    finally:
        db.close()
