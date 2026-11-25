"""Essen-Umfrage nach dem Spiel - automatische Meldungen.

1. Umfrage geht auf  -> Push an alle: "Kommst du nach dem Spiel essen? Was?"
2. 3 Std. vor Schluss -> Erinnerung an alle, die noch nicht abgestimmt haben
3. Umfrage zu (30 Min. vor dem Spiel) -> Restaurants zur Gewinner-Kueche in der Naehe
   der Halle suchen und an Admin + Captain(s) schicken (zum Reservieren)
4. Spiel vorbei      -> Push an alle: was gegessen wird und wer mitkommt
Jede Meldung nur einmal. Laeuft auch spaet abends (Spiele enden oft nach 21:30).
"""
import json
import logging
from datetime import timedelta

from app import models
from app.config import settings
from app.services import webpush
from app.services.agenda import now_local, team_members
from app.services.polls import create_poll_for_game, poll_status, poll_summary, results_visible

log = logging.getLogger("food_jobs")


def _game_title(g: models.Game) -> str:
    return f"{'Heimspiel' if g.is_home else 'Auswärtsspiel' if g.is_home is False else 'Spiel'} gegen {g.opponent}"


def save_suggestions(db, poll_id: int, data: dict) -> None:
    key = f"food_{poll_id}"
    row = db.get(models.AppAsset, key)
    raw = json.dumps(data, ensure_ascii=False, default=str).encode()
    if row is None:
        db.add(models.AppAsset(key=key, data=raw, mime="application/json"))
    else:
        row.data = raw
    db.commit()


def load_suggestions(db, poll_id: int):
    row = db.get(models.AppAsset, f"food_{poll_id}")
    if not row:
        return None
    try:
        return json.loads(row.data.decode() if isinstance(row.data, (bytes, bytearray)) else row.data)
    except Exception:  # noqa: BLE001
        return None


def suggestions_for(db, game: models.Game, poll: models.EssenPoll, force: bool = False) -> dict:
    """Gewinner-Kueche + Restaurants (zwischengespeichert)."""
    s = poll_summary(db, poll)
    cached = None if force else load_suggestions(db, poll.id)
    if cached and cached.get("winners") == s["winners"]:
        return cached
    from app.services.food import find_restaurants
    near = game.resolved_address or game.location_name or ""
    rest = []
    for w in s["winners"][:2]:
        if w.strip().lower().startswith("sonstig"):
            continue
        rest += [dict(r, cuisine=w) for r in find_restaurants(w, near, 3 if len(s["winners"]) == 1 else 2)]
    data = {"winners": s["winners"], "winner_votes": s["winner_votes"], "counts": s["counts"],
            "mitkommende": s["mitkommende"], "restaurants": rest[:3], "near": near, "at": now_local().isoformat()}
    save_suggestions(db, poll.id, data)
    return data


def run_once(db) -> dict:
    now = now_local()
    out = {"open": 0, "reminder": 0, "restaurants": 0, "result": 0}
    # Umfragen fuer alle kommenden Spiele sicherstellen (auch fuer Spiele von handball.ch)
    for g in db.query(models.Game).filter(models.Game.start_time > now, models.Game.start_time < now + timedelta(days=14)).all():
        if not g.poll:
            create_poll_for_game(db, g)
    db.commit()

    polls = (db.query(models.EssenPoll).join(models.Game, models.Game.id == models.EssenPoll.game_id)
             .filter(models.Game.start_time > now - timedelta(hours=8), models.Game.start_time < now + timedelta(days=8)).all())
    members = [p.id for p in team_members(db)]
    for poll in polls:
        g = poll.game
        st = poll_status(poll, now)
        title = _game_title(g)
        url = f"/?open=game:{g.id}"
        opts = ", ".join(o.label for o in poll.options if o.label.strip().casefold() != settings.poll_skip_option.strip().casefold())
        if st == "offen" and webpush.once(db, f"food-open:{poll.id}"):
            webpush.notify(members, "🍽️ Essen nach dem Spiel?", f"{title}: Kommst du mit und was willst du? ({opts} oder „komme nicht mit“)",
                           url, "essen", f"food-{poll.id}")
            out["open"] += 1
        if st == "offen" and poll.closes_at - now <= timedelta(hours=3):
            s = poll_summary(db, poll)
            missing = [m for m in members if m not in s["voted_ids"]]
            if missing and webpush.once(db, f"food-rem:{poll.id}"):
                webpush.notify(missing, "🍽️ Noch nicht abgestimmt", f"Essen nach dem Spiel ({title}) – Umfrage schliesst um {poll.closes_at:%H:%M}.",
                               url, "essen", f"food-{poll.id}")
                out["reminder"] += 1
        if st == "geschlossen" and webpush.once(db, f"food-close:{poll.id}"):
            data = suggestions_for(db, g, poll, force=True)
            if data["winners"]:
                ids = [u.id for u in db.query(models.User).filter(models.User.is_active == True).all()  # noqa: E712
                       if u.base_role == models.BaseRole.ADMIN or u.is_captain]
                from app.services.food import short
                rest = " · ".join(short(r) for r in data["restaurants"]) or "Keine Restaurants gefunden – bitte selber suchen."
                webpush.notify(ids, f"🍽️ {' / '.join(data['winners'])}: {len(data['mitkommende'])} kommen mit",
                               f"Vorschläge bei {data['near'] or 'der Halle'}: {rest}", url, "essen", f"food-rest-{poll.id}")
                out["restaurants"] += 1
        if results_visible(g, now) and now - (g.end_time or g.start_time) < timedelta(hours=4) \
                and webpush.once(db, f"food-result:{poll.id}"):
            data = suggestions_for(db, g, poll)
            if data["winners"]:
                names = ", ".join(n.split(" ")[0] for n in data["mitkommende"])
                webpush.notify(members, f"🍽️ Nach dem Spiel: {' / '.join(data['winners'])}",
                               f"{len(data['mitkommende'])} kommen mit: {names}. Details in der App.", url, "essen", f"food-{poll.id}")
                out["result"] += 1
    return out
