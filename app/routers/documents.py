"""Kachel "Dokumente": Ordnerstruktur mit Dateien und Info-Eintraegen.
Ansehen/Herunterladen: alle. Bearbeiten: Admin/Trainer und Captain."""
from typing import Optional
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app import models
from app.database import SessionLocal, get_db
from app.deps import get_current_user, require_captain_or_admin
from app.services.images import compress_image

router = APIRouter(prefix="/docs", tags=["dokumente"])

MAX_FILE_BYTES = 10 * 1024 * 1024  # 10 MB pro Datei
DEFAULT_FOLDERS = ["Vorlagen", "Verein", "Team"]


def seed_default_folders() -> None:
    db = SessionLocal()
    try:
        if db.query(models.DocFolder).count() == 0:
            for name in DEFAULT_FOLDERS:
                db.add(models.DocFolder(name=name))
            db.commit()
    finally:
        db.close()


class FolderIn(BaseModel):
    name: str
    parent_id: Optional[int] = None


class InfoIn(BaseModel):
    folder_id: int
    title: str
    text: str


class InfoPatch(BaseModel):
    title: Optional[str] = None
    text: Optional[str] = None


def _item_out(i: models.DocItem) -> dict:
    return {
        "id": i.id,
        "folder_id": i.folder_id,
        "title": i.title,
        "kind": i.kind,
        "filename": i.filename,
        "mime": i.mime,
        "size": i.size,
        "text": i.text if i.kind == "info" else None,
        "created_at": i.created_at,
    }


@router.get("/tree")
def get_tree(db: Session = Depends(get_db), user: models.User = Depends(get_current_user)):
    folders = db.query(models.DocFolder).order_by(models.DocFolder.name).all()
    items = (
        db.query(
            models.DocItem.id, models.DocItem.folder_id, models.DocItem.title, models.DocItem.kind,
            models.DocItem.filename, models.DocItem.mime, models.DocItem.size, models.DocItem.text,
            models.DocItem.created_at,
        )
        .order_by(models.DocItem.title)
        .all()
    )
    can_edit = user.is_trainer or user.is_captain
    return {
        "can_edit": can_edit,
        "folders": [{"id": f.id, "name": f.name, "parent_id": f.parent_id} for f in folders],
        "items": [_item_out(i) for i in items],
    }


@router.post("/folders")
def create_folder(body: FolderIn, db: Session = Depends(get_db), user=Depends(require_captain_or_admin)):
    name = body.name.strip()
    if not name:
        raise HTTPException(400, "Ordnername fehlt")
    if body.parent_id and not db.get(models.DocFolder, body.parent_id):
        raise HTTPException(404, "Oberordner nicht gefunden")
    f = models.DocFolder(name=name, parent_id=body.parent_id)
    db.add(f)
    db.commit()
    return {"id": f.id}


@router.patch("/folders/{folder_id}")
def rename_folder(folder_id: int, body: FolderIn, db: Session = Depends(get_db), user=Depends(require_captain_or_admin)):
    f = db.get(models.DocFolder, folder_id)
    if not f:
        raise HTTPException(404, "Ordner nicht gefunden")
    f.name = body.name.strip() or f.name
    db.commit()
    return {"ok": True}


@router.delete("/folders/{folder_id}")
def delete_folder(folder_id: int, db: Session = Depends(get_db), user=Depends(require_captain_or_admin)):
    f = db.get(models.DocFolder, folder_id)
    if not f:
        raise HTTPException(404, "Ordner nicht gefunden")
    has_children = db.query(models.DocFolder).filter(models.DocFolder.parent_id == folder_id).count()
    has_items = db.query(models.DocItem).filter(models.DocItem.folder_id == folder_id).count()
    if has_children or has_items:
        raise HTTPException(400, "Ordner ist nicht leer")
    db.delete(f)
    db.commit()
    return {"ok": True}


@router.post("/items/file")
async def upload_file(
    folder_id: int = Form(...),
    title: str = Form(""),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    user: models.User = Depends(require_captain_or_admin),
):
    if not db.get(models.DocFolder, folder_id):
        raise HTTPException(404, "Ordner nicht gefunden")
    raw = await file.read()
    if len(raw) > MAX_FILE_BYTES:
        raise HTTPException(400, "Datei ist grösser als 10 MB")
    mime = file.content_type or "application/octet-stream"
    filename = file.filename or "datei"
    if mime.startswith("image/") and mime != "image/svg+xml":
        try:
            raw = compress_image(raw)
            mime = "image/jpeg"
            filename = filename.rsplit(".", 1)[0] + ".jpg"
        except Exception:
            pass  # unbekanntes Bildformat -> Original speichern
    item = models.DocItem(
        folder_id=folder_id, title=title.strip() or filename, kind="file", filename=filename,
        mime=mime, size=len(raw), data=raw, created_by_id=user.id,
    )
    db.add(item)
    db.commit()
    return {"id": item.id}


@router.post("/items/info")
def create_info(body: InfoIn, db: Session = Depends(get_db), user=Depends(require_captain_or_admin)):
    if not db.get(models.DocFolder, body.folder_id):
        raise HTTPException(404, "Ordner nicht gefunden")
    item = models.DocItem(folder_id=body.folder_id, title=body.title.strip() or "Info", kind="info",
                          text=body.text, created_by_id=user.id)
    db.add(item)
    db.commit()
    return {"id": item.id}


@router.patch("/items/{item_id}")
def update_item(item_id: int, body: InfoPatch, db: Session = Depends(get_db), user=Depends(require_captain_or_admin)):
    item = db.get(models.DocItem, item_id)
    if not item:
        raise HTTPException(404, "Eintrag nicht gefunden")
    if body.title is not None and body.title.strip():
        item.title = body.title.strip()
    if body.text is not None and item.kind == "info":
        item.text = body.text
    db.commit()
    return {"ok": True}


@router.delete("/items/{item_id}")
def delete_item(item_id: int, db: Session = Depends(get_db), user=Depends(require_captain_or_admin)):
    item = db.get(models.DocItem, item_id)
    if not item:
        raise HTTPException(404, "Eintrag nicht gefunden")
    db.delete(item)
    db.commit()
    return {"ok": True}


@router.get("/items/{item_id}/download")
def download_item(item_id: int, db: Session = Depends(get_db), user=Depends(get_current_user)):
    item = db.get(models.DocItem, item_id)
    if not item or item.kind != "file" or item.data is None:
        raise HTTPException(404, "Datei nicht gefunden")
    return Response(
        content=item.data,
        media_type=item.mime or "application/octet-stream",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(item.filename or 'datei')}"},
    )
