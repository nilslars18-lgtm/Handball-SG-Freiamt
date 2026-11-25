"""Chat wie WhatsApp.

- "Team-Info": alle (Spieler, Trainer, Admin). Schreiben und Umfragen nur Trainer/Admin.
- "Spieler-Chat": nur Spieler (ohne Trainer/Admin). Alle duerfen schreiben und Umfragen machen.
- Direktnachrichten zwischen zwei Personen. Nur die beiden sehen sie (auch der Admin nicht).
- Wie WhatsApp: Antworten, Haken (gesendet/zugestellt/gelesen), Nachrichten-Info,
  "schreibt ...", online / zuletzt online, Fotos. "Zuletzt online" und
  Lesebestaetigungen kann jeder ausschalten (gilt gegenseitig, wie bei WhatsApp).
"""
import io
import threading
import time
from datetime import datetime, timedelta
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Response, UploadFile
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from app import models
from app.database import get_db
from app.deps import get_current_user
from app.services.avatars import avatar_urls, check_key, image_url

router = APIRouter(prefix="/chat", tags=["chat"])

ROOM_NAMES = {"team": "📢 Team-Info", "players": "👥 Spieler-Chat"}

# ---------------------------------------------------------------------------
# Online / schreibt ... (im Speicher; die App laeuft mit einem Prozess)
# ---------------------------------------------------------------------------
ONLINE_S = 45          # so lange gilt man nach dem letzten Lebenszeichen als online
TYPING_S = 6
_seen: Dict[int, float] = {}          # user_id -> time.time()
_seen_saved: Dict[int, float] = {}
_typing: Dict[int, Dict[int, float]] = {}   # room_id -> {user_id: ts}
_lock = threading.Lock()


def touch(db: Session, u: models.User) -> None:
    """Lebenszeichen: merkt 'online' und speichert 'zuletzt online' hoechstens jede Minute."""
    now = time.time()
    with _lock:
        _seen[u.id] = now
        if now - _seen_saved.get(u.id, 0) < 60:
            return
        _seen_saved[u.id] = now
    row = db.get(models.ChatPresence, u.id)
    if row is None:
        db.add(models.ChatPresence(user_id=u.id, last_seen=datetime.utcnow()))
    else:
        row.last_seen = datetime.utcnow()
    db.commit()


def _privacy(db: Session, uid: int) -> models.ChatPrivacy:
    return db.get(models.ChatPrivacy, uid) or models.ChatPrivacy(user_id=uid, show_last_seen=True, read_receipts=True)


def _presence(db: Session, me: models.User, other: models.User) -> dict:
    """online / zuletzt online - nur wenn beide 'Zuletzt online' zeigen (wie WhatsApp)."""
    ts = _seen.get(other.id)
    if ts and time.time() - ts < ONLINE_S:
        return {"online": True, "last_seen": None}
    if not (_privacy(db, me.id).show_last_seen and _privacy(db, other.id).show_last_seen):
        return {"online": False, "last_seen": None}
    if ts:
        return {"online": False, "last_seen": datetime.utcfromtimestamp(ts).isoformat(timespec="seconds") + "Z"}
    row = db.get(models.ChatPresence, other.id)
    return {"online": False, "last_seen": _iso(row.last_seen) if row and row.last_seen else None}


def _typing_names(db: Session, room_id: int, me: models.User) -> List[str]:
    now = time.time()
    with _lock:
        t = _typing.get(room_id, {})
        ids = [uid for uid, ts in t.items() if uid != me.id and now - ts < TYPING_S]
    if not ids:
        return []
    return [_display_name(u).split(" ")[0] for u in db.query(models.User).filter(models.User.id.in_(ids)).all()]


# ---------------------------------------------------------------------------
# Raeume und Rechte
# ---------------------------------------------------------------------------
def _group_room(db: Session, kind: str) -> models.ChatRoom:
    r = db.query(models.ChatRoom).filter(models.ChatRoom.kind == kind).first()
    if r is None:
        r = models.ChatRoom(kind=kind, name=ROOM_NAMES[kind])
        db.add(r)
        db.commit()
    return r


def _is_player(u: models.User) -> bool:
    return u.base_role == models.BaseRole.PLAYER and not u.staff


def _can_read(db: Session, room: models.ChatRoom, u: models.User) -> bool:
    if room.kind == "team":
        return True
    if room.kind == "players":
        return _is_player(u)
    return db.get(models.ChatMember, (room.id, u.id)) is not None


def _can_write(room: models.ChatRoom, u: models.User) -> bool:
    if room.kind == "team":
        return u.is_trainer
    return True


def _can_poll(room: models.ChatRoom, u: models.User) -> bool:
    return room.kind in ("team", "players") and _can_write(room, u)


def _room_or_404(db: Session, room_id: int, u: models.User) -> models.ChatRoom:
    room = db.get(models.ChatRoom, room_id)
    if room is None or not _can_read(db, room, u):
        raise HTTPException(404, "Chat nicht gefunden")
    return room


