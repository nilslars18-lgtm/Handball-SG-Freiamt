"""handball.ch-Anbindung: Spiel suchen ("Spiel gegen Stans"), Resultat +
Statistik + Liveticker holen, Entwurf fuer den Spielbericht erzeugen."""
import json
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app import models
from app.config import settings
from app.database import get_db
from app.deps import get_current_user, require_admin, require_owner
from app.services import handball_ch as hb
from app.services.places import build_maps_link

router = APIRouter(prefix="/handballch", tags=["handball.ch"])


def _match_app_game(db: Session, start: Optional[datetime]) -> Optional[models.Game]:
    """Gleiches Spiel in der App finden: gleicher Tag, naechste Uhrzeit."""
    if not start:
        return None
    day = datetime(start.year, start.month, start.day)
    cands = (
        db.query(models.Game)
        .filter(models.Game.start_time >= day, models.Game.start_time < day + timedelta(days=1))
        .all()
    )
    if not cands:
        return None
    return min(cands, key=lambda g: abs((g.start_time - start).total_seconds()))


def _can_import(user: models.User, report: Optional[models.GameReport]) -> bool:
    if user.is_trainer or user.is_captain:
        return True
    return bool(report and report.writer_id == user.id)


@router.get("/games")
def search_games(q: str = "", only_played: bool = False, db: Session = Depends(get_db),
                 user: models.User = Depends(get_current_user)):
    """Alle Spiele des Teams auf handball.ch, optional gefiltert ('stans')."""
    try:
        games = hb.team_games()
    except hb.HandballChError as e:
        raise HTTPException(502, str(e))
    q = q.strip().lower()
    for word in ("spiel", "gegen", "vs", "match", "u19", "inter"):
        q = q.replace(word, " ")
    words = [w for w in q.split() if w]
    out = []
    imported = {d.hb_game_id: d.game_id for d in db.query(models.HbGameData).all()}
    for g in games:
        if words and not all(w in g["opponent"].lower() for w in words):
            continue
        if only_played and not g["played"]:
            continue
        app_game = _match_app_game(db, g["start_time"])
        g["app_game_id"] = app_game.id if app_game else None
        g["imported"] = g["hb_game_id"] in imported
        out.append(g)
    out.sort(key=lambda x: x["start_time"] or datetime.min, reverse=True)
    return out


def _do_import(db: Session, hb_game_id: int, overwrite_text: bool = False, user: Optional[models.User] = None) -> dict:
    """Holt ein Spiel von handball.ch und fuellt Resultat, Statistik und Entwurf.
    user=None bedeutet: automatischer Abgleich (keine Rechtepruefung)."""
    try:
        b = hb.game_bundle(hb_game_id)
    except hb.HandballChError as e:
        raise HTTPException(502, str(e))
    start = hb._dt(b.get("start_time"))
    game = _match_app_game(db, start)
    report = db.query(models.GameReport).filter(models.GameReport.game_id == game.id).first() if game else None
    if user is not None and not _can_import(user, report):
        raise HTTPException(403, "Nur der eingeteilte Schreiber, Captain oder Trainer")
    if game is None:
        # Spiel ist (noch) nicht in der App -> anlegen
        if not start:
            raise HTTPException(400, "Spiel hat kein Datum")
        game = models.Game(opponent=b["opponent"], is_home=b["is_home"], start_time=start,
                           end_time=start + timedelta(minutes=settings.game_default_duration_minutes),
                           location_name=b.get("venue"),
                           maps_link=build_maps_link(b.get("venue") or b["opponent"]))
        db.add(game)
        db.flush()
    if report is None:
        report = db.query(models.GameReport).filter(models.GameReport.game_id == game.id).first()
    if report is None:
        report = models.GameReport(game_id=game.id)
        db.add(report)
        db.flush()

    played = b.get("us") is not None and b.get("them") is not None and (b["us"] or b["them"])
    if played:
        report.score_home, report.score_away = b["us"], b["them"]
        report.half_home, report.half_away = b.get("half_us"), b.get("half_them")
    rows = [
        {k: p.get(k) for k in ("name", "nr", "tore", "wuerfe", "siebenm", "zwei_min", "gelb", "rot", "tf", "paraden")}
        for p in b["players"]
        if p.get("tore") or p.get("wuerfe") or p.get("paraden") or p.get("zwei_min") or p.get("gelb")
    ]
    if rows:
        report.stats_json = json.dumps(rows, ensure_ascii=False)
    draft = hb.draft_report(b, game.start_time) if played else None
    if draft and (overwrite_text or not (report.text or "").strip()):
        report.text = draft
    report.updated_at = datetime.utcnow()

    data = db.get(models.HbGameData, game.id)
    if data is None:
        data = models.HbGameData(game_id=game.id, hb_game_id=hb_game_id, payload="{}")
        db.add(data)
    data.hb_game_id = hb_game_id
    data.payload = json.dumps(b, ensure_ascii=False, default=str)
    data.draft = draft
    data.imported_at = datetime.utcnow()
    db.commit()
    return {"game_id": game.id, "played": bool(played), "players": len(rows), "draft": draft,
            "text_replaced": bool(draft and report.text == draft),
            "us": b.get("us"), "them": b.get("them"), "opponent": b["opponent"]}


