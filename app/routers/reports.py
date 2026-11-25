"""Spielberichte: Rotation (wer schreibt) + Assistent (Spiel waehlen, Teamfoto,
Statistik) + fertige Word-Datei zum Herunterladen."""
import json
from datetime import datetime
from typing import List, Optional
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app import models
from app.database import get_db
from app.deps import get_current_user, require_admin
from app.routers.branding import get_logo_bytes
from app.services.images import compress_image
from app.services.report_docx import build_report_docx
from app.services.report_rotation import rotation_overview, sync_assignments

router = APIRouter(prefix="/reports", tags=["spielberichte"])

DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


class StatRow(BaseModel):
    name: str
    tore: Optional[int] = None
    siebenm: Optional[str] = None   # z.B. "2/3"
    zwei_min: Optional[int] = None
    gelb: Optional[int] = None
    nr: Optional[int] = None
    wuerfe: Optional[str] = None    # "5/7" (von handball.ch)
    paraden: Optional[str] = None   # Torhueter "7/23"
    tf: Optional[str] = None        # technische Fehler
    rot: Optional[int] = None


class ReportIn(BaseModel):
    score_home: Optional[int] = None
    score_away: Optional[int] = None
    half_home: Optional[int] = None
    half_away: Optional[int] = None
    stats: Optional[List[StatRow]] = None
    text: Optional[str] = None
    is_done: Optional[bool] = None


class WriterIn(BaseModel):
    user_id: Optional[int] = None  # None = wieder automatisch


def _can_edit(user: models.User, report: Optional[models.GameReport]) -> bool:
    if user.is_trainer or user.is_captain:
        return True
    return bool(report and report.writer_id == user.id)


def _get_game(db: Session, game_id: int) -> models.Game:
    g = db.get(models.Game, game_id)
    if not g:
        raise HTTPException(404, "Spiel nicht gefunden")
    return g


def _get_or_create(db: Session, game_id: int) -> models.GameReport:
    r = db.query(models.GameReport).filter(models.GameReport.game_id == game_id).first()
    if r is None:
        r = models.GameReport(game_id=game_id)
        db.add(r)
        db.flush()
    return r


def _filename(game: models.Game) -> str:
    opp = "".join(c for c in game.opponent if c.isalnum() or c in " -_").strip().replace(" ", "_")
    return f"Spielbericht_{game.start_time.strftime('%Y-%m-%d')}_{opp or 'Spiel'}.docx"


@router.get("/template.docx")
def empty_template(db: Session = Depends(get_db), user=Depends(get_current_user)):
    data = build_report_docx(None, None, get_logo_bytes(db))
    return Response(content=data, media_type=DOCX_MIME,
                    headers={"Content-Disposition": "attachment; filename=Spielbericht_Vorlage.docx"})


@router.get("")
def list_reports(db: Session = Depends(get_db), user: models.User = Depends(get_current_user)):
    """Alle Spiele mit Schreiber-Info. Neueste/naechste zuerst sortiert vom Frontend."""
    sync_assignments(db)
    reports = {r.game_id: r for r in db.query(models.GameReport).all()}
    now = datetime.utcnow()
    out = []
    for g in db.query(models.Game).order_by(models.Game.start_time).all():
        r = reports.get(g.id)
        out.append({
            "game_id": g.id,
            "opponent": g.opponent,
            "is_home": g.is_home,
            "start_time": g.start_time,
            "is_past": g.start_time < now,
            "writer_id": r.writer_id if r else None,
            "writer_name": r.writer.name if r and r.writer else None,
            "writer_manual": bool(r and r.writer_manual),
            "is_done": bool(r and r.is_done),
            "has_photo": bool(r and r.team_photo),
            "has_stats": bool(r and (r.stats_json or r.stats_image)),
            "is_mine": bool(r and r.writer_id == user.id),
        })
    return out


@router.get("/rotation")
def get_rotation(db: Session = Depends(get_db), user=Depends(get_current_user)):
    sync_assignments(db)
    return rotation_overview(db)


@router.get("/{game_id}")
def get_report(game_id: int, db: Session = Depends(get_db), user: models.User = Depends(get_current_user)):
    sync_assignments(db)
    g = _get_game(db, game_id)
    r = db.query(models.GameReport).filter(models.GameReport.game_id == game_id).first()
    aufgebotene = (
        db.query(models.User.name)
        .join(models.Aufgebot, models.Aufgebot.user_id == models.User.id)
        .filter(models.Aufgebot.game_id == game_id,
                models.Aufgebot.status == models.AufgebotStatus.AUFGEBOTEN,
                models.User.is_active == True)  # noqa: E712
        .order_by(models.User.name)
        .all()
    )
    stats = json.loads(r.stats_json) if r and r.stats_json else []
    return {
        "game_id": g.id,
        "opponent": g.opponent,
        "is_home": g.is_home,
        "start_time": g.start_time,
        "location_name": g.location_name,
        "writer_id": r.writer_id if r else None,
        "writer_name": r.writer.name if r and r.writer else None,
        "writer_manual": bool(r and r.writer_manual),
        "score_home": r.score_home if r else None,
        "score_away": r.score_away if r else None,
        "half_home": r.half_home if r else None,
        "half_away": r.half_away if r else None,
        "stats": stats,
        "text": r.text if r else None,
        "is_done": bool(r and r.is_done),
        "has_photo": bool(r and r.team_photo),
        "has_stats_image": bool(r and r.stats_image),
        "aufgebotene": [n for (n,) in aufgebotene],
        "can_edit": _can_edit(user, r),
    }


