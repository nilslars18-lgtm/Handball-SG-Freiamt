"""Web-Push (Benachrichtigungen aufs Handy) ohne Zusatz-Bibliothek.

- VAPID-Schluessel werden beim ersten Start erzeugt und in der Datenbank
  (AppAsset "vapid_private") gespeichert - nichts muss auf Render eingestellt
  werden, und die Abos bleiben auch nach einem Neustart gueltig.
- Verschluesselung nach RFC 8291 (aes128gcm) + VAPID (RFC 8292) mit
  `cryptography` (ist ueber python-jose bereits installiert).
- Versand laeuft in einem Hintergrund-Thread, damit Klicks in der App nicht
  auf die Push-Server warten muessen.
"""
import base64
import hashlib
import hmac
import json
import logging
import os
import struct
import threading
import time
from datetime import datetime
from typing import Iterable, List, Optional
from urllib.parse import urlparse

import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from sqlalchemy.orm import Session

from app import models

log = logging.getLogger("webpush")

VAPID_KEY = "vapid_private"
VAPID_SUB = "mailto:nils@sgfreiamt.ch"

# Kategorien, die jeder Spieler einzeln ein-/ausschalten kann
CATEGORIES = {
    "aufgebot": "Aufgebot für Spiele",
    "erinnerung": "Erinnerungen zum Eintragen",
    "termine": "Neue, verschobene und abgesagte Termine",
    "aemtli": "Ämtli (am Vortag)",
    "bericht": "Spielbericht (wenn du dran bist)",
    "bussen": "Bussen und Beschwerden",
    "essen": "Essen nach dem Spiel (Umfrage)",
    "teaminfo": "📢 Team-Info (Nachrichten der Trainer)",
    "chat": "💬 Chat-Nachrichten (Spieler-Chat und Direktnachrichten)",
}
ADMIN_CATEGORIES = {
    "abmeldungen": "Ab- und Verletztmeldungen der Spieler",
    "statistik": "Statistik nach dem Spiel ist da",
}
OWNER_CATEGORIES = {"logins": "Neue Spieler-Logins (mit Passwort)"}

_key_cache = {}


def b64u(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def b64u_dec(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


# ---------------------------------------------------------------------------
# Schluessel
# ---------------------------------------------------------------------------
def _private_key(db: Session) -> ec.EllipticCurvePrivateKey:
    if "k" in _key_cache:
        return _key_cache["k"]
    pem = os.environ.get("VAPID_PRIVATE_PEM")
    if not pem:
        row = db.get(models.AppAsset, VAPID_KEY)
        if row is None:
            key = ec.generate_private_key(ec.SECP256R1())
            data = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                     serialization.NoEncryption())
            row = models.AppAsset(key=VAPID_KEY, data=data, mime="application/x-pem-file")
            db.add(row)
            db.commit()
        pem = row.data.decode() if isinstance(row.data, (bytes, bytearray)) else row.data
    key = serialization.load_pem_private_key(pem.encode() if isinstance(pem, str) else pem, password=None)
    _key_cache["k"] = key
    return key


def _raw_public(key) -> bytes:
    return key.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)


def public_key_b64(db: Session) -> str:
    return b64u(_raw_public(_private_key(db)))


def _vapid_header(key, endpoint: str) -> str:
    u = urlparse(endpoint)
    aud = f"{u.scheme}://{u.netloc}"
    header = b64u(json.dumps({"typ": "JWT", "alg": "ES256"}, separators=(",", ":")).encode())
    claims = b64u(json.dumps({"aud": aud, "exp": int(time.time()) + 12 * 3600, "sub": VAPID_SUB},
                             separators=(",", ":")).encode())
    signing_input = f"{header}.{claims}".encode()
    der = key.sign(signing_input, ec.ECDSA(hashes.SHA256()))
    r, s = decode_dss_signature(der)
    sig = r.to_bytes(32, "big") + s.to_bytes(32, "big")
    jwt = f"{header}.{claims}.{b64u(sig)}"
    return f"vapid t={jwt}, k={b64u(_raw_public(key))}"


# ---------------------------------------------------------------------------
# Verschluesselung (RFC 8291, aes128gcm)
# ---------------------------------------------------------------------------
def _hmac(key: bytes, data: bytes) -> bytes:
    return hmac.new(key, data, hashlib.sha256).digest()


