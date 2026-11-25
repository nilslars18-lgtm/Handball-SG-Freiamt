"""Daten von handball.ch (Matchcenter) holen: Spiele des Teams, Resultat,
Spielerstatistik und Liveticker - und daraus einen Entwurf fuer den
Spielbericht schreiben.

Hinweis: handball.ch laedt diese Daten auf der eigenen Webseite ueber
/Umbraco/Api/MatchCenter/Query (GraphQL). Das ist keine offiziell
dokumentierte Schnittstelle - wenn handball.ch die Seite umbaut, muss das
hier evtl. angepasst werden. Die Team-ID (z.B. 40666) aendert sich jede Saison
und wird ueber HANDBALL_TEAM_ID gesetzt.
"""
import re
from datetime import datetime
from typing import List, Optional

import httpx

from app.config import settings

URL = "https://www.handball.ch/Umbraco/Api/MatchCenter/Query"
HEADERS = {
    "Content-Type": "application/json",
    "Accept-Language": "de",
    "User-Agent": "Mozilla/5.0 (SG Freiamt PLUS Team-App)",
    "Referer": "https://www.handball.ch/de/matchcenter/",
}

Q_GAMES = """query($teamId: Int){ games(teamId: $teamId) {
  objectId gameNumber gameDateTime homeTeamId homeTeamName awayTeamId awayTeamName
  homeTeamScore awayTeamScore homeTeamScoreInterval awayTeamScoreInterval venueName
} }"""
Q_GAME = """query($gameId: Int, $isLive: Boolean){ game(gameId: $gameId, isLive: $isLive) {
  gameId gameNumber gameDateTime homeTeamId homeTeamName awayTeamId awayTeamName
  homeTeamScore awayTeamScore homeTeamScoreInterval awayTeamScoreInterval
  venueName gameStatus spectators ref1Name ref2Name groupName leagueName
} }"""
Q_STATS = """query($gameId: Int, $isLive: Boolean){ gamePlayerStats(gameId: $gameId, isLive: $isLive) {
  teamId teamName dressNr playerName function scoreAndShot scoreAndShot7m totalScore totalScore7m
  totalShots totalShots7m total2Minutes totalWarnings totalSuspension technicalErrors
  saveAndShot totalSaves totalShotsGK saveAndShot7m isHome
} }"""
Q_LOG = """query($gameId: Int, $isLive: Boolean){ gameLog(gameId: $gameId, isLive: $isLive) {
  timeInt timeString result actionText homeTeamPlayerStaffName awayTeamPlayerStaffName
} }"""

PARTICLES = {"der", "von", "van", "de", "da", "di", "del", "zu", "den", "la", "le"}


class HandballChError(Exception):
    pass


def _query(query: str, variables: dict, key: str):
    try:
        r = httpx.post(URL, json={"query": query, "variables": variables}, headers=HEADERS, timeout=20)
        r.raise_for_status()
        data = r.json()
    except Exception as exc:  # Netzwerk, Format ...
        raise HandballChError(f"handball.ch ist gerade nicht erreichbar ({exc.__class__.__name__})")
    if data.get("errors"):
        raise HandballChError("handball.ch hat die Anfrage abgelehnt")
    return (data.get("data") or {}).get(key) or []


def pretty_name(raw: str) -> str:
    """'KÖHLE Nils' -> 'Nils Köhle', 'AUF DER MAUR Manuel' -> 'Manuel Auf der Maur'."""
    raw = (raw or "").strip()
    if not raw:
        return ""
    parts = raw.split()
    last = [p for p in parts if p.isupper() and len(p) > 1 or p in ("-",)]
    first = [p for p in parts if p not in last]
    if not last or not first:
        return raw.title()
    lastname = " ".join(
        (w.lower() if (i > 0 and w.lower() in PARTICLES) else w.capitalize() if "-" not in w
         else "-".join(x.capitalize() for x in w.split("-")))
        for i, w in enumerate(last)
    )
    return f"{' '.join(first)} {lastname}"