def _members(db: Session, room: models.ChatRoom) -> List[models.User]:
    active = db.query(models.User).filter(models.User.is_active == True).all()  # noqa: E712
    if room.kind == "team":
        return active
    if room.kind == "players":
        return [u for u in active if _is_player(u)]
    ids = {m.user_id for m in db.query(models.ChatMember).filter(models.ChatMember.room_id == room.id).all()}
    return [u for u in active if u.id in ids]


def _read_row(db: Session, room_id: int, user_id: int) -> models.ChatRead:
    r = db.get(models.ChatRead, (room_id, user_id))
    if r is None:
        r = models.ChatRead(room_id=room_id, user_id=user_id, last_read_id=0, muted=False)
        db.add(r)
        db.flush()
    return r


def _dm_partner(db: Session, room: models.ChatRoom, me: models.User) -> Optional[models.User]:
    for m in db.query(models.ChatMember).filter(models.ChatMember.room_id == room.id).all():
        if m.user_id != me.id:
            return db.get(models.User, m.user_id)
    return None


def _iso(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds") + "Z"


def _display_name(u: Optional[models.User]) -> str:
    if u is None:
        return "Gelöscht"
    if u.email == "admin":
        return "Admin"
    return u.name


def _room_title(db: Session, room: models.ChatRoom, me: models.User) -> str:
    if room.kind in ROOM_NAMES:
        return ROOM_NAMES[room.kind]
    p = _dm_partner(db, room, me)
    return _display_name(p)


def _unread(db: Session, room: models.ChatRoom, me: models.User) -> int:
    last = (db.get(models.ChatRead, (room.id, me.id)) or models.ChatRead(last_read_id=0)).last_read_id or 0
    return (db.query(func.count(models.ChatMessage.id))
            .filter(models.ChatMessage.room_id == room.id, models.ChatMessage.id > last,
                    models.ChatMessage.user_id != me.id, models.ChatMessage.is_deleted == False)  # noqa: E712
            .scalar() or 0)


def _rooms_for(db: Session, me: models.User) -> List[models.ChatRoom]:
    rooms = [_group_room(db, "team")]
    if _is_player(me):
        rooms.append(_group_room(db, "players"))
    dm_ids = [m.room_id for m in db.query(models.ChatMember).filter(models.ChatMember.user_id == me.id).all()]
    if dm_ids:
        rooms += db.query(models.ChatRoom).filter(models.ChatRoom.id.in_(dm_ids)).all()
    return rooms


def unread_total(db: Session, me: models.User) -> int:
    try:
        return sum(_unread(db, r, me) for r in _rooms_for(db, me))
    except Exception:  # noqa: BLE001
        return 0


# ---------------------------------------------------------------------------
# Nachrichten -> JSON
# ---------------------------------------------------------------------------
def _snippet(m: Optional[models.ChatMessage]) -> Optional[dict]:
    if m is None:
        return None
    if m.is_deleted:
        text = "🚫 Nachricht gelöscht"
    elif m.kind == "poll":
        text = "📊 " + (m.text or "Umfrage")
    elif m.kind == "image":
        text = "📷 " + (m.text or "Foto")
    else:
        text = (m.text or "")[:160]
    return {"id": m.id, "user_id": m.user_id, "name": _display_name(m.user), "text": text}


def _ticks(m: models.ChatMessage, cur: Optional[dict]) -> str:
    """'sent' | 'delivered' | 'read' fuer eigene Nachrichten (alle Empfaenger muessen erreicht sein)."""
    if not cur:
        return "sent"
    if cur.get("read") and m.id <= cur["read"]:
        return "read"
    if cur.get("delivered") and m.id <= cur["delivered"]:
        return "delivered"
    return "sent"


def _msg_out(db: Session, m: models.ChatMessage, me: models.User, partner_read: int = 0, cur: Optional[dict] = None) -> dict:
    mine = m.user_id == me.id
    d = {"id": m.id, "user_id": m.user_id, "name": _display_name(m.user), "mine": mine,
         "kind": m.kind, "text": None if m.is_deleted else m.text, "deleted": bool(m.is_deleted),
         "created_at": _iso(m.created_at), "read": bool(partner_read and m.id <= partner_read),
         "status": _ticks(m, cur) if mine else None}
    ex = db.get(models.ChatMsgExtra, m.id)
    if ex and ex.reply_to_id:
        d["reply"] = _snippet(db.get(models.ChatMessage, ex.reply_to_id))
    if m.kind == "image" and not m.is_deleted:
        img = db.query(models.ChatImage).filter(models.ChatImage.message_id == m.id).first()
        if img:
            d["image"] = {"url": image_url(img.id), "w": img.width, "h": img.height}
    if m.kind == "poll" and not m.is_deleted:
        opts = (db.query(models.ChatPollOption).filter(models.ChatPollOption.message_id == m.id)
                .order_by(models.ChatPollOption.sort).all())
        votes = db.query(models.ChatPollVote).filter(models.ChatPollVote.message_id == m.id).all()
        names = {}
        for v in votes:
            names.setdefault(v.option_id, []).append(v.user_id)
        users = {u.id: _display_name(u) for u in db.query(models.User).filter(
            models.User.id.in_({v.user_id for v in votes} or {0})).all()}
        d["poll"] = {
            "multiple": bool(m.poll_multiple),
            "voters": len({v.user_id for v in votes}),
            "options": [{"id": o.id, "text": o.text, "votes": len(names.get(o.id, [])),
                         "mine": me.id in names.get(o.id, []),
                         "who": sorted(users.get(uid, "?") for uid in names.get(o.id, []))} for o in opts],
        }
    return d


# ---------------------------------------------------------------------------
# Zugestellt / Gelesen
# ---------------------------------------------------------------------------
def _log(db: Session, room_id: int, uid: int, kind: str, upto: int) -> None:
    db.add(models.ChatCursorLog(room_id=room_id, user_id=uid, kind=kind, upto_id=upto, at=datetime.utcnow()))


_deliver_at: Dict[int, float] = {}


def deliver_all(db: Session, me: models.User, force: bool = False) -> None:
    """Die App von 'me' hat die neuesten Nachrichten geladen -> zugestellt.
    Hoechstens alle 10 Sekunden pro Person (spart Datenbank-Abfragen)."""
    now = time.time()
    if not force and now - _deliver_at.get(me.id, 0) < 10:
        return
    _deliver_at[me.id] = now
    changed = False
    for r in _rooms_for(db, me):
        top = (db.query(func.max(models.ChatMessage.id))
               .filter(models.ChatMessage.room_id == r.id, models.ChatMessage.user_id != me.id).scalar()) or 0
        if not top:
            continue
        c = db.get(models.ChatCursor, (r.id, me.id))
        if c is None:
            c = models.ChatCursor(room_id=r.id, user_id=me.id, delivered_id=0)
            db.add(c)
        if top > (c.delivered_id or 0):
            c.delivered_id = top
            _log(db, r.id, me.id, "d", top)
            changed = True
    if changed:
        db.commit()


def _receipts_on(db: Session, room: models.ChatRoom, a: int, b: Optional[int]) -> bool:
    """In Direktnachrichten muessen beide Lesebestaetigungen eingeschaltet haben (wie WhatsApp).
    In Gruppen werden sie immer angezeigt (auch wie WhatsApp)."""
    if room.kind != "dm" or b is None:
        return True
    return bool(_privacy(db, a).read_receipts and _privacy(db, b).read_receipts)


def _cursors(db: Session, room: models.ChatRoom, me: models.User) -> Optional[dict]:
    """Kleinster Stand aller Empfaenger -> Haken fuer meine Nachrichten."""
    others = [u.id for u in _members(db, room) if u.id != me.id]
    if not others:
        return None
    reads = {r.user_id: r.last_read_id or 0 for r in db.query(models.ChatRead)
             .filter(models.ChatRead.room_id == room.id, models.ChatRead.user_id.in_(others)).all()}
    dels = {c.user_id: c.delivered_id or 0 for c in db.query(models.ChatCursor)
            .filter(models.ChatCursor.room_id == room.id, models.ChatCursor.user_id.in_(others)).all()}
    read = min(reads.get(u, 0) for u in others)
    delivered = min(max(dels.get(u, 0), reads.get(u, 0)) for u in others)
    if not _receipts_on(db, room, me.id, others[0] if room.kind == "dm" else None):
        read = 0
    return {"read": read, "delivered": delivered}


# ---------------------------------------------------------------------------
# Endpunkte
# ---------------------------------------------------------------------------
@router.get("/rooms")
def rooms(db: Session = Depends(get_db), me: models.User = Depends(get_current_user)):
    touch(db, me)
    deliver_all(db, me)
    out = []
    room_list = _rooms_for(db, me)
    partners = {r.id: _dm_partner(db, r, me) for r in room_list if r.kind == "dm"}
    avs = avatar_urls(db, [p.id for p in partners.values() if p])
    for r in room_list:
        last = (db.query(models.ChatMessage).filter(models.ChatMessage.room_id == r.id)
                .order_by(models.ChatMessage.id.desc()).first())
        preview = None
        if last:
            who = "Du" if last.user_id == me.id else _display_name(last.user).split(" ")[0]
            body = _snippet(last)["text"]
            preview = (f"{who}: " if r.kind != "dm" or last.user_id == me.id else "") + body
        partner = partners.get(r.id)
        rr = db.get(models.ChatRead, (r.id, me.id))
        last_status = _ticks(last, _cursors(db, r, me)) if last and last.user_id == me.id and not last.is_deleted else None
        out.append({"id": r.id, "kind": r.kind, "title": _room_title(db, r, me),
                    "partner_id": partner.id if partner else None,
                    "partner_staff": partner.staff_title if partner else None,
                    "can_write": _can_write(r, me), "can_poll": _can_poll(r, me),
                    "members": len(_members(db, r)) if r.kind != "dm" else 2,
                    "unread": _unread(db, r, me), "muted": bool(rr and rr.muted),
                    "last_at": _iso(last.created_at) if last else None, "preview": preview,
                    "last_status": last_status, "typing": _typing_names(db, r.id, me),
                    "avatar": avs.get(partner.id) if partner else None,
                    "online": _presence(db, me, partner)["online"] if partner else False})
    db.commit()
    # Gruppen oben, danach Direktnachrichten nach letzter Nachricht
    groups = [x for x in out if x["kind"] != "dm"]
    dms = sorted([x for x in out if x["kind"] == "dm"], key=lambda x: x["last_at"] or "", reverse=True)
    return groups + dms


@router.get("/unread")
def unread(db: Session = Depends(get_db), me: models.User = Depends(get_current_user)):
    """Wird von der App regelmaessig aufgerufen (Badge) -> zaehlt auch als 'online' und 'zugestellt'."""
    touch(db, me)
    deliver_all(db, me)
    return {"total": unread_total(db, me)}


@router.get("/contacts")
def contacts(db: Session = Depends(get_db), me: models.User = Depends(get_current_user)):
    users = db.query(models.User).filter(models.User.is_active == True, models.User.id != me.id,  # noqa: E712
                                         models.User.email != "admin").order_by(models.User.name).all()
    avs = avatar_urls(db, [u.id for u in users])
    return [{"id": u.id, "name": u.name, "staff_title": u.staff_title, "avatar": avs.get(u.id)} for u in users]


class DmIn(BaseModel):
    user_id: int


@router.post("/dm")
def open_dm(body: DmIn, db: Session = Depends(get_db), me: models.User = Depends(get_current_user)):
    other = db.get(models.User, body.user_id)
    if not other or not other.is_active or other.id == me.id:
        raise HTTPException(404, "Person nicht gefunden")
    mine = {m.room_id for m in db.query(models.ChatMember).filter(models.ChatMember.user_id == me.id).all()}
    theirs = {m.room_id for m in db.query(models.ChatMember).filter(models.ChatMember.user_id == other.id).all()}
    common = [rid for rid in mine & theirs if (db.get(models.ChatRoom, rid) or models.ChatRoom(kind="")).kind == "dm"]
    if common:
        return {"id": common[0]}
    r = models.ChatRoom(kind="dm")
    db.add(r)
    db.flush()
    db.add(models.ChatMember(room_id=r.id, user_id=me.id))
    db.add(models.ChatMember(room_id=r.id, user_id=other.id))
    db.commit()
    return {"id": r.id}


@router.get("/rooms/{room_id}/messages")
def messages(room_id: int, after: int = 0, before: int = 0, limit: int = 60, polls: int = 0,
             db: Session = Depends(get_db), me: models.User = Depends(get_current_user)):
    """Ohne 'after': ganzer Chat (erstes Laden). Mit 'after' (alle 2 Sek.): nur Neues -
    Umfragen werden nur mit polls=1 mitgeschickt (die App fragt das alle 10 Sek.)."""
    room = _room_or_404(db, room_id, me)
    touch(db, me)
    deliver_all(db, me, force=not after)
    q = db.query(models.ChatMessage).filter(models.ChatMessage.room_id == room.id)
    if after:
        rows = q.filter(models.ChatMessage.id > after).order_by(models.ChatMessage.id).limit(200).all()
    else:
        if before:
            q = q.filter(models.ChatMessage.id < before)
        rows = list(reversed(q.order_by(models.ChatMessage.id.desc()).limit(min(limit, 200)).all()))
    partner_read = 0
    partner = None
    if room.kind == "dm":
        partner = _dm_partner(db, room, me)
        pr = db.get(models.ChatRead, (room.id, partner.id)) if partner else None
        partner_read = pr.last_read_id if pr else 0
        if partner and not _receipts_on(db, room, me.id, partner.id):
            partner_read = 0
    cur = _cursors(db, room, me)
    # Umfragen und Loeschungen aendern sich auch spaeter -> bei "after" die letzten Umfragen mitschicken
    want_polls = bool(polls)
    polls = []
    if after and want_polls:
        polls = [_msg_out(db, m, me, partner_read, cur) for m in
                 q.filter(models.ChatMessage.kind == "poll", models.ChatMessage.id <= after)
                 .order_by(models.ChatMessage.id.desc()).limit(10).all()]
    if after:   # schlanke Antwort fuers regelmaessige Nachladen
        return {"messages": [_msg_out(db, m, me, partner_read, cur) for m in rows],
                "updated_polls": polls, "partner_read": partner_read, "cursors": cur,
                "typing": _typing_names(db, room.id, me),
                "presence": _presence(db, me, partner) if partner else None,
                "deleted_ids": [i for (i,) in db.query(models.ChatMessage.id).filter(
                    models.ChatMessage.room_id == room.id, models.ChatMessage.is_deleted == True,  # noqa: E712
                    models.ChatMessage.id <= after).order_by(models.ChatMessage.id.desc()).limit(30).all()]}
    members = _members(db, room)
    member_names = sorted((_display_name(u).split(" ")[0] for u in members if u.id != me.id))
    return {"room": {"id": room.id, "kind": room.kind, "title": _room_title(db, room, me),
                     "can_write": _can_write(room, me), "can_poll": _can_poll(room, me),
                     "members": len(members), "member_names": member_names,
                     "partner_id": partner.id if partner else None,
                     "partner_staff": partner.staff_title if partner else None,
                     "avatar": avatar_urls(db, [partner.id]).get(partner.id) if partner else None},
            "messages": [_msg_out(db, m, me, partner_read, cur) for m in rows],
            "updated_polls": polls, "partner_read": partner_read, "cursors": cur,
            "typing": _typing_names(db, room.id, me),
            "presence": _presence(db, me, partner) if partner else None,
            "deleted_ids": [m.id for m in q.filter(models.ChatMessage.is_deleted == True,  # noqa: E712
                                                   models.ChatMessage.id <= after).order_by(models.ChatMessage.id.desc()).limit(30).all()] if after else [],
            "has_more": bool(rows) and not after and q.filter(models.ChatMessage.id < rows[0].id).count() > 0}


class TextIn(BaseModel):
    text: str
    reply_to_id: Optional[int] = None


def _set_reply(db: Session, room: models.ChatRoom, m: models.ChatMessage, reply_to_id: Optional[int]) -> None:
    if not reply_to_id:
        return
    orig = db.get(models.ChatMessage, reply_to_id)
    if orig and orig.room_id == room.id:
        db.add(models.ChatMsgExtra(message_id=m.id, reply_to_id=orig.id))


def _notify(db: Session, room: models.ChatRoom, sender: models.User, body: str) -> None:
    from app.services import webpush
    ids = []
    for u in _members(db, room):
        if u.id == sender.id:
            continue
        rr = db.get(models.ChatRead, (room.id, u.id))
        if rr and rr.muted:
            continue
        ids.append(u.id)
    if not ids:
        return
    name = _display_name(sender)
    if room.kind == "dm":
        title, text = f"💬 {name}", body
    else:
        title, text = ROOM_NAMES[room.kind], f"{name.split(' ')[0]}: {body}"
    cat = "teaminfo" if room.kind == "team" else "chat"
    webpush.notify(ids, title, text[:180], f"/?open=chat:{room.id}", cat, f"chat-{room.id}")


@router.post("/rooms/{room_id}/messages")
def send(room_id: int, body: TextIn, db: Session = Depends(get_db), me: models.User = Depends(get_current_user)):
    room = _room_or_404(db, room_id, me)
    if not _can_write(room, me):
        raise HTTPException(403, "Hier können nur Trainer schreiben")
    text = (body.text or "").strip()
    if not text:
        raise HTTPException(400, "Leere Nachricht")
    m = models.ChatMessage(room_id=room.id, user_id=me.id, kind="text", text=text[:4000])
    db.add(m)
    db.flush()
    _set_reply(db, room, m, body.reply_to_id)
    _read_row(db, room.id, me.id).last_read_id = m.id
    db.commit()
    with _lock:
        _typing.get(room.id, {}).pop(me.id, None)
    _notify(db, room, me, text)
    return _msg_out(db, m, me, 0, _cursors(db, room, me))


class PollIn(BaseModel):
    question: str
    options: List[str]
    multiple: bool = False


@router.post("/rooms/{room_id}/poll")
def create_poll(room_id: int, body: PollIn, db: Session = Depends(get_db), me: models.User = Depends(get_current_user)):
    room = _room_or_404(db, room_id, me)
    if not _can_poll(room, me):
        raise HTTPException(403, "Hier kannst du keine Umfrage erstellen")
    q = (body.question or "").strip()
    opts = [o.strip()[:120] for o in body.options if o and o.strip()][:12]
    if not q or len(opts) < 2:
        raise HTTPException(400, "Frage und mindestens 2 Antworten nötig")
    m = models.ChatMessage(room_id=room.id, user_id=me.id, kind="poll", text=q[:300], poll_multiple=body.multiple)
    db.add(m)
    db.flush()
    for i, o in enumerate(opts):
        db.add(models.ChatPollOption(message_id=m.id, text=o, sort=i))
    _read_row(db, room.id, me.id).last_read_id = m.id
    db.commit()
    _notify(db, room, me, f"📊 Umfrage: {q}")
    return _msg_out(db, m, me)


class VoteIn(BaseModel):
    option_ids: List[int]


@router.post("/messages/{message_id}/vote")
def vote(message_id: int, body: VoteIn, db: Session = Depends(get_db), me: models.User = Depends(get_current_user)):
    m = db.get(models.ChatMessage, message_id)
    if not m or m.kind != "poll" or m.is_deleted:
        raise HTTPException(404, "Umfrage nicht gefunden")
    _room_or_404(db, m.room_id, me)
    valid = {o.id for o in db.query(models.ChatPollOption).filter(models.ChatPollOption.message_id == m.id).all()}
    chosen = [i for i in body.option_ids if i in valid]
    if not m.poll_multiple:
        chosen = chosen[:1]
    db.query(models.ChatPollVote).filter(models.ChatPollVote.message_id == m.id,
                                         models.ChatPollVote.user_id == me.id).delete()
    for oid in chosen:
        db.add(models.ChatPollVote(option_id=oid, user_id=me.id, message_id=m.id))
    db.commit()
    return _msg_out(db, m, me)


@router.delete("/messages/{message_id}")
def delete_message(message_id: int, db: Session = Depends(get_db), me: models.User = Depends(get_current_user)):
    m = db.get(models.ChatMessage, message_id)
    if not m:
        raise HTTPException(404, "Nachricht nicht gefunden")
    room = _room_or_404(db, m.room_id, me)
    # eigene Nachrichten immer; in Gruppen duerfen Trainer/Admin auch fremde loeschen (Moderation)
    if m.user_id != me.id and not (room.kind != "dm" and me.is_trainer):
        raise HTTPException(403, "Du kannst nur eigene Nachrichten löschen")
    m.is_deleted = True
    m.text = None
    db.commit()
    return {"ok": True}


class ReadIn(BaseModel):
    last_id: int


@router.post("/rooms/{room_id}/read")
def mark_read(room_id: int, body: ReadIn, db: Session = Depends(get_db), me: models.User = Depends(get_current_user)):
    room = _room_or_404(db, room_id, me)
    r = _read_row(db, room.id, me.id)
    top = (db.query(func.max(models.ChatMessage.id)).filter(models.ChatMessage.room_id == room.id).scalar()) or 0
    last = min(body.last_id, top)
    if last > (r.last_read_id or 0):
        r.last_read_id = last
        _log(db, room.id, me.id, "r", last)
    db.commit()
    return {"ok": True}


class MuteIn(BaseModel):
    muted: bool


@router.post("/rooms/{room_id}/mute")
def mute(room_id: int, body: MuteIn, db: Session = Depends(get_db), me: models.User = Depends(get_current_user)):
    room = _room_or_404(db, room_id, me)
    _read_row(db, room.id, me.id).muted = body.muted
    db.commit()
    return {"ok": True}


# ---------------------------------------------------------------------------
# schreibt ...
# ---------------------------------------------------------------------------
@router.post("/rooms/{room_id}/typing")
def typing(room_id: int, db: Session = Depends(get_db), me: models.User = Depends(get_current_user)):
    room = _room_or_404(db, room_id, me)
    if _can_write(room, me):
        with _lock:
            _typing.setdefault(room.id, {})[me.id] = time.time()
    touch(db, me)
    return {"ok": True}


# ---------------------------------------------------------------------------
# Nachrichten-Info (wie WhatsApp: nur fuer eigene Nachrichten)
# ---------------------------------------------------------------------------
@router.get("/messages/{message_id}/info")
def message_info(message_id: int, db: Session = Depends(get_db), me: models.User = Depends(get_current_user)):
    m = db.get(models.ChatMessage, message_id)
    if not m:
        raise HTTPException(404, "Nachricht nicht gefunden")
    room = _room_or_404(db, m.room_id, me)
    if m.user_id != me.id:
        raise HTTPException(403, "Info gibt es nur für eigene Nachrichten")
    others = [u for u in _members(db, room) if u.id != me.id]
    ids = [u.id for u in others]
    reads = {r.user_id: r.last_read_id or 0 for r in db.query(models.ChatRead)
             .filter(models.ChatRead.room_id == room.id, models.ChatRead.user_id.in_(ids or [0])).all()}
    dels = {c.user_id: c.delivered_id or 0 for c in db.query(models.ChatCursor)
            .filter(models.ChatCursor.room_id == room.id, models.ChatCursor.user_id.in_(ids or [0])).all()}
    first = {}
    for uid, kind, at in (db.query(models.ChatCursorLog.user_id, models.ChatCursorLog.kind, func.min(models.ChatCursorLog.at))
                          .filter(models.ChatCursorLog.room_id == room.id, models.ChatCursorLog.upto_id >= m.id,
                                  models.ChatCursorLog.user_id.in_(ids or [0]))
                          .group_by(models.ChatCursorLog.user_id, models.ChatCursorLog.kind).all()):
        first[(uid, kind)] = at
    show_read = _receipts_on(db, room, me.id, ids[0] if room.kind == "dm" and ids else None)
    avs = avatar_urls(db, ids)
    read, delivered, pending = [], [], []
    for u in others:
        row = {"user_id": u.id, "name": _display_name(u), "avatar": avs.get(u.id)}
        r_at, d_at = first.get((u.id, "r")), first.get((u.id, "d"))
        if show_read and reads.get(u.id, 0) >= m.id:
            row["at"] = _iso(r_at) if r_at else None
            row["delivered_at"] = _iso(d_at) if d_at else row["at"]
            read.append(row)
        elif max(dels.get(u.id, 0), reads.get(u.id, 0)) >= m.id:
            row["at"] = _iso(d_at or r_at) if (d_at or r_at) else None
            delivered.append(row)
        else:
            pending.append(row)
    return {"id": m.id, "kind": room.kind, "sent_at": _iso(m.created_at), "read": read, "delivered": delivered,
            "pending": pending, "receipts_hidden": not show_read}


# ---------------------------------------------------------------------------
# Fotos
# ---------------------------------------------------------------------------
MAX_IMG = 8 * 1024 * 1024


def _reencode(raw: bytes, max_side: int, quality: int = 82):
    """Bild drehen, verkleinern und OHNE Metadaten (GPS usw.) als JPEG speichern."""
    from PIL import Image, ImageOps
    try:
        im = Image.open(io.BytesIO(raw))
        im = ImageOps.exif_transpose(im)
    except Exception:  # noqa: BLE001
        raise HTTPException(400, "Das ist kein gültiges Bild")
    if im.mode not in ("RGB", "L"):
        bg = Image.new("RGB", im.size, (255, 255, 255))
        im = im.convert("RGBA")
        bg.paste(im, mask=im.split()[-1])
        im = bg
    im.thumbnail((max_side, max_side))
    out = io.BytesIO()
    im.save(out, "JPEG", quality=quality, optimize=True)
    return out.getvalue(), im.size


@router.post("/rooms/{room_id}/image")
async def send_image(room_id: int, file: UploadFile = File(...), caption: str = Form(""), reply_to_id: int = Form(0),
                     db: Session = Depends(get_db), me: models.User = Depends(get_current_user)):
    room = _room_or_404(db, room_id, me)
    if not _can_write(room, me):
        raise HTTPException(403, "Hier können nur Trainer schreiben")
    raw = await file.read()
    if len(raw) > MAX_IMG:
        raise HTTPException(400, "Bild ist zu gross (max. 8 MB)")
    data, (w, h) = _reencode(raw, 1600)
    m = models.ChatMessage(room_id=room.id, user_id=me.id, kind="image", text=(caption or "").strip()[:1000] or None)
    db.add(m)
    db.flush()
    db.add(models.ChatImage(message_id=m.id, data=data, mime="image/jpeg", width=w, height=h))
    _set_reply(db, room, m, reply_to_id or None)
    _read_row(db, room.id, me.id).last_read_id = m.id
    db.commit()
    _notify(db, room, me, "📷 " + (m.text or "Foto"))
    return _msg_out(db, m, me, 0, _cursors(db, room, me))


@router.get("/img/{img_id}", include_in_schema=False)
def get_image(img_id: int, k: str = "", db: Session = Depends(get_db)):
    if not check_key("img", img_id, k):
        raise HTTPException(404, "Nicht gefunden")
    img = db.get(models.ChatImage, img_id)
    if not img:
        raise HTTPException(404, "Nicht gefunden")
    msg = db.get(models.ChatMessage, img.message_id) if img.message_id else None
    if msg is None or msg.is_deleted:
        raise HTTPException(404, "Gelöscht")
    return Response(img.data, media_type=img.mime, headers={"Cache-Control": "private, max-age=31536000, immutable"})


# ---------------------------------------------------------------------------
# Profilbild
# ---------------------------------------------------------------------------
@router.post("/avatar")
async def upload_avatar(file: UploadFile = File(...), db: Session = Depends(get_db), me: models.User = Depends(get_current_user)):
    raw = await file.read()
    if len(raw) > MAX_IMG:
        raise HTTPException(400, "Bild ist zu gross (max. 8 MB)")
    from PIL import Image, ImageOps
    try:
        im = ImageOps.exif_transpose(Image.open(io.BytesIO(raw))).convert("RGB")
    except Exception:  # noqa: BLE001
        raise HTTPException(400, "Das ist kein gültiges Bild")
    side = min(im.size)
    left, top = (im.width - side) // 2, (im.height - side) // 2
    im = im.crop((left, top, left + side, top + side)).resize((320, 320))
    out = io.BytesIO()
    im.save(out, "JPEG", quality=85, optimize=True)
    row = db.get(models.UserAvatar, me.id)
    if row is None:
        db.add(models.UserAvatar(user_id=me.id, data=out.getvalue(), updated_at=datetime.utcnow()))
    else:
        row.data, row.updated_at = out.getvalue(), datetime.utcnow()
    db.commit()
    return {"avatar": avatar_urls(db, [me.id]).get(me.id)}


@router.delete("/avatar")
def delete_avatar(db: Session = Depends(get_db), me: models.User = Depends(get_current_user)):
    row = db.get(models.UserAvatar, me.id)
    if row:
        db.delete(row)
        db.commit()
    return {"avatar": None}


@router.delete("/avatar/{user_id}")
def delete_avatar_of(user_id: int, db: Session = Depends(get_db), me: models.User = Depends(get_current_user)):
    """Trainer/Admin koennen unpassende Profilbilder entfernen."""
    if not me.is_trainer:
        raise HTTPException(403, "Nur Trainer/Admin")
    row = db.get(models.UserAvatar, user_id)
    if row:
        db.delete(row)
        db.commit()
    return {"ok": True}


@router.get("/me")
def chat_me(db: Session = Depends(get_db), me: models.User = Depends(get_current_user)):
    p = _privacy(db, me.id)
    return {"avatar": avatar_urls(db, [me.id]).get(me.id), "show_last_seen": bool(p.show_last_seen),
            "read_receipts": bool(p.read_receipts)}


class PrivacyIn(BaseModel):
    show_last_seen: bool
    read_receipts: bool


@router.put("/privacy")
def set_privacy(body: PrivacyIn, db: Session = Depends(get_db), me: models.User = Depends(get_current_user)):
    p = db.get(models.ChatPrivacy, me.id)
    if p is None:
        p = models.ChatPrivacy(user_id=me.id)
        db.add(p)
    p.show_last_seen, p.read_receipts = body.show_last_seen, body.read_receipts
    db.commit()
    return {"ok": True}


# Profilbilder sind ausserhalb von /chat erreichbar (fuer Terminplan, Kontakte usw.)
public = APIRouter(tags=["chat"])


@public.get("/avatars/{user_id}", include_in_schema=False)
def get_avatar(user_id: int, v: str = "0", k: str = "", db: Session = Depends(get_db)):
    if not check_key("av", user_id, k, v):
        raise HTTPException(404, "Nicht gefunden")
    row = db.get(models.UserAvatar, user_id)
    if not row:
        raise HTTPException(404, "Nicht gefunden")
    return Response(row.data, media_type=row.mime, headers={"Cache-Control": "private, max-age=31536000, immutable"})


# ---------------------------------------------------------------------------
# Alte Nachrichten automatisch loeschen (Admin-Einstellung, Standard: aus)
# ---------------------------------------------------------------------------
RETENTION_KEY = "chat_retention"


def _retention(db: Session) -> int:
    import json
    row = db.get(models.AppAsset, RETENTION_KEY)
    try:
        return int(json.loads(row.data.decode())["months"]) if row else 0
    except Exception:  # noqa: BLE001
        return 0


@router.get("/retention")
def get_retention(db: Session = Depends(get_db), me: models.User = Depends(get_current_user)):
    return {"months": _retention(db)}


class RetentionIn(BaseModel):
    months: int


@router.put("/retention")
def set_retention(body: RetentionIn, db: Session = Depends(get_db), me: models.User = Depends(get_current_user)):
    import json
    if me.base_role != models.BaseRole.ADMIN:
        raise HTTPException(403, "Nur der Admin")
    months = body.months if body.months in (0, 3, 6, 12, 24) else 0
    row = db.get(models.AppAsset, RETENTION_KEY)
    data = json.dumps({"months": months}).encode()
    if row is None:
        db.add(models.AppAsset(key=RETENTION_KEY, data=data, mime="application/json"))
    else:
        row.data = data
    db.commit()
    return {"months": months}


def cleanup_old(db: Session) -> int:
    """Loescht Nachrichten (inkl. Fotos und Umfragen), die aelter als eingestellt sind."""
    months = _retention(db)
    if not months:
        return 0
    cutoff = datetime.utcnow() - timedelta(days=30 * months)
    ids = [i for (i,) in db.query(models.ChatMessage.id).filter(models.ChatMessage.created_at < cutoff).limit(500).all()]
    if not ids:
        return 0
    db.query(models.ChatPollVote).filter(models.ChatPollVote.message_id.in_(ids)).delete(synchronize_session=False)
    db.query(models.ChatPollOption).filter(models.ChatPollOption.message_id.in_(ids)).delete(synchronize_session=False)
    db.query(models.ChatImage).filter(models.ChatImage.message_id.in_(ids)).delete(synchronize_session=False)
    db.query(models.ChatMsgExtra).filter(models.ChatMsgExtra.message_id.in_(ids)).delete(synchronize_session=False)
    db.query(models.ChatMessage).filter(models.ChatMessage.id.in_(ids)).delete(synchronize_session=False)
    db.commit()
    return len(ids)
