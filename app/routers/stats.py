"""Saisonstatistik (aus den Spielberichten), Resultate/Bilanz, Trainings-Praesenz
und Excel-Export (Bussen, Praesenz, Statistik)."""
import io
import json
import re
from datetime import datetime
from typing import Optional
from urllib.parse import quote

from fastapi import APIRouter, Depends
from fastapi.responses import Response
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from sqlalchemy.orm import Session

from app import models
from app.database import get_db
from app.deps import get_current_user, require_admin, require_bussenchef, require_owner
from app.services.agenda import active_players, now_local, season_bounds, season_label, season_of

router = APIRouter(tags=["statistik"])

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _int(v) -> int:
    try:
        return int(v or 0)
    except (TypeError, ValueError):
        return 0


def _seven(v):
    """'2/3' -> (2, 3); '2' -> (2, None)"""
    if v in (None, ""):
        return 0, 0
    m = re.match(r"\s*(\d+)\s*(?:/\s*(\d+))?", str(v))
    if not m:
        return 0, 0
    goals = int(m.group(1))
    att = int(m.group(2)) if m.group(2) else goals
    return goals, att


def _split(v):
    """'5/7' -> (5, 7), sonst (None, None)"""
    m = re.match(r"\s*(\d+)\s*/\s*(\d+)", str(v or ""))
    return (int(m.group(1)), int(m.group(2))) if m else (None, None)