def _dt(s: str) -> Optional[datetime]:
    try:
        return datetime.fromisoformat((s or "")[:19])
    except Exception:
        return None


def team_games(team_id: Optional[int] = None) -> List[dict]:
    team_id = team_id or settings.handball_team_id
    rows = _query(Q_GAMES, {"teamId": team_id}, "games")
    out = []
    for g in rows:
        home = g.get("homeTeamId") == team_id
        opp = (g.get("awayTeamName") if home else g.get("homeTeamName")) or ""
        dt = _dt(g.get("gameDateTime"))
        played = bool((g.get("homeTeamScore") or 0) + (g.get("awayTeamScore") or 0)) and dt and dt < datetime.now()
        out.append({
            "hb_game_id": g.get("objectId"), "game_number": g.get("gameNumber"), "start_time": dt,
            "is_home": home, "opponent": opp.strip(), "venue": g.get("venueName"),
            "us": (g.get("homeTeamScore") if home else g.get("awayTeamScore")) if played else None,
            "them": (g.get("awayTeamScore") if home else g.get("homeTeamScore")) if played else None,
            "played": bool(played),
        })
    out.sort(key=lambda x: x["start_time"] or datetime.min)
    return out


def _split(v):
    """'5/7' -> (5, 7)"""
    m = re.match(r"\s*(\d+)\s*/\s*(\d+)", str(v or ""))
    return (int(m.group(1)), int(m.group(2))) if m else (None, None)


def game_bundle(hb_game_id: int, team_id: Optional[int] = None) -> dict:
    team_id = team_id or settings.handball_team_id
    games = _query(Q_GAME, {"gameId": hb_game_id, "isLive": False}, "game")
    if not games:
        raise HandballChError("Spiel auf handball.ch nicht gefunden")
    g = games[0]
    home = g.get("homeTeamId") == team_id
    stats = _query(Q_STATS, {"gameId": hb_game_id, "isLive": False}, "gamePlayerStats")
    log = _query(Q_LOG, {"gameId": hb_game_id, "isLive": False}, "gameLog")
    players, goalies = [], []

    def blank():
        return {"tore": 0, "wurf_tore": 0, "wuerfe": 0, "siebenm_tore": 0, "siebenm_versuche": 0,
                "tf": 0, "zwei_min": 0, "gelb": 0, "rot": 0, "paraden": 0, "schuesse_aufs_tor": 0}
    team_stats = {"us": blank(), "them": blank()}
    for p in stats:
        if p.get("function") not in ("player", "goalkeeper"):
            continue
        side = team_stats["us" if p.get("teamId") == team_id else "them"]
        side["tore"] += p.get("totalScore") or 0
        wt, wv = _split(p.get("scoreAndShot"))
        if wv is not None:
            side["wurf_tore"] += wt
            side["wuerfe"] += wv
        st, sa = _split(p.get("scoreAndShot7m"))
        if sa is not None:
            side["siebenm_tore"] += st
            side["siebenm_versuche"] += sa
        try:
            side["tf"] += int(p.get("technicalErrors") or 0)
        except (TypeError, ValueError):
            pass
        side["zwei_min"] += p.get("total2Minutes") or 0
        side["gelb"] += p.get("totalWarnings") or 0
        side["rot"] += p.get("totalSuspension") or 0
        ps, pv = _split(p.get("saveAndShot"))
        if ps is not None:
            side["paraden"] += ps
            side["schuesse_aufs_tor"] += pv
    for side in team_stats.values():
        side["wurfquote"] = round(100 * side["wurf_tore"] / side["wuerfe"]) if side["wuerfe"] else None
        side["torhueterquote"] = (round(100 * side["paraden"] / side["schuesse_aufs_tor"])
                                  if side["schuesse_aufs_tor"] else None)

    for p in stats:
        if p.get("teamId") != team_id:
            continue
        name = pretty_name(p.get("playerName"))
        if p.get("function") == "goalkeeper":
            sv, sh = _split(p.get("saveAndShot"))
            goalies.append({"name": name, "nr": p.get("dressNr"), "saves": sv, "shots": sh,
                            "saves7m": p.get("saveAndShot7m") or ""})
        if p.get("function") in ("player", "goalkeeper"):
            players.append({
                "name": name, "nr": p.get("dressNr"),
                "tore": p.get("totalScore") or 0,
                "wuerfe": p.get("scoreAndShot") or "",
                "siebenm": p.get("scoreAndShot7m") or "",
                "zwei_min": p.get("total2Minutes") or 0,
                "gelb": p.get("totalWarnings") or 0,
                "rot": p.get("totalSuspension") or 0,
                "tf": p.get("technicalErrors") or "",
                "paraden": p.get("saveAndShot") or "",
            })
    players.sort(key=lambda x: (x["nr"] if isinstance(x["nr"], int) else 99))
    return {
        "hb_game_id": hb_game_id,
        "game_number": g.get("gameNumber"),
        "start_time": g.get("gameDateTime"),
        "is_home": home,
        "team": (g.get("homeTeamName") if home else g.get("awayTeamName")) or settings.team_name,
        "opponent": ((g.get("awayTeamName") if home else g.get("homeTeamName")) or "").strip(),
        "us": g.get("homeTeamScore") if home else g.get("awayTeamScore"),
        "them": g.get("awayTeamScore") if home else g.get("homeTeamScore"),
        "half_us": g.get("homeTeamScoreInterval") if home else g.get("awayTeamScoreInterval"),
        "half_them": g.get("awayTeamScoreInterval") if home else g.get("homeTeamScoreInterval"),
        "venue": g.get("venueName"), "spectators": g.get("spectators"),
        "referees": [x for x in (g.get("ref1Name"), g.get("ref2Name")) if x],
        "status": g.get("gameStatus"),
        "players": players, "goalies": goalies, "team_stats": team_stats,
        "log": [
            {"t": e.get("timeInt") or 0, "time": e.get("timeString"), "result": e.get("result"),
             "action": e.get("actionText"),
             "home": e.get("homeTeamPlayerStaffName") or "", "away": e.get("awayTeamPlayerStaffName") or ""}
            for e in log
        ],
    }


