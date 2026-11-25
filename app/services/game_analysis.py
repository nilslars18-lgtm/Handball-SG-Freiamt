"""Spielanalyse aus dem Liveticker von handball.ch.

Liefert pro Spiel:
- Tore pro 10-Minuten-Phase (wir / Gegner) und Spielstand am Ende jeder Phase
- groesste Laeufe (z. B. 5:0 zwischen Minute 38 und 44)
- Ueber-/Unterzahl: Tore und Zeit in Gleich-, Ueber- und Unterzahl
- Schlussphase (letzte 10 Minuten): Torschuetzen, Spielstand vorher/nachher
- Fuehrungswechsel, hoechste Fuehrung/Rueckstand, Timeouts mit Wirkung
"""
import math
import re
from typing import List, Optional

from app.services.handball_ch import pretty_name

GOAL = ("Tor", "7m Tor")
PENALTY = ("Zeitstrafe",)
RED = ("Rote Karte", "Disqualifikation", "Disqualifikation mit Bericht")
TWO_MIN = 120


def _mmss(sec: int) -> str:
    return f"{sec // 60}:{sec % 60:02d}"


def _minute(sec: int) -> int:
    return sec // 60 + (1 if sec % 60 else 0) if sec else 0


def analyze(log: List[dict], is_home: bool) -> Optional[dict]:
    if not log:
        return None
    ev = sorted(log, key=lambda e: e.get("t") or 0)
    goals = []          # (t, side, name, is7m)
    penalties = []      # (t, side) -> side ist in Unterzahl
    timeouts = []       # (t, side)
    for e in ev:
        a = (e.get("action") or "").strip()
        t = int(e.get("t") or 0)
        side = None
        if e.get("home") and not e.get("away"):
            side = "wir" if is_home else "gegner"
        elif e.get("away") and not e.get("home"):
            side = "gegner" if is_home else "wir"
        if a in GOAL and side:
            goals.append((t, side, pretty_name(e.get("home") or e.get("away")), a == "7m Tor"))
        elif (a in PENALTY or a in RED) and side:
            penalties.append((t, side))
        elif a == "Team Time-Out":
            # Timeout hat oft keinen Spielernamen - Team aus der Spalte ableiten, sonst unbekannt
            timeouts.append((t, side))
    if not goals:
        return None

    end = max([e.get("t") or 0 for e in ev] + [g[0] for g in goals])
    duration = max(1200, int(math.ceil(end / 600.0)) * 600)

    # --- Phasen (10 Minuten)
    phases = []
    us = them = 0
    gi = 0
    for start in range(0, duration, 600):
        stop = start + 600
        pu = pt = 0
        while gi < len(goals) and goals[gi][0] <= stop:
            if goals[gi][1] == "wir":
                pu += 1
                us += 1
            else:
                pt += 1
                them += 1
            gi += 1
        phases.append({"from": start // 60, "to": stop // 60, "us": pu, "them": pt, "score": f"{us}:{them}"})

    # --- Laeufe
    runs = []
    cur_side, cur_n, cur_start, before = None, 0, 0, (0, 0)
    su = st = 0
    for t, side, _, _ in goals:
        if side == cur_side:
            cur_n += 1
        else:
            if cur_side and cur_n >= 3:
                runs.append({"side": cur_side, "n": cur_n, "from": _minute(cur_start), "to": _minute(prev_t),
                             "score_before": f"{before[0]}:{before[1]}", "score_after": f"{su}:{st}"})
            cur_side, cur_n, cur_start, before = side, 1, t, (su, st)
        prev_t = t
        if side == "wir":
            su += 1
        else:
            st += 1
    if cur_side and cur_n >= 3:
        runs.append({"side": cur_side, "n": cur_n, "from": _minute(cur_start), "to": _minute(prev_t),
                     "score_before": f"{before[0]}:{before[1]}", "score_after": f"{su}:{st}"})
    runs.sort(key=lambda r: -r["n"])

    # --- Ueber-/Unterzahl
    def down(side, t):
        return sum(1 for p, s in penalties if s == side and p <= t < p + TWO_MIN)

    def state(t):
        d = down("gegner", t) - down("wir", t)
        return "ueberzahl" if d > 0 else "unterzahl" if d < 0 else "gleichzahl"

    situ = {k: {"us": 0, "them": 0, "sec": 0} for k in ("gleichzahl", "ueberzahl", "unterzahl")}
    for t, side, _, _ in goals:
        situ[state(t - 1 if t else 0)]["us" if side == "wir" else "them"] += 1
    for s in range(0, duration, 5):
        situ[state(s)]["sec"] += 5
    for v in situ.values():
        v["min"] = round(v["sec"] / 60)

    # --- Schlussphase
    last_start = duration - 600
    score_at = lambda sec: (sum(1 for g in goals if g[0] <= sec and g[1] == "wir"),  # noqa: E731
                            sum(1 for g in goals if g[0] <= sec and g[1] == "gegner"))
    b = score_at(last_start)
    final_goals = [g for g in goals if g[0] > last_start]
    scorers = {}
    for _, side, name, _ in final_goals:
        if side == "wir":
            scorers[name] = scorers.get(name, 0) + 1
    finale = {"from": last_start // 60, "score_before": f"{b[0]}:{b[1]}", "score_after": f"{us}:{them}",
              "us": sum(1 for g in final_goals if g[1] == "wir"), "them": sum(1 for g in final_goals if g[1] == "gegner"),
              "scorers": sorted(({"name": k, "goals": v} for k, v in scorers.items()), key=lambda x: -x["goals"]),
              "goals": [{"time": _mmss(t), "name": n, "seven": s7} for t, side, n, s7 in final_goals if side == "wir"]}

    # --- Fuehrung
    lead_changes, max_lead, max_back = 0, (0, 0), (0, 0)
    last_leader = None
    su = st = 0
    for t, side, _, _ in goals:
        if side == "wir":
            su += 1
        else:
            st += 1
        leader = "wir" if su > st else "gegner" if st > su else last_leader
        if leader and last_leader and leader != last_leader and su != st:
            lead_changes += 1
        if su != st:
            last_leader = leader
        if su - st > max_lead[0]:
            max_lead = (su - st, t)
        if st - su > max_back[0]:
            max_back = (st - su, t)

    # --- Timeouts (Wirkung: Tore in den 5 Minuten danach)
    tos = []
    for t, side in timeouts:
        after = [g for g in goals if t < g[0] <= t + 300]
        sc = score_at(t)
        tos.append({"time": _mmss(t), "side": side, "score": f"{sc[0]}:{sc[1]}",
                    "next5_us": sum(1 for g in after if g[1] == "wir"),
                    "next5_them": sum(1 for g in after if g[1] == "gegner")})

    return {
        "duration_min": duration // 60,
        "phases": phases,
        "runs": runs[:4],
        "situations": situ,
        "penalties_us": sum(1 for p in penalties if p[1] == "wir"),
        "penalties_them": sum(1 for p in penalties if p[1] == "gegner"),
        "finale": finale,
        "lead_changes": lead_changes,
        "max_lead": {"goals": max_lead[0], "time": _mmss(max_lead[1])} if max_lead[0] else None,
        "max_back": {"goals": max_back[0], "time": _mmss(max_back[1])} if max_back[0] else None,
        "timeouts": tos,
        "seven_us": sum(1 for g in goals if g[3] and g[1] == "wir"),
        "seven_them": sum(1 for g in goals if g[3] and g[1] == "gegner"),
    }
