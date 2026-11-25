"""Profilbilder und Chat-Fotos: Links mit geheimem Schluessel.

Bilder werden ueber <img src=...> geladen - dabei kann der Browser keinen
Login-Header mitschicken. Darum bekommt jedes Bild einen Link mit einem
nicht erratbaren Schluessel (HMAC), wie bei WhatsApp-Mediendateien.
"""
import hashlib
import hmac
from typing import Dict, Iterable, Optional

from sqlalchemy.orm import Session

from app import models
from app.config import settings


def media_key(kind: str, ident: int, version: str = "") -> str:
    msg = f"{kind}:{ident}:{version}".encode()
    return hmac.new(settings.secret_key.encode(), msg, hashlib.sha256).hexdigest()[:24]


def check_key(kind: str, ident: int, key: str, version: str = "") -> bool:
    return hmac.compare_digest(media_key(kind, ident, version), key or "")


def _ver(dt) -> str:
    return str(int(dt.timestamp())) if dt else "0"


def avatar_urls(db: Session, user_ids: Optional[Iterable[int]] = None) -> Dict[int, str]:
    q = db.query(models.UserAvatar.user_id, models.UserAvatar.updated_at)
    if user_ids is not None:
        ids = list({int(i) for i in user_ids if i})
        if not ids:
            return {}
        q = q.filter(models.UserAvatar.user_id.in_(ids))
    return {uid: f"/avatars/{uid}?v={_ver(at)}&k={media_key('av', uid, _ver(at))}" for uid, at in q.all()}


def image_url(img_id: int) -> str:
    return f"/chat/img/{img_id}?k={media_key('img', img_id)}"
