"""
Löst einen blossen Hallennamen (z.B. "Muri Bachmatten") über die Google
Places Text Search API zu einer vollständigen Adresse auf, und erzeugt
Google-Maps-Routenlinks.

Ohne gesetzten GOOGLE_MAPS_API_KEY läuft der Import trotzdem durch: der
Maps-Link wird dann einfach mit dem rohen Hallennamen statt einer
aufgelösten Adresse gebaut (Google Maps kann damit meist trotzdem
navigieren, ist aber weniger zuverlässig als mit echter Adresse).
"""

from typing import Optional
from urllib.parse import quote

import httpx

from app.config import settings

PLACES_TEXT_SEARCH_URL = "https://maps.googleapis.com/maps/api/place/textsearch/json"


async def resolve_address(location_name: str) -> Optional[str]:
    """Gibt eine vollständige, formatierte Adresse zurück oder None bei Fehler/kein Key."""
    if not settings.google_maps_api_key or not location_name:
        return None

    params = {
        # "Schweiz" ergänzt, damit z.B. "Emmen Rossmoos" nicht irgendwo im
        # Ausland aufgelöst wird
        "query": f"{location_name}, Schweiz",
        "key": settings.google_maps_api_key,
    }
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(PLACES_TEXT_SEARCH_URL, params=params)
            resp.raise_for_status()
            data = resp.json()
    except httpx.HTTPError:
        return None

    results = data.get("results") or []
    if not results:
        return None
    return results[0].get("formatted_address")


def build_maps_link(destination: str) -> str:
    return f"https://www.google.com/maps/dir/?api=1&destination={quote(destination)}"


async def search_open_restaurants(cuisine_query: str, near: str, limit: int = 3) -> list[dict]:
    """
    Feature 5: sucht offene Restaurants passend zur gewaehlten Kategorie in
    der Naehe der Halle. `opennow=true` filtert auf den Moment der Anfrage -
    das passt hier gut, weil die Vorschlaege ohnehin erst sichtbar werden,
    nachdem das Spiel vorbei ist (also praktisch "jetzt gleich losgehen").

    Gibt eine leere Liste zurueck, wenn kein API-Key gesetzt ist oder die
    Anfrage fehlschlaegt - der Router zeigt dann einen Hinweis statt
    Vorschlaegen an.
    """
    if not settings.google_maps_api_key or not near:
        return []

    params = {
        "query": f"{cuisine_query} Restaurant",
        "location_bias": None,  # Text Search nutzt "region"/"query" statt location_bias
        "region": "ch",
        "opennow": "true",
        "key": settings.google_maps_api_key,
    }
    # Naehe ueber die Query statt eines separaten Feldes, da Text Search
    # keinen expliziten "near"-Parameter kennt
    params["query"] = f"{cuisine_query} Restaurant in der Naehe von {near}"
    params.pop("location_bias", None)

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(PLACES_TEXT_SEARCH_URL, params=params)
            resp.raise_for_status()
            data = resp.json()
    except httpx.HTTPError:
        return []

    results = (data.get("results") or [])[:limit]
    return [
        {"name": r.get("name", cuisine_query), "address": r.get("formatted_address")}
        for r in results
    ]