@router.put("/{game_id}")
def save_report(game_id: int, body: ReportIn, db: Session = Depends(get_db), user=Depends(get_current_user)):
    _get_game(db, game_id)
    r = _get_or_create(db, game_id)
    if not _can_edit(user, r):
        raise HTTPException(403, "Nur der eingeteilte Schreiber, Captain oder Trainer")
    data = body.model_dump(exclude_unset=True)
    for f in ("score_home", "score_away", "half_home", "half_away", "text", "is_done"):
        if f in data:
            setattr(r, f, data[f])
    if "stats" in data:
        rows = [s for s in (body.stats or []) if s.name.strip()]
        r.stats_json = json.dumps([s.model_dump() for s in rows], ensure_ascii=False) if rows else None
    r.updated_at = datetime.utcnow()
    db.commit()
    return {"ok": True}


@router.post("/{game_id}/image/{kind}")
async def upload_image(game_id: int, kind: str, file: UploadFile = File(...),
                       db: Session = Depends(get_db), user=Depends(get_current_user)):
    if kind not in ("team", "stats"):
        raise HTTPException(400, "kind muss team oder stats sein")
    _get_game(db, game_id)
    r = _get_or_create(db, game_id)
    if not _can_edit(user, r):
        raise HTTPException(403, "Nur der eingeteilte Schreiber, Captain oder Trainer")
    raw = await file.read()
    if len(raw) > 15 * 1024 * 1024:
        raise HTTPException(400, "Bild ist grösser als 15 MB")
    try:
        img = compress_image(raw, max_side=1600 if kind == "team" else 2000)
    except Exception:
        raise HTTPException(400, "Das ist kein lesbares Bild (JPG/PNG/HEIC-Screenshot als JPG speichern)")
    if kind == "team":
        r.team_photo = img
    else:
        r.stats_image = img
    r.updated_at = datetime.utcnow()
    db.commit()
    return {"ok": True, "size": len(img)}


@router.delete("/{game_id}/image/{kind}")
def delete_image(game_id: int, kind: str, db: Session = Depends(get_db), user=Depends(get_current_user)):
    r = db.query(models.GameReport).filter(models.GameReport.game_id == game_id).first()
    if not r:
        return {"ok": True}
    if not _can_edit(user, r):
        raise HTTPException(403, "Keine Berechtigung")
    if kind == "team":
        r.team_photo = None
    elif kind == "stats":
        r.stats_image = None
    db.commit()
    return {"ok": True}


@router.get("/{game_id}/image/{kind}")
def get_image(game_id: int, kind: str, db: Session = Depends(get_db), user=Depends(get_current_user)):
    r = db.query(models.GameReport).filter(models.GameReport.game_id == game_id).first()
    data = None
    if r:
        data = r.team_photo if kind == "team" else r.stats_image if kind == "stats" else None
    if not data:
        raise HTTPException(404, "Kein Bild")
    return Response(content=data, media_type="image/jpeg")


@router.put("/{game_id}/writer")
def set_writer(game_id: int, body: WriterIn, db: Session = Depends(get_db), admin=Depends(require_admin)):
    _get_game(db, game_id)
    r = _get_or_create(db, game_id)
    if body.user_id is None:
        r.writer_manual = False
        r.writer_id = None
    else:
        u = db.get(models.User, body.user_id)
        if not u or not u.is_active:
            raise HTTPException(404, "Spieler nicht gefunden")
        r.writer_id = u.id
        r.writer_manual = True
        r.writer_assigned_at = datetime.utcnow()
    db.commit()
    sync_assignments(db)
    return {"ok": True}


@router.get("/{game_id}/docx")
def download_docx(game_id: int, db: Session = Depends(get_db), user=Depends(get_current_user)):
    sync_assignments(db)
    g = _get_game(db, game_id)
    r = db.query(models.GameReport).filter(models.GameReport.game_id == game_id).first()
    data = build_report_docx(g, r, get_logo_bytes(db))
    return Response(content=data, media_type=DOCX_MIME,
                    headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(_filename(g))}"})
