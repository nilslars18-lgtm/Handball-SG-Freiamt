"""Einmalig: Trainer / TW-Trainer als Staff markieren (fehlende Logins anlegen).

Staff hat ein Login und Trainer-Rechte, zaehlt aber nicht als Spieler.
Wird pro Version nur einmal angewendet (Markierung in app_assets) - spaetere
Aenderungen macht der Admin in der App unter Verwaltung → Spieler verwalten.
"""
import logging

from sqlalchemy import func

from app import models
from app.database import SessionLocal

VERSION = 1
STAFF = [
    # (Name, Titel)
    ("Andreas Kohlreiter", "TW-Trainer"),
    ("Paul S", "Trainer"),
    ("Fabian Meier", "Trainer"),
]
NOT_STAFF = ["Andrin Meyer"]  # ist Spieler


def apply_staff_seed() -> None:
    marker = f"seed_staff_v{VERSION}"
    db = SessionLocal()
    try:
        if db.get(models.AppAsset, marker):
            return
        from app.routers.fine_types import _find_user
        from app.routers.handballch import make_password
        from app.security import hash_password

        for name, title in STAFF:
            u = _find_user(db, name)
            if u is None:
                login = name.split()[0].lower()
                cand, k = login, 2
                while db.query(models.User).filter(func.lower(models.User.email) == cand).first():
                    cand, k = f"{login}{k}", k + 1
                pw = make_password()
                u = models.User(name=name, email=cand, password_hash=hash_password(pw),
                                last_plaintext_password=pw, base_role=models.BaseRole.PLAYER)
                db.add(u)
                db.flush()
            row = db.get(models.StaffRole, u.id)
            if row is None:
                db.add(models.StaffRole(user_id=u.id, title=title))
            else:
                row.title = title
        for name in NOT_STAFF:
            u = _find_user(db, name)
            if u:
                db.query(models.StaffRole).filter(models.StaffRole.user_id == u.id).delete()
        db.add(models.AppAsset(key=marker, data=b"1", mime="text/plain"))
        db.commit()
    except Exception as exc:  # darf den Start nie verhindern
        db.rollback()
        logging.getLogger("uvicorn").warning("Staff-Import fehlgeschlagen: %s", exc)
    finally:
        db.close()
