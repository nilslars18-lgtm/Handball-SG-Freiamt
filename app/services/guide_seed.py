"""Anleitung fuer Spieler: einmalig als Word-Datei unter Dokumente → Anleitung ablegen.
Neue Version (VERSION erhoehen) ersetzt die alte Datei."""
import logging
from pathlib import Path

from app import models
from app.database import SessionLocal

VERSION = 2
FOLDER = "Anleitung"
FILE = Path(__file__).resolve().parent.parent / "seed" / "Anleitung_Team-App_U19_Spieler.docx"
TITLE = "Anleitung Team-App (Word)"


def apply_guide_seed() -> None:
    marker = f"seed_guide_v{VERSION}"
    db = SessionLocal()
    try:
        if db.get(models.AppAsset, marker) or not FILE.exists():
            return
        folder = (db.query(models.DocFolder)
                  .filter(models.DocFolder.parent_id == None, models.DocFolder.name == FOLDER).first())  # noqa: E711
        if folder is None:
            folder = models.DocFolder(name=FOLDER)
            db.add(folder)
            db.flush()
        db.query(models.DocItem).filter(models.DocItem.folder_id == folder.id, models.DocItem.title == TITLE).delete()
        data = FILE.read_bytes()
        db.add(models.DocItem(folder_id=folder.id, title=TITLE, kind="file", filename=FILE.name, size=len(data),
                              mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document", data=data))
        db.add(models.AppAsset(key=marker, data=b"1", mime="text/plain"))
        db.commit()
    except Exception as exc:  # darf den Start nie verhindern
        db.rollback()
        logging.getLogger("uvicorn").warning("Anleitung-Import fehlgeschlagen: %s", exc)
    finally:
        db.close()