def compute_season(db: Session, season: int) -> dict:
    start, end = season_bounds(season)
    now = now_local()
    games = (
        db.query(models.Game)
        .filter(models.Game.start_time >= start, models.Game.start_time < end)
        .order_by(models.Game.start_time)
        .all()
    )
    reports = {r.game_id: r for r in db.query(models.GameReport).all()}

    results, bilanz = [], {"spiele": 0, "siege": 0, "unentschieden": 0, "niederlagen": 0, "tore": 0, "gegentore": 0}
    players, goalies = {}, {}
    team = {"tore": 0, "wurf_tore": 0, "wuerfe": 0, "siebenm_tore": 0, "siebenm_versuche": 0,
            "zwei_min": 0, "gelb": 0, "rot": 0, "paraden": 0, "schuesse_aufs_tor": 0, "tf": 0, "spiele_mit_stats": 0}
    for g in games:
        r = reports.get(g.id)
        res = None
        if r and r.score_home is not None and r.score_away is not None:
            us, them = r.score_home, r.score_away
            outcome = "S" if us > them else "N" if us < them else "U"
            bilanz["spiele"] += 1
            bilanz["tore"] += us
            bilanz["gegentore"] += them
            bilanz[{"S": "siege", "U": "unentschieden", "N": "niederlagen"}[outcome]] += 1
            res = {"us": us, "them": them, "outcome": outcome}
            results.append({"game_id": g.id, "start_time": g.start_time, "opponent": g.opponent,
                            "is_home": g.is_home, "us": us, "them": them, "outcome": outcome,
                            "half_us": r.half_home, "half_them": r.half_away})
        if not (r and r.stats_json):
            continue
        try:
            rows = json.loads(r.stats_json)
        except Exception:
            rows = []
        game_info = {"game_id": g.id, "start_time": g.start_time, "opponent": g.opponent,
                     "is_home": g.is_home, "result": res}
        if rows:
            team["spiele_mit_stats"] += 1
        for row in rows:
            name = (row.get("name") or "").strip()
            if not name:
                continue
            key = name.lower()
            tore = _int(row.get("tore"))
            wt, wv = _split(row.get("wuerfe"))
            if wv is None:          # manuell erfasst: nur Tore bekannt
                wt, wv = tore, None
            st, sa = _seven(row.get("siebenm"))
            z2, ge, ro = _int(row.get("zwei_min")), _int(row.get("gelb")), _int(row.get("rot"))
            tf = _int(row.get("tf"))
            ps, pv = _split(row.get("paraden"))
            is_goalie = ps is not None

            team["tore"] += tore
            if wv is not None:
                team["wurf_tore"] += wt
                team["wuerfe"] += wv
            team["siebenm_tore"] += st
            team["siebenm_versuche"] += sa
            team["zwei_min"] += z2
            team["gelb"] += ge
            team["rot"] += ro
            team["tf"] += tf

            if is_goalie:
                team["paraden"] += ps
                team["schuesse_aufs_tor"] += pv
                gk = goalies.setdefault(key, {"name": name, "spiele": 0, "paraden": 0, "schuesse": 0, "games": []})
                gk["spiele"] += 1
                gk["paraden"] += ps
                gk["schuesse"] += pv
                gk["games"].append(dict(game_info, paraden=ps, schuesse=pv,
                                        quote=round(100 * ps / pv) if pv else 0,
                                        siebenm_paraden=row.get("paraden7m") or "", tore=tore,
                                        zwei_min=z2, gelb=ge, rot=ro))

            if is_goalie and not tore and not z2 and not ge and not ro and not tf:
                continue  # reine Torhueter-Zeile: nicht in der Feldspieler-Liste
            p = players.setdefault(key, {"name": name, "spiele": 0, "tore": 0, "wurf_tore": 0, "wuerfe": 0,
                                         "siebenm_tore": 0, "siebenm_versuche": 0, "zwei_min": 0, "gelb": 0,
                                         "rot": 0, "tf": 0, "is_goalie": False, "games": []})
            p["is_goalie"] = p["is_goalie"] or is_goalie
            p["spiele"] += 1
            p["tore"] += tore
            if wv is not None:
                p["wurf_tore"] += wt
                p["wuerfe"] += wv
            p["siebenm_tore"] += st
            p["siebenm_versuche"] += sa
            p["zwei_min"] += z2
            p["gelb"] += ge
            p["rot"] += ro
            p["tf"] += tf
            p["games"].append(dict(game_info, tore=tore, tf=tf, wuerfe=wv, wurf_tore=wt if wv is not None else None,
                                   quote=round(100 * wt / wv) if wv else None,
                                   siebenm=row.get("siebenm") or "", zwei_min=z2, gelb=ge, rot=ro))

    player_list = sorted(players.values(), key=lambda x: (-x["tore"], x["name"].lower()))
    for p in player_list:
        p["schnitt"] = round(p["tore"] / p["spiele"], 1) if p["spiele"] else 0
        p["quote"] = round(100 * p["wurf_tore"] / p["wuerfe"]) if p["wuerfe"] else None
        p["tf_schnitt"] = round(p["tf"] / p["spiele"], 1) if p["spiele"] else 0
        p["games"].sort(key=lambda x: x["start_time"], reverse=True)
    goalie_list = sorted(goalies.values(), key=lambda x: (-x["paraden"], x["name"].lower()))
    for gk in goalie_list:
        gk["quote"] = round(100 * gk["paraden"] / gk["schuesse"]) if gk["schuesse"] else 0
        gk["games"].sort(key=lambda x: x["start_time"], reverse=True)
    penalties = sorted([p for p in player_list if p["zwei_min"] or p["gelb"] or p["rot"]],
                       key=lambda x: (-x["zwei_min"], -x["rot"], -x["gelb"], x["name"].lower()))
    team["wurfquote"] = round(100 * team["wurf_tore"] / team["wuerfe"]) if team["wuerfe"] else None
    n = team["spiele_mit_stats"]
    team["tf_schnitt"] = round(team["tf"] / n, 1) if n else None
    bilanz["tore_schnitt"] = round(bilanz["tore"] / bilanz["spiele"], 1) if bilanz["spiele"] else None
    bilanz["gegentore_schnitt"] = round(bilanz["gegentore"] / bilanz["spiele"], 1) if bilanz["spiele"] else None
    team["torhueterquote"] = round(100 * team["paraden"] / team["schuesse_aufs_tor"]) if team["schuesse_aufs_tor"] else None

    # Trainings-Praesenz (nur vergangene, nicht abgesagte Trainings)
    trainings = (
        db.query(models.TeamEvent)
        .filter(models.TeamEvent.kind == "training", models.TeamEvent.is_cancelled == False,  # noqa: E712
                models.TeamEvent.start_time >= start, models.TeamEvent.start_time < min(end, now))
        .order_by(models.TeamEvent.start_time)
        .all()
    )
    presence = []
    for p in active_players(db):
        z = a = v = ind = keine = da = 0
        for t in trainings:
            r = next((x for x in t.responses if x.user_id == p.id), None)
            status = r.status if r else None
            if status == "zusage":
                z += 1
            elif status == "absage":
                a += 1
            elif status == "verletzt":
                v += 1
            elif status == "individuell":
                ind += 1
            else:
                keine += 1
            attended = r.present if (r and r.present is not None) else (status == "zusage")
            da += 1 if attended else 0
        total = len(trainings)
        presence.append({"name": p.name, "trainings": total, "zusagen": z, "absagen": a, "verletzt": v,
                         "individuell": ind, "keine_antwort": keine,
                         "anwesend": da, "quote": round(100 * da / total) if total else 0})
    presence.sort(key=lambda x: (-x["quote"], x["name"].lower()))

    return {"season": season, "season_label": season_label(season), "bilanz": bilanz, "results": results,
            "team": team, "players": player_list, "goalies": goalie_list, "penalties": penalties,
            "presence": presence, "trainings_count": len(trainings)}


