"""Standard-Anmeldung wie bei Gump: wer sich nicht meldet, gilt als dabei (gruen).

Fuer alle kommenden Termine/Spiele (nicht abgesagt, naechste 120 Tage) bekommt
jedes Teammitglied ohne Antwort automatisch "dabei" / "kann spielen".
Automatische Eintraege erkennt man an set_at = NULL; sobald jemand selbst
antwortet, wird set_at gesetzt. Schaltet der Admin die Funktion aus, werden
nur die automatischen Eintraege wieder entfernt.
"""
import json
import time
from datetime import timedelta

from sqlalchemy import func
from sqlalchemy.orm import Session

from app import models

KEY = "default_attendance"
HORIZON_DAYS = 120
MAX_AGE_S = 300          # spaetestens alle 5 Minuten neu rechnen
_cache = {"fill_sig": None, "fill_at": 0.0, "gk_sig": None, "gk_at": 0.0, "gk": set()}


def invalidate() -> None:
    _cache.update(fill_sig=None, gk_sig=None)


def _fill_sig(db: Session):
    """Billiger Fingerabdruck: aendert sich, sobald Termine, Spiele oder Spieler dazukommen."""
    e = db.query(func.count(models.TeamEvent.id), func.max(models.TeamEvent.id)).one()
    g = db.query(func.count(models.Game.id), func.max(models.Game.id)).one()
    u = db.query(func.count(models.User.id), func.max(models.User.id)).filter(models.User.is_active == True).one()  # noqa: E712
    return (tuple(e), tuple(g), tuple(u))


def enabled(db: Session) -> bool:
    row = db.get(models.AppAsset, KEY)
    if row is None:
        return True                      # Standard: eingeschaltet
    try:
        return bool(json.loads(row.data.decode())["enabled"])
    except Exception:  # noqa: BLE001
        return True


def set_enabled(db: Session, on: bool) -> None:
    from app.services.agenda import now_local
    row = db.get(models.AppAsset, KEY)
    data = json.dumps({"enabled": bool(on)}).encode()
    if row is None:
        db.add(models.AppAsset(key=KEY, data=data, mime="application/json"))
    else:
        row.data = data
    db.commit()
    invalidate()
    if on:
        fill(db)
    else:   # nur automatische Eintraege kommender Termine entfernen
        now = now_local()
        ev_ids = [i for (i,) in db.query(models.TeamEvent.id).filter(models.TeamEvent.start_time > now)]
        g_ids = [i for (i,) in db.query(models.Game.id).filter(models.Game.start_time > now)]
        if ev_ids:
            db.query(models.EventResponse).filter(models.EventResponse.event_id.in_(ev_ids),
                                                  models.EventResponse.set_at == None).delete(synchronize_session=False)  # noqa: E711
        if g_ids:
            db.query(models.Availability).filter(models.Availability.game_id.in_(g_ids),
                                                 models.Availability.set_at == None).delete(synchronize_session=False)  # noqa: E711
        db.commit()


def fill(db: Session, force: bool = False) -> int:
    """Fehlende Antworten fuer kommende Termine automatisch auf 'dabei' setzen.
    Laeuft nur, wenn sich etwas geaendert hat oder 5 Minuten vergangen sind."""
    sig = _fill_sig(db)
    if not force and sig == _cache["fill_sig"] and time.time() - _cache["fill_at"] < MAX_AGE_S:
        return 0
    _cache.update(fill_sig=sig, fill_at=time.time())
    if not enabled(db):
        return 0
    from app.services.agenda import now_local, team_members
    now = now_local()
    end = now + timedelta(days=HORIZON_DAYS)
    members = [u.id for u in team_members(db)]
    if not members:
        return 0
    added = 0
    events = (db.query(models.TeamEvent.id).filter(models.TeamEvent.start_time > now, models.TeamEvent.start_time < end,
                                                   models.TeamEvent.is_cancelled == False).all())  # noqa: E712
    ev_ids = [i for (i,) in events]
    if ev_ids:
        have = {(e, u) for e, u in db.query(models.EventResponse.event_id, models.EventResponse.user_id)
                .filter(models.EventResponse.event_id.in_(ev_ids))}
        for e in ev_ids:
            for u in members:
                if (e, u) not in have:
                    db.add(models.EventResponse(event_id=e, user_id=u, status="zusage", set_at=None))
                    added += 1
    g_ids = [i for (i,) in db.query(models.Game.id).filter(models.Game.start_time > now, models.Game.start_time < end)]
    if g_ids:
        have = {(g, u): s for g, u, s in db.query(models.Availability.game_id, models.Availability.user_id, models.Availability.status)
                .filter(models.Availability.game_id.in_(g_ids))}
        for g in g_ids:
            for u in members:
                s = have.get((g, u))
                if s is None:
                    db.add(models.Availability(game_id=g, user_id=u, status=models.AvailabilityStatus.KANN_SPIELEN, set_at=None))
                    added += 1
    if added:
        db.commit()
    return added


def goalie_ids(db: Session) -> set:
    """Torhueter: von Hand gesetzt, sonst automatisch aus den Spielstatistiken (Paraden)."""
    from app.services.agenda import team_members
    sig = (db.query(func.count(models.GameReport.id), func.max(models.GameReport.id),
                    func.sum(func.length(models.GameReport.stats_json))).one(),
           db.query(func.count(models.User.id), func.max(models.User.id)).filter(models.User.is_active == True).one())  # noqa: E712
    sig = (tuple(sig[0]), tuple(sig[1]))
    if sig == _cache["gk_sig"] and time.time() - _cache["gk_at"] < MAX_AGE_S:
        return set(_cache["gk"])
    members = team_members(db)
    manual = {r.user_id: r.goalie for r in db.query(models.PlayerRole).all() if r.goalie is not None}
    auto_names = set()
    for (sj,) in db.query(models.GameReport.stats_json).filter(models.GameReport.stats_json != None).all():  # noqa: E711
        try:
            for row in json.loads(sj) or []:
                if row.get("paraden") not in (None, "", "-") and row.get("name"):
                    auto_names.add(" ".join(sorted(row["name"].lower().split())))
        except Exception:  # noqa: BLE001
            continue
    out = set()
    for u in members:
        if u.staff:
            continue
        if u.id in manual:
            if manual[u.id]:
                out.add(u.id)
        elif " ".join(sorted((u.name or "").lower().split())) in auto_names:
            out.add(u.id)
    _cache.update(gk_sig=sig, gk_at=time.time(), gk=set(out))
    return out
