"""Vereinslogo: der Admin laedt es in der Verwaltung hoch, die App zeigt es
oben im Kopfbereich, als Browser-Icon und im Spielbericht."""
import io
from datetime import datetime

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import Response
from PIL import Image, ImageOps
from sqlalchemy.orm import Session

from app import models
from app.database import get_db
from app.deps import require_admin
from pathlib import Path

DEFAULT_LOGO = Path(__file__).resolve().parent.parent / "static" / "logo.png"

router = APIRouter(tags=["logo"])


def get_logo_bytes(db: Session):
    a = db.get(models.AppAsset, "logo")
    if a:
        return a.data
    return DEFAULT_LOGO.read_bytes() if DEFAULT_LOGO.exists() else None


@router.get("/logo")
def get_logo(db: Session = Depends(get_db)):
    # Hochgeladenes Logo hat Vorrang, sonst das mitgelieferte SG-Freiamt-PLUS-Wappen
    a = db.get(models.AppAsset, "logo")
    if a:
        return Response(content=a.data, media_type=a.mime, headers={"Cache-Control": "public, max-age=300"})
    if DEFAULT_LOGO.exists():
        return Response(content=DEFAULT_LOGO.read_bytes(), media_type="image/png",
                        headers={"Cache-Control": "public, max-age=3600"})
    raise HTTPException(404, "Kein Logo")


@router.post("/logo")
async def upload_logo(file: UploadFile = File(...), db: Session = Depends(get_db), admin=Depends(require_admin)):
    raw = await file.read()
    if len(raw) > 10 * 1024 * 1024:
        raise HTTPException(400, "Datei ist grösser als 10 MB")
    try:
        img = ImageOps.exif_transpose(Image.open(io.BytesIO(raw)))
        img = img.convert("RGBA")  # Transparenz behalten
        img.thumbnail((512, 512))
        out = io.BytesIO()
        img.save(out, format="PNG", optimize=True)
    except Exception:
        raise HTTPException(400, "Bitte ein Bild hochladen (PNG oder JPG)")
    a = db.get(models.AppAsset, "logo")
    if a is None:
        a = models.AppAsset(key="logo", data=out.getvalue(), mime="image/png")
        db.add(a)
    else:
        a.data, a.mime = out.getvalue(), "image/png"
    a.updated_at = datetime.utcnow()
    db.commit()
    return {"ok": True}


@router.delete("/logo")
def delete_logo(db: Session = Depends(get_db), admin=Depends(require_admin)):
    a = db.get(models.AppAsset, "logo")
    if a:
        db.delete(a)
        db.commit()
    return {"ok": True}