@router.get("/stats/seasons")
def seasons(db: Session = Depends(get_db), user=Depends(get_current_user)):
    found = {season_of(now_local())}
    for (dt,) in db.query(models.Game.start_time).all():
        found.add(season_of(dt))
    return [{"season": s, "label": season_label(s)} for s in sorted(found, reverse=True)]


@router.get("/stats/season")
def season_stats(season: Optional[int] = None, db: Session = Depends(get_db), user=Depends(get_current_user)):
    return compute_season(db, season or season_of(now_local()))


# ---------------------------------------------------------------------------
# Excel-Export
# ---------------------------------------------------------------------------
HEAD_FILL = PatternFill("solid", fgColor="1D4ED8")
HEAD_FONT = Font(bold=True, color="FFFFFF")


def _sheet(ws, title: str, headers: list, rows: list, widths: Optional[list] = None):
    ws.title = title[:31]
    ws.append(headers)
    for c in ws[1]:
        c.fill, c.font = HEAD_FILL, HEAD_FONT
        c.alignment = Alignment(vertical="center")
    for r in rows:
        ws.append(r)
    ws.freeze_panes = "A2"
    for i, h in enumerate(headers, start=1):
        w = (widths[i - 1] if widths else None) or max(10, min(40, max(len(str(h)), *(len(str(r[i - 1] or "")) for r in rows)) + 2 if rows else len(str(h)) + 2))
        ws.column_dimensions[get_column_letter(i)].width = w