# ---------------------------------------------------------------------------
# Entwurf fuer den Spielbericht (ohne KI - aus Zahlen + Liveticker)
# ---------------------------------------------------------------------------
def _score_at(log: List[dict], is_home: bool, second: int):
    """Spielstand (wir, gegner) zum Zeitpunkt `second`."""
    best = (0, 0)
    for e in sorted(log, key=lambda x: x["t"]):
        if e["t"] > second:
            break
        m = re.match(r"(\d+):(\d+)", e.get("result") or "")
        if m:
            h, a = int(m.group(1)), int(m.group(2))
            best = (h, a) if is_home else (a, h)
    return best


def _goal_events(log: List[dict], is_home: bool):
    """Liste (sekunde, 'wir'/'gegner', torschuetze) in zeitlicher Reihenfolge."""
    out = []
    for e in sorted(log, key=lambda x: x["t"]):
        if e.get("action") not in ("Tor", "7m Tor"):
            continue
        scorer_home = bool(e.get("home"))
        ours = scorer_home == is_home
        out.append((e["t"], "wir" if ours else "gegner", pretty_name(e["home"] or e["away"])))
    return out


def _best_run(goals, side):
    best, cur, start, best_span = 0, 0, None, (0, 0)
    for t, s, _ in goals:
        if s == side:
            if cur == 0:
                start = t
            cur += 1
            if cur > best:
                best, best_span = cur, (start, t)
        else:
            cur = 0
    return best, best_span


def _min(t: int) -> int:
    return max(1, round(t / 60))


