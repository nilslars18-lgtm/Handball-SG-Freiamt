"""Erzeugt den vorbereiteten Spielbericht als Word-Datei (.docx).

Alles was die App schon weiss (Spiel, Datum, Halle, Resultat, Teamfoto,
Statistik, Schreiber) wird eingefuellt - der Spieler muss nur noch den Text
schreiben. Ohne Spiel entsteht die leere Vorlage fuer den Dokumente-Ordner.
"""
import io
import json
from typing import Optional

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.shared import Cm, Pt, RGBColor

from app import models
from app.config import settings

PLACEHOLDER = "[Hier den Spielbericht schreiben …]"
BLUE = RGBColor(0x1D, 0x4E, 0xD8)
GREY = RGBColor(0x6B, 0x72, 0x80)

STAT_COLS = [("name", "Spieler"), ("tore", "Tore"), ("siebenm", "7m"), ("zwei_min", "2'"), ("gelb", "Gelb")]


def _fmt_date(dt) -> str:
    # Spielzeiten liegen in der DB als Schweizer Lokalzeit (aus der ICS)
    wd = ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag"][dt.weekday()]
    return f"{wd}, {dt.strftime('%d.%m.%Y, %H:%M')} Uhr"


def _score_line(game: models.Game, r: Optional[models.GameReport]) -> str:
    team = settings.team_name
    if not r or r.score_home is None or r.score_away is None:
        left, right = (team, game.opponent) if game.is_home is not False else (game.opponent, team)
        return f"{left}  __ : __  {right}   (Halbzeit __ : __)"
    us, them = r.score_home, r.score_away
    hu, ht = r.half_home, r.half_away
    if game.is_home is False:
        line = f"{game.opponent}  {them} : {us}  {team}"
        half = f"{ht} : {hu}" if hu is not None and ht is not None else "__ : __"
    else:
        line = f"{team}  {us} : {them}  {game.opponent}"
        half = f"{hu} : {ht}" if hu is not None and ht is not None else "__ : __"
    return f"{line}   (Halbzeit {half})"


def _stats_rows(r: Optional[models.GameReport]) -> list:
    if not r or not r.stats_json:
        return []
    try:
        rows = json.loads(r.stats_json)
        return [row for row in rows if isinstance(row, dict) and row.get("name")]
    except Exception:
        return []


def build_report_docx(game: Optional[models.Game], report: Optional[models.GameReport],
                      logo: Optional[bytes] = None) -> bytes:
    doc = Document()
    sec = doc.sections[0]
    sec.left_margin = sec.right_margin = Cm(2.2)
    sec.top_margin = sec.bottom_margin = Cm(2)
    style = doc.styles["Normal"]
    style.font.name = "Calibri"
    style.font.size = Pt(11)

    p = doc.add_paragraph()
    if logo:
        try:
            p.add_run().add_picture(io.BytesIO(logo), height=Cm(1.6))
            p.add_run("   ")
        except Exception:
            pass
    run = p.add_run(f"{settings.team_name} · U19")
    run.font.size = Pt(10)
    run.font.color.rgb = GREY

    h = doc.add_heading("Spielbericht", level=0)
    for r_ in h.runs:
        r_.font.color.rgb = BLUE

    if game is None:
        doc.add_paragraph(f"{settings.team_name}  __ : __  Gegner   (Halbzeit __ : __)").runs[0].bold = True
        doc.add_paragraph("Datum / Zeit: ______________________")
        doc.add_paragraph("Halle: ______________________")
    else:
        sp = doc.add_paragraph()
        sr = sp.add_run(_score_line(game, report))
        sr.bold = True
        sr.font.size = Pt(14)
        info = doc.add_paragraph()
        info.add_run(("Heimspiel" if game.is_home else "Auswärtsspiel" if game.is_home is False else "Spiel") + " · ")
        info.add_run(_fmt_date(game.start_time))
        if game.location_name:
            info.add_run(f"\nHalle: {game.location_name}")

    # Teamfoto
    if report and report.team_photo:
        doc.add_heading("Teamfoto", level=2)
        doc.add_picture(io.BytesIO(report.team_photo), width=Cm(16))
        doc.paragraphs[-1].alignment = WD_ALIGN_PARAGRAPH.CENTER
    elif game is None:
        doc.add_heading("Teamfoto", level=2)
        doc.add_paragraph("[Teamfoto einfügen]").runs[0].font.color.rgb = GREY

    # Bericht
    doc.add_heading("Bericht", level=2)
    text = (report.text or "").strip() if report else ""
    if text:
        for para in text.split("\n"):
            doc.add_paragraph(para)
    else:
        ph = doc.add_paragraph(PLACEHOLDER)
        ph.runs[0].italic = True
        ph.runs[0].font.color.rgb = GREY
        for _ in range(3):
            doc.add_paragraph("")

    # Statistik
    rows = _stats_rows(report)
    if rows or (report and report.stats_image) or game is None:
        doc.add_heading("Statistik", level=2)
    if rows:
        cols = list(STAT_COLS)
        if any(r.get("wuerfe") for r in rows):
            cols.insert(2, ("wuerfe", "Tore/Würfe"))
        if any(r.get("paraden") for r in rows):
            cols.append(("paraden", "Paraden"))
        table = doc.add_table(rows=1, cols=len(cols))
        table.style = "Light Grid Accent 1"
        for i, (_, label) in enumerate(cols):
            table.rows[0].cells[i].text = label
        for row in rows:
            cells = table.add_row().cells
            for i, (key, _) in enumerate(cols):
                val = row.get(key)
                cells[i].text = "" if val in (None, "", 0) and key != "name" else str(val)
        total = sum(int(r.get("tore") or 0) for r in rows)
        doc.add_paragraph(f"Total Tore: {total}").runs[0].font.color.rgb = GREY
    if report and report.stats_image:
        doc.add_picture(io.BytesIO(report.stats_image), width=Cm(16))
    if game is None:
        table = doc.add_table(rows=6, cols=len(STAT_COLS))
        table.style = "Light Grid Accent 1"
        for i, (_, label) in enumerate(STAT_COLS):
            table.rows[0].cells[i].text = label

    doc.add_paragraph("")
    writer = report.writer.name if report and report.writer else "______________________"
    fp = doc.add_paragraph(f"Verfasst von: {writer}")
    fp.runs[0].font.color.rgb = GREY

    out = io.BytesIO()
    doc.save(out)
    return out.getvalue()