def _xlsx_response(wb: Workbook, filename: str) -> Response:
    out = io.BytesIO()
    wb.save(out)
    return Response(content=out.getvalue(), media_type=XLSX,
                    headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"})


@router.get("/export/bussen.xlsx")
def export_fines(db: Session = Depends(get_db), user=Depends(require_bussenchef)):
    wb = Workbook()
    fines = db.query(models.Fine).order_by(models.Fine.date).all()
    _sheet(wb.active, "Alle Bussen", ["Datum", "Spieler", "Grund", "Betrag CHF", "Status"],
           [[f.date.strftime("%d.%m.%Y"), f.player.name if f.player else "", f.reason, f.amount, f.status.value]
            for f in fines])
    per = {}
    for f in fines:
        name = f.player.name if f.player else "?"
        d = per.setdefault(name, [0.0, 0.0])
        d[0 if f.status == models.BussenStatus.OFFEN else 1] += f.amount
    _sheet(wb.create_sheet(), "Pro Spieler", ["Spieler", "Offen CHF", "Bezahlt CHF", "Total CHF"],
           [[n, o, b, o + b] for n, (o, b) in sorted(per.items())])
    harz = db.query(models.HarzExpense).order_by(models.HarzExpense.date).all()
    _sheet(wb.create_sheet(), "Harz-Ausgaben", ["Datum", "Betrag CHF", "Notiz", "Von"],
           [[h.date.strftime("%d.%m.%Y"), h.amount, h.note or "", h.created_by.name if h.created_by else ""] for h in harz])
    paid = sum(f.amount for f in fines if f.status == models.BussenStatus.BEZAHLT)
    _sheet(wb.create_sheet(), "Kasse", ["Posten", "CHF"],
           [["Bussen bezahlt", paid], ["Bussen offen", sum(f.amount for f in fines if f.status == models.BussenStatus.OFFEN)],
            ["Harz-Ausgaben", sum(h.amount for h in harz)], ["Kassenstand", paid - sum(h.amount for h in harz)]])
    return _xlsx_response(wb, f"Bussenliste_{datetime.now().strftime('%Y-%m-%d')}.xlsx")


@router.get("/export/praesenz.xlsx")
def export_presence(season: Optional[int] = None, db: Session = Depends(get_db), admin=Depends(require_admin)):
    season = season or season_of(now_local())
    data = compute_season(db, season)
    wb = Workbook()
    _sheet(wb.active, "Übersicht", ["Spieler", "Trainings", "Anwesend", "Quote %", "Zusagen", "Absagen", "Verletzt", "Individuell", "Keine Antwort"],
           [[p["name"], p["trainings"], p["anwesend"], p["quote"], p["zusagen"], p["absagen"], p["verletzt"], p["individuell"], p["keine_antwort"]]
            for p in data["presence"]])
    # Matrix: jedes Training eine Spalte
    start, end = season_bounds(season)
    trainings = (
        db.query(models.TeamEvent)
        .filter(models.TeamEvent.kind == "training", models.TeamEvent.is_cancelled == False,  # noqa: E712
                models.TeamEvent.start_time >= start, models.TeamEvent.start_time < min(end, now_local()))
        .order_by(models.TeamEvent.start_time).all()
    )
    rows = []
    for p in active_players(db):
        row = [p.name]
        for t in trainings:
            r = next((x for x in t.responses if x.user_id == p.id), None)
            if r and r.present is not None:
                row.append("✔" if r.present else "✘")
            elif r and r.status == "zusage":
                row.append("zugesagt")
            elif r and r.status in ("absage", "verletzt", "individuell"):
                row.append({"absage": "abgesagt", "verletzt": "verletzt", "individuell": "individuell"}[r.status])
            else:
                row.append("")
        rows.append(row)
    _sheet(wb.create_sheet(), "Pro Training",
           ["Spieler"] + [t.start_time.strftime("%d.%m.") for t in trainings], rows,
           [18] + [10] * len(trainings))
    return _xlsx_response(wb, f"Praesenz_{season_label(season).replace('/', '-')}.xlsx")


@router.get("/export/statistik.xlsx")
def export_stats(season: Optional[int] = None, db: Session = Depends(get_db), admin=Depends(require_admin)):
    season = season or season_of(now_local())
    data = compute_season(db, season)
    wb = Workbook()
    _sheet(wb.active, "Spieler", ["Spieler", "Spiele", "Tore", "Ø Tore", "Würfe", "Wurfquote %", "7m Tore", "7m Versuche", "Techn. Fehler", "Ø TF", "2 Min", "Verwarnungen", "Rot"],
           [[p["name"], p["spiele"], p["tore"], p["schnitt"], p["wuerfe"] or "", p["quote"] if p["quote"] is not None else "",
             p["siebenm_tore"], p["siebenm_versuche"], p["tf"], p["tf_schnitt"], p["zwei_min"], p["gelb"], p["rot"]]
            for p in data["players"]])
    _sheet(wb.create_sheet(), "Torhüter", ["Torhüter", "Spiele", "Paraden", "Schüsse", "Quote %"],
           [[g["name"], g["spiele"], g["paraden"], g["schuesse"], g["quote"]] for g in data["goalies"]])
    _sheet(wb.create_sheet(), "Resultate", ["Datum", "Gegner", "Heim/Auswärts", "Tore", "Gegentore", "Halbzeit", "Ergebnis"],
           [[r["start_time"].strftime("%d.%m.%Y"), r["opponent"], "Heim" if r["is_home"] else "Auswärts",
             r["us"], r["them"],
             f'{r["half_us"]}:{r["half_them"]}' if r["half_us"] is not None and r["half_them"] is not None else "",
             {"S": "Sieg", "U": "Unentschieden", "N": "Niederlage"}[r["outcome"]]] for r in data["results"]])
    b = data["bilanz"]
    _sheet(wb.create_sheet(), "Bilanz", ["", "Wert"],
           [["Spiele", b["spiele"]], ["Siege", b["siege"]], ["Unentschieden", b["unentschieden"]],
            ["Niederlagen", b["niederlagen"]], ["Tore", b["tore"]], ["Gegentore", b["gegentore"]],
            ["Tordifferenz", b["tore"] - b["gegentore"]]])
    return _xlsx_response(wb, f"Statistik_{season_label(season).replace('/', '-')}.xlsx")


@router.get("/export/logins.xlsx")
def export_logins(db: Session = Depends(get_db), admin=Depends(require_owner)):
    """Alle Logins mit dem aktuellen Passwort. Wird bei jedem Download neu aus der
    Datenbank erstellt - ist also immer aktuell, auch wenn jemand sein Passwort aendert."""
    users = (
        db.query(models.User).filter(models.User.is_active == True)  # noqa: E712
        .order_by(models.User.base_role, models.User.name).all()
    )
    rows = []
    for u in users:
        roles = ["Admin"] if u.base_role == models.BaseRole.ADMIN else [u.staff_title] if u.staff_title else ["Spieler"]
        if u.is_captain:
            roles.append("Captain")
        if u.is_bussenchef:
            roles.append("Bussenchef")
        if u.is_harzchef:
            roles.append("Harzchef")
        rows.append([u.name, u.email, u.last_plaintext_password or "(selbst geändert – bei Bedarf neu setzen)", ", ".join(roles)])
    wb = Workbook()
    _sheet(wb.active, "Logins", ["Name", "Benutzer (Login)", "Passwort", "Rolle"], rows, [26, 22, 26, 30])
    ws = wb.active
    ws.append([])
    ws.append([f"Stand: {now_local().strftime('%d.%m.%Y %H:%M')} – App: https://handball-h3dt.onrender.com"])
    return _xlsx_response(wb, f"Logins_{datetime.now().strftime('%Y-%m-%d')}.xlsx")