def draft_report(b: dict, game_date: Optional[datetime] = None) -> str:
    team, opp = b["team"], b["opponent"]
    us, them = b.get("us") or 0, b.get("them") or 0
    hu, ht = b.get("half_us"), b.get("half_them")
    is_home = b["is_home"]
    log = b.get("log") or []
    dt = game_date or _dt(b.get("start_time"))
    wd = ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag"]
    when = f"Am {wd[dt.weekday()]}, {dt.strftime('%d.%m.%Y')}," if dt else "Im letzten Spiel"
    where = f" in der Halle {b['venue']}" if b.get("venue") else ""
    paras = []

    # Einleitung
    paras.append(f"{when} traf die U19 Inter der {team} {'zu Hause' if is_home else 'auswärts'}{where} "
                 f"auf {opp}." + (f" {b['spectators']} Zuschauer verfolgten die Partie." if b.get("spectators") else ""))

    # Erste Halbzeit
    if log:
        s10 = _score_at(log, is_home, 600)
        s20 = _score_at(log, is_home, 1200)
        first = (f"Die Anfangsphase verlief ausgeglichen, nach zehn Minuten stand es {s10[0]}:{s10[1]}."
                 if abs(s10[0] - s10[1]) <= 1 else
                 f"Nach zehn Minuten {'führte' if s10[0] > s10[1] else 'lag'} unser Team mit {s10[0]}:{s10[1]} "
                 f"{'' if s10[0] > s10[1] else 'zurück'}.".replace("  ", " ").replace(" .", "."))
        paras.append(first + f" In der 20. Minute hiess es {s20[0]}:{s20[1]}." +
                     (f" Zur Pause stand es {hu}:{ht}" + (" – wir lagen also knapp vorne." if hu is not None and ht is not None and 0 < hu - ht <= 2
                      else " – mit einer klaren Führung für uns." if hu is not None and ht is not None and hu - ht > 2
                      else " – unentschieden." if hu == ht else " – wir mussten einem Rückstand hinterherlaufen.")
                      if hu is not None and ht is not None else ""))
    elif hu is not None and ht is not None:
        paras.append(f"Zur Pause stand es {hu}:{ht}.")

    # Zweite Halbzeit
    if log:
        goals = _goal_events(log, is_home)
        run, (a, z) = _best_run([g for g in goals if g[0] > 1800] or goals, "wir")
        s45 = _score_at(log, is_home, 2700)
        lead = max((sc[0] - sc[1]) for sc in (_score_at(log, is_home, g[0]) for g in goals)) if goals else 0
        txt = f"In der zweiten Halbzeit stand es nach 45 Minuten {s45[0]}:{s45[1]}."
        if run >= 3:
            txt += f" Zwischen der {_min(a)}. und {_min(z)}. Minute gelang uns ein {run}:0-Lauf."
        if lead >= 3:
            txt += f" Unser grösster Vorsprung betrug {lead} Tore."
        paras.append(txt)

    # Schluss
    diff = us - them
    if diff > 0:
        res = f"Am Ende gewann die {team} {'deutlich ' if diff >= 5 else 'knapp ' if diff <= 2 else ''}mit {us}:{them}."
    elif diff < 0:
        res = f"Am Ende verlor die {team} {'knapp ' if diff >= -2 else ''}mit {us}:{them}."
    else:
        res = f"Das Spiel endete {us}:{them} unentschieden."
    paras.append(res)

    # Torschuetzen + Torhueter
    scorers = sorted([p for p in b.get("players", []) if p.get("tore")], key=lambda p: -p["tore"])
    if scorers:
        top = []
        for p in scorers[:3]:
            seven = _split(p.get("siebenm"))[0]
            top.append(f"{p['name']} ({p['tore']} Tore{f', davon {seven} Penaltys' if seven else ''})")
        paras.append("Beste Torschützen waren " + ", ".join(top[:-1]) + (" und " if len(top) > 1 else "") + top[-1] + ".")
    saves = [g for g in b.get("goalies", []) if g.get("saves")]
    if saves:
        paras.append("Im Tor zeigten " + " und ".join(f"{g['name']}" for g in saves) +
                     f" zusammen {sum(g['saves'] for g in saves)} Paraden." if len(saves) > 1 else
                     f"Im Tor zeigte {saves[0]['name']} {saves[0]['saves']} Paraden.")
    twomin = sum(p.get("zwei_min") or 0 for p in b.get("players", []))
    if twomin:
        paras.append(f"Wir kassierten {twomin} Zeitstrafe{'n' if twomin != 1 else ''}.")

    paras.append("[Hier noch eigene Eindrücke ergänzen: Stimmung, Taktik, besondere Szenen, Ausblick aufs nächste Spiel …]")
    return "\n\n".join(paras)


