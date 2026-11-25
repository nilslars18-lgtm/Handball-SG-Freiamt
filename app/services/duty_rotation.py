"""Aemtliliste: verteilt jedes Aemtli reihum auf die Spieler.

- Pro Aemtli wird separat gezaehlt: wer es in dieser Saison am wenigsten
  hatte, ist dran (bei Gleichstand: wer es am laengsten nicht mehr hatte).
- Wer sich abgemeldet hat (Spiel: verletzt/abwesend/nicht aufgeboten,
  Training: Absage), bekommt kein Aemtli.
- Vergangene und vom Trainer fix gesetzte Einteilungen bleiben stehen;
  kommende werden bei jeder Aenderung neu ausbalanciert.
- Verteilt wird nur fuer Termine in den naechsten LOOKAHEAD_DAYS Tagen.
"""
from datetime import datetime, timedelta
from typing import Dict, List, Tuple

from sqlalchemy.orm import Session

from app import models
from app.services.agenda import (
    active_players, event_declined_ids, game_declined_ids, now_local, season_bounds, season_of,
)

LOOKAHEAD_DAYS = 21


def _items(db: Session, start: datetime, end: datetime) -> List[Tuple[str, int, datetime, str, object]]:
    items = []
    for g in db.query(models.Game).filter(models.Game.start_time >= start, models.Game.start_time < end).all():
        items.append(("game", g.id, g.start_time, "spiel", g))
    for e in (
        db.query(models.TeamEvent)
        .filter(models.TeamEvent.start_time >= start, models.TeamEvent.start_time < end,
                models.TeamEvent.is_cancelled == False)  # noqa: E712
        .all()
    ):
        items.append(("event", e.id, e.start_time, e.kind, e))
    items.sort(key=lambda x: x[2])
    return items


def _applies(duty: models.Duty, kind: str) -> bool:
    if duty.applies_to == "beide":
        return kind in ("spiel", "training")
    if duty.applies_to == "training":
        return kind == "training"
    return kind == "spiel"


def sync_duties(db: Session) -> None:
    duties = db.query(models.Duty).filter(models.Duty.is_active == True).all()  # noqa: E712
    if not duties:
        return
    now = now_local()
    s_start, _ = season_bounds(season_of(now))
    items = _items(db, s_start, now + timedelta(days=LOOKAHEAD_DAYS))
    players = active_players(db)
    player_ids = {p.id for p in players}
    existing = {
        (a.duty_id, a.ref_kind, a.ref_id): a for a in db.query(models.DutyAssignment).all()
    }
    count: Dict[int, Dict[int, int]] = {d.id: {} for d in duties}
    last: Dict[int, Dict[int, datetime]] = {d.id: {} for d in duties}
    changed = False

    def tally(duty_id, uid, when):
        if uid:
            count[duty_id][uid] = count[duty_id].get(uid, 0) + 1
            last[duty_id][uid] = when

    for ref_kind, ref_id, start, kind, obj in items:
        is_past = start < now
        declined = None
        busy = set()  # wer an diesem Termin schon ein Aemtli hat
        # zuerst feste (vergangene/manuelle) Einteilungen dieses Termins erfassen
        todo = []
        for duty in duties:
            if not _applies(duty, kind):
                continue
            a = existing.get((duty.id, ref_kind, ref_id))
            if a and (is_past or (a.manual and a.user_id in player_ids)):
                tally(duty.id, a.user_id, start)
                if a.user_id:
                    busy.add(a.user_id)
            elif not is_past:
                todo.append((duty, a))
        for duty, a in todo:
            if declined is None:
                declined = game_declined_ids(db, obj) if ref_kind == "game" else event_declined_ids(db, obj)
            eligible = [p for p in players if p.id not in declined]
            chosen = None
            if eligible:
                c, l = count[duty.id], last[duty.id]
                # moeglichst niemand mit zwei Aemtli am gleichen Termin
                chosen = min(eligible, key=lambda p: (p.id in busy, c.get(p.id, 0), l.get(p.id, datetime.min), p.name.lower()))
            new_uid = chosen.id if chosen else None
            if a is None:
                a = models.DutyAssignment(duty_id=duty.id, ref_kind=ref_kind, ref_id=ref_id)
                db.add(a)
                existing[(duty.id, ref_kind, ref_id)] = a
                changed = True
            if a.user_id != new_uid or a.manual:
                a.user_id = new_uid
                a.manual = False
                changed = True
            tally(duty.id, new_uid, start)
            if new_uid:
                busy.add(new_uid)

    if changed:
        db.commit()


def duties_for(db: Session, ref_kind: str, ref_id: int) -> List[dict]:
    rows = (
        db.query(models.DutyAssignment)
        .join(models.Duty, models.Duty.id == models.DutyAssignment.duty_id)
        .filter(models.DutyAssignment.ref_kind == ref_kind, models.DutyAssignment.ref_id == ref_id,
                models.Duty.is_active == True)  # noqa: E712
        .all()
    )
    return [
        {"duty_id": a.duty_id, "duty": a.duty.name, "user_id": a.user_id,
         "user_name": a.user.name if a.user else None, "manual": a.manual}
        for a in rows
    ]