@router.post("/import/{hb_game_id}")
def import_game(hb_game_id: int, overwrite_text: bool = False, db: Session = Depends(get_db),
                user: models.User = Depends(get_current_user)):
    return _do_import(db, hb_game_id, overwrite_text, user)


@router.get("/game/{game_id}")
def imported_data(game_id: int, db: Session = Depends(get_db), user=Depends(get_current_user)):
    """Gespeicherte handball.ch-Daten eines App-Spiels (Liveticker, Torhueter, Draft)."""
    d = db.get(models.HbGameData, game_id)
    if not d:
        return {"imported": False}
    b = json.loads(d.payload)
    return {"imported": True, "hb_game_id": d.hb_game_id, "imported_at": d.imported_at, "draft": d.draft,
            "team_stats": b.get("team_stats"), "team": b.get("team"), "opponent": b.get("opponent"),
            "us": b.get("us"), "them": b.get("them"), "half_us": b.get("half_us"), "half_them": b.get("half_them"),
            "players": b.get("players", []),
            "link": f"https://www.handball.ch/de/matchcenter/spiele/{d.hb_game_id}",
            "spectators": b.get("spectators"), "referees": b.get("referees"), "goalies": b.get("goalies"),
            "log": b.get("log", []), "analysis": _analysis(b)}


def _analysis(b: dict):
    try:
        from app.services.game_analysis import analyze
        return analyze(b.get("log") or [], bool(b.get("is_home")))
    except Exception:  # noqa: BLE001
        return None


def sync_missing(db: Session) -> dict:
    """Alle gespielten Spiele holen, die in der App noch kein Resultat/keine Statistik haben."""
    games = hb.team_games()
    done, errors = [], 0
    for g in games:
        if not g["played"]:
            continue
        app_game = _match_app_game(db, g["start_time"])
        rep = db.query(models.GameReport).filter(models.GameReport.game_id == app_game.id).first() if app_game else None
        data = db.get(models.HbGameData, app_game.id) if app_game else None
        recent = g["start_time"] and (datetime.now() - g["start_time"]).total_seconds() < 3 * 86400
        if (rep and rep.score_home is not None and rep.stats_json and data
                and '"team_stats"' in (data.payload or "") and not recent):
            continue  # schon vollstaendig geholt (Spiele der letzten 3 Tage werden nachgeladen)
        try:
            r = _do_import(db, g["hb_game_id"])
            done.append(f"{g['opponent']} {r['us']}:{r['them']}")
        except Exception:
            db.rollback()
            errors += 1
            continue
        try:
            _notify_stats(db, g, r)
        except Exception:  # noqa: BLE001
            db.rollback()
    if done:
        try:   # neue Spieler im Kader? -> Login anlegen und Admin informieren
            create_missing_logins(db, auto=True)
        except Exception:  # noqa: BLE001
            db.rollback()
    return {"imported": done, "errors": errors}