def encrypt(payload: bytes, p256dh_b64: str, auth_b64: str, salt: Optional[bytes] = None,
            as_key: Optional[ec.EllipticCurvePrivateKey] = None) -> bytes:
    ua_public = b64u_dec(p256dh_b64)
    auth_secret = b64u_dec(auth_b64)
    as_key = as_key or ec.generate_private_key(ec.SECP256R1())
    as_public = _raw_public(as_key)
    ua_key = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), ua_public)
    ecdh_secret = as_key.exchange(ec.ECDH(), ua_key)
    prk_key = _hmac(auth_secret, ecdh_secret)
    ikm = _hmac(prk_key, b"WebPush: info\x00" + ua_public + as_public + b"\x01")
    salt = salt or os.urandom(16)
    prk = _hmac(salt, ikm)
    cek = _hmac(prk, b"Content-Encoding: aes128gcm\x00\x01")[:16]
    nonce = _hmac(prk, b"Content-Encoding: nonce\x00\x01")[:12]
    ciphertext = AESGCM(cek).encrypt(nonce, payload + b"\x02", None)
    header = salt + struct.pack("!L", 4096) + bytes([len(as_public)]) + as_public
    return header + ciphertext


def send_one(db: Session, sub: models.PushSubscription, data: dict, ttl: int = 86400) -> int:
    key = _private_key(db)
    body = encrypt(json.dumps(data).encode(), sub.p256dh, sub.auth)
    headers = {
        "Authorization": _vapid_header(key, sub.endpoint),
        "Content-Encoding": "aes128gcm",
        "Content-Type": "application/octet-stream",
        "TTL": str(ttl),
        "Urgency": "normal",
    }
    r = httpx.post(sub.endpoint, content=body, headers=headers, timeout=15)
    return r.status_code


# ---------------------------------------------------------------------------
# Wer will was?
# ---------------------------------------------------------------------------
def wants(db: Session, user_id: int, category: str) -> bool:
    p = db.get(models.NotifyPref, (user_id, category))
    return True if p is None else bool(p.enabled)


def _deliver(user_ids: List[int], data: dict, category: Optional[str]) -> None:
    from app.database import SessionLocal
    db = SessionLocal()
    try:
        _private_key(db)
        for uid in user_ids:
            if category and not wants(db, uid, category):
                continue
            for sub in db.query(models.PushSubscription).filter(models.PushSubscription.user_id == uid).all():
                try:
                    code = send_one(db, sub, data)
                    if code in (404, 410):
                        db.delete(sub)          # Abo existiert nicht mehr (App geloescht, Handy gewechselt)
                    elif code < 300:
                        sub.last_ok_at = datetime.utcnow()
                    else:
                        log.warning("push %s -> %s", sub.endpoint[:60], code)
                except Exception as e:  # noqa: BLE001
                    log.warning("push failed: %s", e)
            db.commit()
    finally:
        db.close()


def notify(user_ids: Iterable[int], title: str, body: str, url: str = "/", category: Optional[str] = None,
           tag: Optional[str] = None, wait: bool = False) -> None:
    """Benachrichtigung an einzelne Spieler. Laeuft im Hintergrund."""
    ids = sorted({int(u) for u in user_ids if u})
    if not ids:
        return
    data = {"title": title, "body": body, "url": url, "tag": tag or category or "info"}
    if wait:
        _deliver(ids, data, category)
    else:
        _ensure_worker()
        _queue.put((ids, data, category))


# Ein einziger Hintergrund-Thread arbeitet die Meldungen nacheinander ab
# (sonst wuerde jede Meldung eine eigene DB-Verbindung belegen).
import queue as _queue_mod  # noqa: E402

_queue: "_queue_mod.Queue" = _queue_mod.Queue()
_worker = {"t": None}
_worker_lock = threading.Lock()


def _ensure_worker() -> None:
    with _worker_lock:
        if _worker["t"] is not None and _worker["t"].is_alive():
            return

        def run():
            while True:
                ids, data, category = _queue.get()
                try:
                    _deliver(ids, data, category)
                except Exception as e:  # noqa: BLE001
                    log.warning("push worker: %s", e)
                finally:
                    _queue.task_done()

        _worker["t"] = threading.Thread(target=run, daemon=True)
        _worker["t"].start()


def notify_admins(db: Session, title: str, body: str, url: str = "/", category: str = "abmeldungen",
                  tag: Optional[str] = None) -> None:
    ids = [u.id for u in db.query(models.User).filter(models.User.is_active == True).all() if u.is_trainer]  # noqa: E712
    notify(ids, title, body, url, category, tag)


def once(db: Session, key: str) -> bool:
    """True, wenn diese automatische Meldung noch nie verschickt wurde (und merkt sie sich)."""
    if db.get(models.PushSent, key):
        return False
    db.add(models.PushSent(key=key))
    db.commit()
    return True


WD = ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"]


def fmt_when(dt) -> str:
    return f"{WD[dt.weekday()]} {dt:%d.%m.} {dt:%H:%M}"


def player_ids(db: Session) -> List[int]:
    from app.services.agenda import team_members
    return [p.id for p in team_members(db)]
