"""Kachel "Fotos & Videos": Links (Google Drive, iCloud, YouTube ...) einem
Spiel zuordnen. Es werden keine Dateien gespeichert, nur Links."""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app import models
from app.database import get_db
from app.deps import get_current_user

router = APIRouter(prefix="/media", tags=["fotos-videos"])

VIDEO_HINTS = ("youtube.com", "youtu.be", "vimeo.com", "tiktok.com", ".mp4", ".mov", "video")


class MediaIn(BaseModel):
    url: str
    title: Optional[str] = None
    game_id: Optional[int] = None
    kind: Optional[str] = None  # "foto" | "video" - leer = automatisch erkennen


def _guess_kind(url: str) -> str:
    u = url.lower()
    return "video" if any(h in u for h in VIDEO_HINTS) else "foto"


@router.get("")
def list_media(game_id: Optional[int] = None, db: Session = Depends(get_db), user=Depends(get_current_user)):
    q = db.query(models.MediaLink)
    if game_id:
        q = q.filter(models.MediaLink.game_id == game_id)
    links = q.order_by(models.MediaLink.created_at.desc()).all()
    is_admin = user.is_trainer
    return [
        {
            "id": m.id,
            "url": m.url,
            "title": m.title,
            "kind": m.kind,
            "game_id": m.game_id,
            "game_opponent": m.game.opponent if m.game else None,
            "game_start": m.game.start_time if m.game else None,
            "game_is_home": m.game.is_home if m.game else None,
            "created_by_name": m.created_by.name if m.created_by else "",
            "created_at": m.created_at,
            "can_delete": is_admin or m.created_by_id == user.id,
        }
        for m in links
    ]


@router.post("")
def add_media(body: MediaIn, db: Session = Depends(get_db), user=Depends(get_current_user)):
    url = body.url.strip()
    if not (url.startswith("http://") or url.startswith("https://")):
        raise HTTPException(400, "Bitte einen gültigen Link einfügen (beginnt mit https://)")
    if body.game_id and not db.get(models.Game, body.game_id):
        raise HTTPException(404, "Spiel nicht gefunden")
    kind = body.kind if body.kind in ("foto", "video") else _guess_kind(url)
    m = models.MediaLink(url=url, title=(body.title or "").strip() or None, game_id=body.game_id,
                         kind=kind, created_by_id=user.id)
    db.add(m)
    db.commit()
    return {"id": m.id}


@router.delete("/{media_id}")
def delete_media(media_id: int, db: Session = Depends(get_db), user=Depends(get_current_user)):
    m = db.get(models.MediaLink, media_id)
    if not m:
        raise HTTPException(404, "Link nicht gefunden")
    if not user.is_trainer and m.created_by_id != user.id:
        raise HTTPException(403, "Nur wer den Link eingefügt hat (oder der Trainer) kann ihn löschen")
    db.delete(m)
    db.commit()
    return {"ok": True}