def _notify_stats(db: Session, g: dict, r: dict) -> None:
    """Admin/Trainer: Nachricht, sobald Resultat + Statistik eines Spiels in der App sind (einmal pro Spiel)."""
    if not r.get("played") or not r.get("players"):
        return
    from app.services import webpush
    if not webpush.once(db, f"stats:{g['hb_game_id']}"):
        return
    data = db.get(models.HbGameData, r["game_id"])
    top = ""
    try:
        b = json.loads(data.payload) if data else {}
        field = sorted([p for p in b.get("players", []) if p.get("tore")], key=lambda p: -int(p.get("tore") or 0))[:3]
        if field:
            top = "Tore: " + ", ".join(f"{p['name'].split(' ')[0]} {p['tore']}" for p in field)
        keepers = [p for p in b.get("players", []) if p.get("paraden")]
        if keepers:
            top += (" · " if top else "") + "TW: " + ", ".join(f"{p['name'].split(' ')[0]} {p['paraden']}" for p in keepers)
    except Exception:  # noqa: BLE001
        pass
    us, them = r.get("us"), r.get("them")
    res = "🏆 Sieg" if us > them else "😞 Niederlage" if us < them else "🤝 Unentschieden"
    webpush.notify_admins(db, f"📊 Statistik da: {us}:{them} gegen {r.get('opponent')}",
                          f"{res}. {top}".strip(), f"/?open=game:{r['game_id']}", "statistik", f"stats-{r['game_id']}")


@router.post("/sync")
def sync_results(db: Session = Depends(get_db), admin=Depends(require_admin)):
    try:
        return sync_missing(db)
    except hb.HandballChError as e:
        raise HTTPException(502, str(e))


import threading as _threading

_sync_lock = _threading.Lock()
_last_sync = {"at": None, "result": None}


def sync_now(db: Session, max_age_seconds: int = 120) -> dict:
    """Abgleich ausfuehren, ausser er lief gerade erst (oder laeuft schon).
    Gibt zurueck, was neu geholt wurde."""
    now = datetime.now()
    if _last_sync["at"] and (now - _last_sync["at"]).total_seconds() < max_age_seconds:
        return {"imported": [], "skipped": "recent"}
    if not _sync_lock.acquire(blocking=True, timeout=25):
        return {"imported": [], "skipped": "busy"}
    try:
        # waehrend wir auf das Lock gewartet haben, kann ein anderer Abgleich fertig geworden sein
        if _last_sync["at"] and (datetime.now() - _last_sync["at"]).total_seconds() < max_age_seconds:
            return {"imported": _last_sync["result"].get("imported", []) if _last_sync["result"] else [],
                    "skipped": "just_done"}
        r = sync_missing(db)
        _last_sync.update(at=datetime.now(), result=r)
        return r
    finally:
        _sync_lock.release()


@router.post("/refresh")
def refresh(db: Session = Depends(get_db), user=Depends(get_current_user)):
    """Wird beim Oeffnen der Statistik aufgerufen: holt neue Spiele von handball.ch,
    falls der letzte Abgleich mehr als 2 Minuten her ist."""
    try:
        r = sync_now(db)
    except hb.HandballChError as e:
        return {"imported": [], "error": str(e)}
    return {"imported": r.get("imported", []), "skipped": r.get("skipped")}


def background_sync() -> None:
    """Beim Start der App (Render weckt sie regelmaessig) automatisch neue
    Resultate holen - laeuft im Hintergrund, Fehler werden ignoriert."""
    import logging
    import threading

    from app.database import SessionLocal

    def run():
        db = SessionLocal()
        try:
            r = sync_now(db, max_age_seconds=60)
            if r["imported"]:
                logging.getLogger("uvicorn").info("handball.ch: %s", ", ".join(r["imported"]))
        except Exception as exc:  # handball.ch nicht erreichbar o.ae.
            logging.getLogger("uvicorn").warning("handball.ch-Abgleich fehlgeschlagen: %s", exc)
        finally:
            db.close()

    def loop():
        import time
        while True:
            run()
            time.sleep(30 * 60)  # alle 30 Minuten neue Resultate/Statistiken holen

    threading.Thread(target=loop, daemon=True).start()


# ---------------------------------------------------------------------------
# Logins fuer alle Spieler anlegen, die gespielt haben (Benutzer = Vorname)
# ---------------------------------------------------------------------------
WORDS = ["Harz", "Kreis", "Konter", "Fluegel", "Kempa", "Abwehr", "Wurf", "Pass", "Block", "Parade",
         "Tor", "Sprung", "Finte", "Dreher", "Heber", "Rueckraum", "Anwurf", "Freiwurf", "Siebner", "Halle"]


def make_password() -> str:
    import secrets
    w = secrets.SystemRandom().sample(WORDS, 2)
    return f"{w[0]}-{w[1]}-{secrets.randbelow(90) + 10}"


