"""Wer schreibt den Spielbericht?

Regeln:
- In Frage kommen nur aktive Spieler, die fuer dieses Spiel AUFGEBOTEN sind.
- Es wird reihum verteilt: dran ist, wer bisher am wenigsten Berichte hat.
  Bei Gleichstand der, dessen letzter Bericht am laengsten her ist (oder der
  noch nie einen hatte), danach alphabetisch.
- Neue Spieler kommen automatisch in die Rotation, geloeschte (inaktive)
  Spieler fallen raus - ein noch nicht geschriebener Bericht wird dann neu
  vergeben.
- Fertige Berichte und vom Trainer fix gesetzte Schreiber bleiben stehen.
"""
from datetime import datetime
from typing import Dict, List, Optional

from sqlalchemy.orm import Session

from app import models


def _eligible_players(db: Session, game: models.Game) -> List[models.User]:
    rows = (
        db.query(models.User)
        .join(models.Aufgebot, models.Aufgebot.user_id == models.User.id)
        .filter(
            models.Aufgebot.game_id == game.id,
            models.Aufgebot.status == models.AufgebotStatus.AUFGEBOTEN,
            models.User.is_active == True,  # noqa: E712
            models.User.base_role == models.BaseRole.PLAYER,
            ~models.User.staff.has(),
        )
        .all()
    )
    return rows


def sync_assignments(db: Session) -> None:
    games = db.query(models.Game).order_by(models.Game.start_time).all()
    reports: Dict[int, models.GameReport] = {
        r.game_id: r for r in db.query(models.GameReport).all()
    }

    count: Dict[int, int] = {}
    last: Dict[int, datetime] = {}
    changed = False

    def tally(user_id: Optional[int], when: datetime) -> None:
        if user_id is None:
            return
        count[user_id] = count.get(user_id, 0) + 1
        last[user_id] = when

    for game in games:
        report = reports.get(game.id)

        # Fertige oder manuell gesetzte Berichte bleiben, zaehlen aber mit
        if report and report.writer_id and (report.is_done or report.writer_manual):
            if report.is_done or (report.writer and report.writer.is_active):
                tally(report.writer_id, game.start_time)
                continue

        eligible = _eligible_players(db, game)
        eligible_ids = {u.id for u in eligible}

        # Bereits gespielte Spiele behalten ihren Schreiber (der weiss Bescheid).
        # Kommende Spiele werden jedes Mal neu ausbalanciert, damit z.B. ein
        # vom Trainer fix gesetzter Schreiber nicht doppelt drankommt.
        is_future = game.start_time > datetime.utcnow()
        if report and report.writer_id in eligible_ids and not is_future:
            tally(report.writer_id, game.start_time)
            continue

        if not eligible:
            # Noch kein Aufgebot -> noch niemand eingeteilt
            if report and report.writer_id and not report.is_done:
                report.writer_id = None
                report.writer_assigned_at = None
                changed = True
            continue

        chosen = min(
            eligible,
            key=lambda u: (count.get(u.id, 0), last.get(u.id, datetime.min), u.name.lower()),
        )
        if report is None:
            report = models.GameReport(game_id=game.id)
            db.add(report)
            reports[game.id] = report
        if report.writer_id == chosen.id and not report.writer_manual:
            tally(chosen.id, game.start_time)
            continue
        report.writer_id = chosen.id
        report.writer_assigned_at = datetime.utcnow()
        report.writer_manual = False
        changed = True
        tally(chosen.id, game.start_time)

    if changed:
        db.commit()


def rotation_overview(db: Session) -> List[dict]:
    """Pro aktivem Spieler: wie viele Berichte geschrieben / eingeteilt."""
    players = (
        db.query(models.User)
        .filter(models.User.is_active == True, models.User.base_role == models.BaseRole.PLAYER,  # noqa: E712
                ~models.User.staff.has())
        .order_by(models.User.name)
        .all()
    )
    reports = db.query(models.GameReport).filter(models.GameReport.writer_id.isnot(None)).all()
    out = []
    for p in players:
        mine = [r for r in reports if r.writer_id == p.id]
        done = [r for r in mine if r.is_done]
        open_ = [r for r in mine if not r.is_done]
        last_done = max((r.game.start_time for r in done), default=None)
        out.append(
            {
                "user_id": p.id,
                "name": p.name,
                "written": len(done),
                "open": len(open_),
                "last_written": last_done,
            }
        )
    out.sort(key=lambda x: (x["written"] + x["open"], x["last_written"] or datetime.min, x["name"].lower()))
    return out