def split_name(raw: str):
    """'KÖHLE Nils' -> ('Nils', 'Köhle')"""
    full = pretty_name(raw)
    parts = (raw or "").split()
    first = " ".join(p for p in parts if not (p.isupper() and len(p) > 1))
    last = full[len(first):].strip() if full.startswith(first) else ""
    return first or full, last


def team_players(team_id: Optional[int] = None) -> List[dict]:
    """Alle Spieler/Torhueter, die in dieser Saison fuer das Team gespielt haben."""
    team_id = team_id or settings.handball_team_id
    seen = {}
    for g in team_games(team_id):
        if not g["played"]:
            continue
        for p in _query(Q_STATS, {"gameId": g["hb_game_id"], "isLive": False}, "gamePlayerStats"):
            if p.get("teamId") != team_id or p.get("function") not in ("player", "goalkeeper"):
                continue
            raw = (p.get("playerName") or "").strip()
            if not raw:
                continue
            first, last = split_name(raw)
            e = seen.setdefault(raw.lower(), {"first": first, "last": last, "name": pretty_name(raw),
                                              "nr": p.get("dressNr"), "games": 0,
                                              "goalie": p.get("function") == "goalkeeper"})
            e["games"] += 1
    return sorted(seen.values(), key=lambda x: (x["first"].lower(), x["last"].lower()))


# ---------------------------------------------------------------------------
# Ligatabelle (wie auf handball.ch) - mit kurzem Zwischenspeicher
# ---------------------------------------------------------------------------
Q_STANDING = """query($groupId: Int){ standing(groupId: $groupId) {
  rank teamId name games won drawn lost goalsFor goalsAgainst goalDifference points lastFive relegationType
} }"""
Q_GROUP = """query($teamId: Int){ games(teamId: $teamId) { gameGroupOrCupId groupShortName leagueShortName } }"""

_standing_cache = {"at": None, "data": None}


def standing(team_id: Optional[int] = None, max_age_minutes: int = 10) -> dict:
    team_id = team_id or settings.handball_team_id
    now = datetime.now()
    c = _standing_cache
    if c["data"] and c["at"] and (now - c["at"]).total_seconds() < max_age_minutes * 60 and c["data"]["team_id"] == team_id:
        return c["data"]
    games = _query(Q_GROUP, {"teamId": team_id}, "games")
    groups = {}
    for g in games:
        gid = g.get("gameGroupOrCupId")
        if gid:
            groups.setdefault(gid, {"n": 0, "name": g.get("groupShortName"), "league": g.get("leagueShortName")})
            groups[gid]["n"] += 1
    if not groups:
        raise HandballChError("Keine Gruppe für das Team gefunden")
    group_id = max(groups, key=lambda k: groups[k]["n"])  # Meisterschaft (nicht Cup)
    rows = _query(Q_STANDING, {"groupId": group_id}, "standing")
    data = {
        "team_id": team_id, "group_id": group_id, "group": groups[group_id]["name"],
        "updated": now.isoformat(timespec="minutes"),
        "link": f"https://www.handball.ch/de/matchcenter/teams/{team_id}",
        "rows": [{
            "rank": r.get("rank"), "team_id": r.get("teamId"), "name": (r.get("name") or "").strip(),
            "games": r.get("games"), "won": r.get("won"), "drawn": r.get("drawn"), "lost": r.get("lost"),
            "goals_for": r.get("goalsFor"), "goals_against": r.get("goalsAgainst"), "diff": r.get("goalDifference"),
            "points": r.get("points"), "form": (r.get("lastFive") or "").strip(),
            "zone": r.get("relegationType"), "is_us": r.get("teamId") == team_id,
        } for r in rows],
    }
    _standing_cache.update(at=now, data=data)
    return data