def _login_name(first: str) -> str:
    import unicodedata
    base = first.split()[0].lower()
    return base.replace(" ", "")


def create_missing_logins(db: Session, players: Optional[list] = None, auto: bool = False) -> dict:
    """Gleicht die Spieler laut handball.ch mit den Logins ab und legt fehlende an
    (Benutzer = Vorname, Passwort generiert). Neue Logins erscheinen automatisch in der
    Bussenliste. auto=True: jeder handball.ch-Name wird nur einmal behandelt - wer vom
    Admin geloescht wurde, wird also nicht wieder angelegt."""
    from sqlalchemy import func
    from app.security import hash_password
    if players is None:
        players = hb.team_players()
    users = db.query(models.User).all()   # auch inaktive: Geloeschte nicht neu anlegen
    names = {u.name.strip().lower(): u for u in users}
    firsts_users = {}
    for u in users:
        firsts_users.setdefault(u.name.strip().lower().split(" ")[0], []).append(u)
    firsts = {}
    for p in players:
        firsts[p["first"].lower()] = firsts.get(p["first"].lower(), 0) + 1
    created, skipped = [], []
    for p in players:
        key = f"autouser:{p['name'].strip().lower()}"
        if auto and db.get(models.PushSent, key):
            continue
        full = p["name"].strip().lower()
        same_first = firsts_users.get(p["first"].lower(), [])
        existing = names.get(full) or (
            same_first[0] if firsts[p["first"].lower()] == 1 and len(same_first) == 1
            and len(same_first[0].name.split()) == 1 else None)
        if auto:
            db.add(models.PushSent(key=key))
        if existing:
            skipped.append({"name": p["name"], "reason": f"existiert schon als '{existing.name}' ({existing.email})"})
            continue
        login = _login_name(p["first"])
        taken = lambda c: db.query(models.User).filter(func.lower(models.User.email) == c).first()  # noqa: E731
        if (firsts[p["first"].lower()] > 1 or taken(login)) and p.get("last"):
            login = f"{login}.{_login_name(p['last'])[0]}"
        candidate, i = login, 2
        while taken(candidate):
            candidate, i = f"{login}{i}", i + 1
        pw = make_password()
        u = models.User(name=p["name"], email=candidate, password_hash=hash_password(pw),
                        last_plaintext_password=pw, base_role=models.BaseRole.PLAYER)
        db.add(u)
        db.flush()
        names[full] = u
        firsts_users.setdefault(p["first"].lower(), []).append(u)
        created.append({"name": p["name"], "login": candidate, "password": pw})
    db.commit()
    if created:
        _notify_new_logins(db, created)
    return {"created": created, "skipped": skipped, "total_players": len(players)}


def _notify_new_logins(db: Session, created: list) -> None:
    """Admin-Login bekommt Login + Passwort (Push + Hinweis auf der Startseite)."""
    from app.services import webpush
    owners = db.query(models.User).filter(models.User.base_role == models.BaseRole.ADMIN).all()
    for c in created:
        text = f"Neuer Spieler {c['name']}: Benutzer „{c['login']}“, Passwort „{c['password']}“ (auch in der Login-Liste)"
        for o in owners:
            db.add(models.Notice(user_id=o.id, text="🆕 " + text))
    db.commit()
    if len(created) == 1:
        c = created[0]
        title = f"🆕 Neuer Spieler: {c['name']}"
        body = f"Login erstellt – Benutzer: {c['login']} · Passwort: {c['password']}"
    else:
        title = f"🆕 {len(created)} neue Spieler-Logins"
        body = " · ".join(f"{c['name']}: {c['login']} / {c['password']}" for c in created)
    webpush.notify([o.id for o in owners], title, body, "/?open=tab:verwaltung", "logins", "new-logins")


@router.post("/create-players")
def create_player_logins(db: Session = Depends(get_db), admin=Depends(require_owner)):
    try:
        return create_missing_logins(db)
    except hb.HandballChError as e:
        raise HTTPException(502, str(e))


@router.get("/standing")
def get_standing(db: Session = Depends(get_db), user=Depends(get_current_user)):
    """Aktuelle Ligatabelle von handball.ch (max. 10 Minuten alt)."""
    try:
        return hb.standing()
    except hb.HandballChError as e:
        raise HTTPException(502, str(e))
