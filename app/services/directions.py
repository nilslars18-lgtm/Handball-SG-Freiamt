"""
Feature 3: Anfahrts-Berechnung mit Live-Verkehrsdaten.

Die Google Directions API unterstuetzt fuer den Fahrmodus "driving" nur
`departure_time`, kein `arrival_time` (das gibt es nur fuer "transit").
Um trotzdem "Ankunft um X Uhr" zu berechnen, gehen wir zweistufig vor:

  1. Grobe Fahrzeit ohne Verkehrsmodell ermitteln (departure_time=jetzt)
  2. Damit einen Abfahrtszeitpunkt schaetzen (Zielankunft - grobe Fahrzeit)
  3. Mit genau diesem zukuenftigen departure_time nochmal abfragen -> die API
     liefert dann `duration_in_traffic`, die fuer genau diesen Zeitpunkt
     vorhergesagte (Google-)Verkehrslage
  4. Finalen Abfahrtszeitpunkt = Zielankunft - duration_in_traffic

Ohne gesetzten GOOGLE_MAPS_API_KEY liefert diese Funktion None, der Router
gibt dann eine verstaendliche Fehlermeldung zurueck statt eines Absturzes.
"""
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

import httpx

from app.config import settings

DIRECTIONS_URL = "https://maps.googleapis.com/maps/api/directions/json"


@dataclass
class DirectionsResult:
    duration_seconds: int
    duration_text: str
    distance_text: str


async def _query_directions(
    origin: str, destination: str, departure_epoch: int
) -> Optional[DirectionsResult]:
    params = {
        "origin": origin,
        "destination": destination,
        "mode": "driving",
        "departure_time": departure_epoch,
        "traffic_model": "best_guess",
        "key": settings.google_maps_api_key,
    }
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(DIRECTIONS_URL, params=params)
            resp.raise_for_status()
            data = resp.json()
    except httpx.HTTPError:
        return None

    if data.get("status") != "OK" or not data.get("routes"):
        return None

    leg = data["routes"][0]["legs"][0]
    duration = leg.get("duration_in_traffic") or leg["duration"]
    return DirectionsResult(
        duration_seconds=duration["value"],
        duration_text=duration["text"],
        distance_text=leg["distance"]["text"],
    )


async def compute_departure_time(
    origin: str, destination: str, arrival_target: datetime
) -> Optional[DirectionsResult]:
    """Gibt eine DirectionsResult mit der fuer den Zielzeitpunkt
    vorhergesagten Fahrzeit zurueck, oder None bei Fehler/fehlendem Key."""
    if not settings.google_maps_api_key or not origin or not destination:
        return None

    now_epoch = int(datetime.utcnow().timestamp())
    rough = await _query_directions(origin, destination, now_epoch)
    if rough is None:
        return None

    estimated_departure = arrival_target.timestamp() - rough.duration_seconds
    departure_epoch = max(int(estimated_departure), now_epoch)

    refined = await _query_directions(origin, destination, departure_epoch)
    return refined or rough
