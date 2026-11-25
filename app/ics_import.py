"""
Parst .ics-Dateien wie sie von handball.ch (Matchcenter) exportiert werden.

Beobachtetes Format pro VEVENT:
  UID:         "<id>@www.handball.ch"                     -> stabile ID für Dedup
  DTSTART:     naives lokales Datum/Zeit (kein TZID, kein Z) -> Europe/Zurich Wandzeit
  SUMMARY:     "<Liga-Kürzel> - <Team A> - <Team B>"        -> eines der beiden ist unser Team
  LOCATION:    nur Hallenname, KEINE vollständige Adresse   -> muss extern aufgelöst werden
  DESCRIPTION: nur eine interne Matchcenter-ID, nicht relevant
"""

from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional

from icalendar import Calendar

from app.config import settings


@dataclass
class ParsedGame:
    ics_uid: Optional[str]
    opponent: str
    is_home: Optional[bool]
    start_time: datetime
    end_time: Optional[datetime]
    location_name: Optional[str]
    raw_summary: str


def _split_teams(summary: str) -> List[str]:
    # Format ist üblicherweise "<Liga> - <Team A> - <Team B>", manchmal mit
    # doppelten Leerzeichen (z.B. "Handball Emmen  - SG Freiamt PLUS")
    parts = [p.strip() for p in summary.split(" - ")]
    return [p for p in parts if p]  # leere Teile (durch doppelte Trenner) verwerfen


def _match_team(name: str) -> bool:
    return name.strip().casefold() == settings.team_name.strip().casefold()


def parse_summary(summary: str) -> tuple[str, Optional[bool]]:
    """
    Gibt (opponent, is_home) zurück.
    is_home ist None, wenn unser Teamname im Titel nicht gefunden wurde
    (z.B. bei abweichender Schreibweise) - dann bekommt opponent den vollen
    Titel, damit nichts verloren geht und der Admin es manuell nachbessern kann.
    """
    parts = _split_teams(summary)

    # Erwartet: [Liga, TeamA, TeamB]. Falls kein Liga-Kürzel vorhanden: [TeamA, TeamB]
    candidates = parts[-2:] if len(parts) >= 2 else parts

    if len(candidates) == 2:
        team_a, team_b = candidates
        if _match_team(team_a):
            return team_b, True
        if _match_team(team_b):
            return team_a, False

    # Team nicht erkannt -> voller Titel als Fallback, is_home unbekannt
    return summary.strip(), None


def parse_ics_bytes(ics_bytes: bytes) -> List[ParsedGame]:
    calendar = Calendar.from_ical(ics_bytes)
    games: List[ParsedGame] = []

    for component in calendar.walk("VEVENT"):
        summary = str(component.get("SUMMARY", "")).strip()
        dtstart = component.get("DTSTART").dt
        if not isinstance(dtstart, datetime):
            # Ganztägiger Termin ohne Uhrzeit - überspringen, kommt bei
            # Spielplänen praktisch nicht vor, aber sicher ist sicher
            continue

        uid_raw = component.get("UID")
        uid = str(uid_raw).strip() if uid_raw else None

        dtend_raw = component.get("DTEND")
        dtend = dtend_raw.dt if dtend_raw else None
        end_time = dtend if isinstance(dtend, datetime) else None

        location = component.get("LOCATION")
        location_name = str(location).strip() if location else None

        opponent, is_home = parse_summary(summary)

        games.append(
            ParsedGame(
                ics_uid=uid,
                opponent=opponent,
                is_home=is_home,
                start_time=dtstart,
                end_time=end_time,
                location_name=location_name,
                raw_summary=summary,
            )
        )

    return games
